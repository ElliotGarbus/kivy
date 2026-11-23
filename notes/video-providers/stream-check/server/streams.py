"""Test streams for stream-check, run on the Raspberry Pi.

    python3 streams.py CLIP

CLIP is any video file; it is re-encoded once into ``work/src.mp4`` (H.264
720p, 2 s keyframes, AAC) and everything is cut from that.

Serves:

- RTSP on 8554 (gst-rtsp-server, live test pattern, shared per path):
  ``/h264`` H.264 720p with G.711 A-law audio, as many cameras send;
  ``/h265`` H.265 540p, no audio.
- HTTP on 8080 (``work/www``): ``/live-ts/index.m3u8`` and
  ``/live-fmp4/index.m3u8`` (live HLS, ffmpeg looping the clip),
  ``/vod/index.m3u8`` (recorded HLS), ``/dash/manifest.mpd`` (recorded DASH).
- MJPEG on 8090: ``/cam.mjpg``, multipart/x-mixed-replace like an IP webcam.
- Control on 8099: ``/drop?name=rtsp`` closes every RTSP session and removes
  ``/h264`` until ``/restore?name=rtsp``; ``name=hls`` stops live-ts and
  deletes it until restored.

Needs ffmpeg, gstreamer1.0-rtsp and gir1.2-gst-rtsp-server-1.0.
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
WWW = os.path.join(WORK, "www")
SRC = os.path.join(WORK, "src.mp4")
# The clip is ~7.6 s; recorded HLS and DASH loop it to ~45 s.
VOD_LOOPS = 5

RTSP_LAUNCH = {
    "/h264": (
        "( videotestsrc is-live=true pattern=ball "
        "! video/x-raw,format=I420,width=1280,height=720,framerate=30/1 "
        "! x264enc tune=zerolatency speed-preset=ultrafast key-int-max=60 "
        "bitrate=3000 ! h264parse ! rtph264pay name=pay0 pt=96 config-interval=1 "
        "audiotestsrc is-live=true wave=ticks ! audioconvert ! audioresample "
        "! audio/x-raw,rate=8000,channels=1 ! alawenc ! rtppcmapay name=pay1 pt=8 )"),
    "/h265": (
        "( videotestsrc is-live=true pattern=smpte "
        "! video/x-raw,format=I420,width=960,height=540,framerate=30/1 "
        "! x265enc tune=zerolatency speed-preset=ultrafast key-int-max=60 "
        "bitrate=2000 ! h265parse ! rtph265pay name=pay0 pt=96 config-interval=1 )"),
}

PROCS = {}
LOCK = threading.Lock()


def log(*parts):
    print("[streams]", *parts, flush=True)


def ffmpeg(*args, **kwargs):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(args)
    return subprocess.Popen(cmd, **kwargs)


def prepare(clip):
    os.makedirs(WWW, exist_ok=True)
    if not os.path.exists(SRC):
        log("encoding", clip, "->", SRC)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", clip,
             "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "fast",
             "-profile:v", "main", "-g", "60", "-keyint_min", "60",
             "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", SRC], check=True)
    vod = os.path.join(WWW, "vod")
    if not os.path.exists(os.path.join(vod, ".loops{}".format(VOD_LOOPS))):
        shutil.rmtree(vod, ignore_errors=True)
        os.makedirs(vod, exist_ok=True)
        log("making recorded HLS")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-stream_loop", str(VOD_LOOPS), "-i", SRC,
             "-c", "copy", "-f", "hls", "-hls_time", "2",
             "-hls_playlist_type", "vod",
             os.path.join(vod, "index.m3u8")], check=True)
        open(os.path.join(vod, ".loops{}".format(VOD_LOOPS)), "w").close()
    dash = os.path.join(WWW, "dash")
    if not os.path.exists(os.path.join(dash, ".loops{}".format(VOD_LOOPS))):
        shutil.rmtree(dash, ignore_errors=True)
        os.makedirs(dash, exist_ok=True)
        log("making recorded DASH")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-stream_loop", str(VOD_LOOPS), "-i", SRC,
             "-c", "copy", "-f", "dash", "-seg_duration", "2",
             os.path.join(dash, "manifest.mpd")], check=True)
        open(os.path.join(dash, ".loops{}".format(VOD_LOOPS)), "w").close()


def start_live_hls(name, segment_type):
    folder = os.path.join(WWW, name)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    args = ["-re", "-stream_loop", "-1", "-i", SRC, "-c", "copy",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+omit_endlist",
            "-hls_segment_type", segment_type]
    if segment_type == "fmp4":
        args += ["-hls_fmp4_init_filename", "init.mp4"]
    args.append(os.path.join(folder, "index.m3u8"))
    PROCS[name] = ffmpeg(*args)
    log("live HLS", name, "started")


def stop_live_hls(name):
    proc = PROCS.pop(name, None)
    if proc:
        proc.terminate()
        proc.wait()
    shutil.rmtree(os.path.join(WWW, name), ignore_errors=True)
    log("live HLS", name, "stopped")


# ---------- MJPEG: ffmpeg writes JPEGs to a pipe; every client gets the latest.


class Mjpeg:
    def __init__(self):
        self.frame = None
        self.cond = threading.Condition()
        self.proc = ffmpeg(
            "-re", "-stream_loop", "-1", "-i", SRC, "-an",
            "-vf", "scale=640:-2,fps=15", "-c:v", "mjpeg", "-q:v", "5",
            "-f", "mjpeg", "pipe:1", stdout=subprocess.PIPE)
        PROCS["mjpeg"] = self.proc
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        buffer = b""
        while True:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                return
            buffer += chunk
            while True:
                end = buffer.find(b"\xff\xd9")
                if end < 0:
                    break
                start = buffer.find(b"\xff\xd8")
                frame, buffer = buffer[start:end + 2], buffer[end + 2:]
                with self.cond:
                    self.frame = frame
                    self.cond.notify_all()


class MjpegHandler(SimpleHTTPRequestHandler):
    mjpeg = None

    def do_GET(self):
        if self.path.split("?")[0] != "/cam.mjpg":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                with self.mjpeg.cond:
                    self.mjpeg.cond.wait(timeout=5)
                    frame = self.mjpeg.frame
                if frame is None:
                    continue
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() +
                    b"\r\n\r\n" + frame + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


# ---------- Static HTTP for HLS and DASH.


class StaticHandler(SimpleHTTPRequestHandler):
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **{
        ".m3u8": "application/vnd.apple.mpegurl",
        ".mpd": "application/dash+xml",
        ".ts": "video/mp2t",
        ".m4s": "video/iso.segment",
        ".mp4": "video/mp4",
    })

    def end_headers(self):
        if self.path.endswith((".m3u8", ".mpd")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ("200", "206", "304"):
            log("http", fmt % args)


# ---------- RTSP.


class Rtsp:
    def __init__(self):
        self.server = GstRtspServer.RTSPServer()
        self.server.set_service("8554")
        self.mounts = self.server.get_mount_points()
        self.factories = {}
        for path, launch in RTSP_LAUNCH.items():
            factory = GstRtspServer.RTSPMediaFactory()
            factory.set_launch(launch)
            # The default protocols offer UDP and TCP; the client picks.
            factory.set_shared(True)
            self.factories[path] = factory
            self.mounts.add_factory(path, factory)
        self.server.attach(None)
        log("RTSP on 8554:", ", ".join(RTSP_LAUNCH))

    def drop(self):
        self.mounts.remove_factory("/h264")
        self.server.client_filter(
            lambda server, client, data=None:
            GstRtspServer.RTSPFilterResult.REMOVE)
        log("RTSP dropped: sessions closed, /h264 removed")
        return False

    def restore(self):
        self.mounts.add_factory("/h264", self.factories["/h264"])
        log("RTSP restored")
        return False


class ControlHandler(SimpleHTTPRequestHandler):
    rtsp = None

    def do_GET(self):
        url = urlparse(self.path)
        name = parse_qs(url.query).get("name", [""])[0]
        action = url.path.strip("/")
        with LOCK:
            if name == "rtsp" and action == "drop":
                GLib.idle_add(self.rtsp.drop)
            elif name == "rtsp" and action == "restore":
                GLib.idle_add(self.rtsp.restore)
            elif name == "hls" and action == "drop":
                stop_live_hls("live-ts")
            elif name == "hls" and action == "restore":
                start_live_hls("live-ts", "mpegts")
            else:
                self.send_error(400, "unknown action or name")
                return
        body = "{} {}\n".format(action, name).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(port, handler):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    Gst.init(None)
    loop = GLib.MainLoop()

    def stop(*args):
        for proc in list(PROCS.values()):
            proc.terminate()
        loop.quit()
        return False

    # GLib's own handlers: the loop sits in C, where Python's never run.
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, stop)
    try:
        prepare(sys.argv[1])
        start_live_hls("live-ts", "mpegts")
        start_live_hls("live-fmp4", "fmp4")
        MjpegHandler.mjpeg = Mjpeg()
        ControlHandler.rtsp = Rtsp()
        serve(8080, partial(StaticHandler, directory=WWW))
        serve(8090, MjpegHandler)
        serve(8099, ControlHandler)
        log("HTTP on 8080, MJPEG on 8090, control on 8099. Ctrl-C stops.")
        loop.run()
    finally:
        stop()


if __name__ == "__main__":
    main()
