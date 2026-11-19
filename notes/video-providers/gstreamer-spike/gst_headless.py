"""Headless GStreamer probe for the Kivy 3.0 provider spike.

Answers, without a window:

* playbin vs playbin3: which decoder/demuxer gets autoplugged, time to the
  first frame, message sequence.
* Bus polling: everything is driven from ONE Python thread that wakes at a
  fixed tick (default 60 Hz, like Kivy's Clock), pops every pending bus
  message with ``Gst.Bus.pop()`` and pulls the newest frame with
  ``appsink.try_pull_sample(0)``. No GLib main loop, no Python callbacks on
  GStreamer's streaming threads.
* Error reporting: what ERROR / WARNING text arrives for missing files,
  junk files, HTTP 404, DNS failure, HLS/DASH, and a server that dies
  mid-stream (``--case hls-server-dies``).
* Cost of the CPU path: per-frame map+copy time for RGBA vs NV12.

Usage::

    python gst_headless.py --list
    python gst_headless.py --case local-1080p --bin playbin3 --format RGBA
    python gst_headless.py --all --json results/headless-windows.json
"""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstApp, GstVideo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, 'clips')


def clip_uri(name):
    return Gst.filename_to_uri(os.path.join(CLIPS, name))


# name -> (uri or callable returning uri, seconds to run)
CASES = {
    'local-360p': (lambda: clip_uri('big_buck_bunny_360_10s.mp4'), 12),
    'local-1080p': (lambda: clip_uri('test_1080p30.mp4'), 8),
    'local-2160p': (lambda: clip_uri('test_2160p30.mp4'), 8),
    'local-vp9': (lambda: clip_uri('vp9_1080p.webm'), 6),
    'local-hevc-1080p': (lambda: clip_uri('hevc_1080p30.mp4'), 8),
    'local-hevc-2160p': (lambda: clip_uri('hevc_2160p30.mp4'), 8),
    'missing-file': (lambda: clip_uri('does_not_exist.mp4'), 4),
    'not-a-video': (lambda: clip_uri('not_a_video.mp4'), 4),
    'corrupt': (lambda: clip_uri('corrupt.mp4'), 6),
    'http-404': ('https://gstreamer.freedesktop.org/data/does-not-exist.mp4', 8),
    'dns-fail': ('https://no-such-host.invalid/video.mp4', 8),
    'hls-ts': ('https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8', 15),
    'hls-fmp4': ('https://devstreaming-cdn.apple.com/videos/streaming/examples/'
                 'img_bipbop_adv_example_fmp4/master.m3u8', 15),
    'dash': ('https://dash.akamaized.net/akamai/bbb_30fps/bbb_30fps.mpd', 15),
    'hls-404': ('https://test-streams.mux.dev/no-such-stream/master.m3u8', 8),
    'hls-server-dies': ('local-hls', 25),
}


class LocalHLS:
    """Package test_1080p30.mp4 as HLS (fMP4/CMAF if available, else TS),
    serve it over http.server, and kill the server on request."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='gst-hls-')
        self.proc = None
        self.port = None

    def build(self):
        src = os.path.join(CLIPS, 'test_1080p30.mp4').replace(os.sep, '/')
        d = self.dir.replace(os.sep, '/')
        if Gst.ElementFactory.find('hlscmafsink'):
            sink = (f'hlscmafsink target-duration=2 playlist-type=vod '
                    f'playlist-location="{d}/index.m3u8" '
                    f'location="{d}/seg%05d.m4s" init-location="{d}/init%05d.mp4"')
        else:
            sink = (f'hlssink2 target-duration=2 playlist-length=0 max-files=0 '
                    f'playlist-location="{d}/index.m3u8" location="{d}/seg%05d.ts"')
        # hlscmafsink (gst-plugins-rs) wants avc; hlssink2 wants byte-stream.
        fmt = 'avc' if 'hlscmafsink' in sink else 'byte-stream'
        desc = (f'filesrc location="{src}" ! qtdemux ! h264parse ! '
                f'video/x-h264,stream-format={fmt} ! {sink}')
        p = Gst.parse_launch(desc)
        p.set_state(Gst.State.PLAYING)
        msg = p.get_bus().timed_pop_filtered(
            60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        p.set_state(Gst.State.NULL)
        if msg is None or msg.type == Gst.MessageType.ERROR:
            raise RuntimeError('could not package HLS: %s' % (
                msg.parse_error()[0].message if msg else 'timeout'))
        return sorted(os.listdir(self.dir))

    def start(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        self.port = s.getsockname()[1]
        s.close()
        # ~1.4x the clip's real bitrate (5.5 MB over 20 s): playback stays only a little ahead.
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'slow_http.py'),
             str(self.port), self.dir, '400000'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        return f'http://127.0.0.1:{self.port}/index.m3u8'

    def kill(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def cleanup(self):
        self.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


class Probe:
    """A provider-shaped object: load() / play() / tick() / unload()."""

    def __init__(self, uri, binname='playbin3', fmt='RGBA', audio=True):
        self.uri = uri
        self.binname = binname
        self.fmt = fmt
        self.audio = audio
        self.events = []
        self.t0 = None
        self.frames = 0
        self.first_frame_s = None
        self.copy_ms = []
        self.size = None
        self.error = None
        self.eos = False
        self.buffering = []
        self.elements = []
        self.pipeline = None

    def log(self, what, **kw):
        t = round(time.perf_counter() - self.t0, 3)
        self.events.append(dict(t=t, what=what, **kw))

    def load(self):
        self.t0 = time.perf_counter()
        self.pipeline = Gst.ElementFactory.make(self.binname, None)
        if self.pipeline is None:
            raise RuntimeError(f'no {self.binname}')
        sink = Gst.ElementFactory.make('appsink', 'videosink')
        sink.set_property('caps', Gst.Caps.from_string(
            f'video/x-raw,format={self.fmt}'))
        sink.set_property('max-buffers', 1)
        sink.set_property('drop', True)
        sink.set_property('sync', True)
        sink.set_property('emit-signals', False)
        sink.set_property('enable-last-sample', False)
        self.sink = sink
        self.pipeline.set_property('video-sink', sink)
        if not self.audio:
            self.pipeline.set_property(
                'audio-sink', Gst.ElementFactory.make('fakesink', None))
        self.pipeline.set_property('uri', self.uri)
        # Record which elements get autoplugged (decoder, demuxer). This is
        # a GObject signal emitted on streaming threads; it only appends
        # to a list, so it's cheap, but it does take the GIL there.
        self.pipeline.connect('deep-element-added', self._on_element)
        self.bus = self.pipeline.get_bus()
        ret = self.pipeline.set_state(Gst.State.PAUSED)
        self.log('set_state(PAUSED)', ret=ret.value_nick)

    def _on_element(self, _bin, _sub, element):
        f = element.get_factory()
        if f is not None:
            klass = f.get_metadata('klass') or ''
            if any(k in klass for k in ('Decoder', 'Demuxer', 'Source')):
                self.elements.append(f.get_name())

    def play(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        self.log('set_state(PLAYING)', ret=ret.value_nick)

    def tick(self):
        """One Kivy-Clock tick: drain the bus, take the newest frame."""
        while True:
            msg = self.bus.pop()
            if msg is None:
                break
            self._on_message(msg)
        sample = self.sink.try_pull_sample(0)
        if sample is not None:
            self._on_sample(sample)

    def _on_message(self, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            if self.error is None:
                self.error = dict(
                    message=err.message, domain=err.domain, code=err.code,
                    src=msg.src.get_name() if msg.src else None,
                    debug=(dbg or '')[:400])
            self.log('ERROR', message=err.message, domain=err.domain,
                     src=msg.src.get_name() if msg.src else None)
        elif t == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            self.log('WARNING', message=err.message,
                     src=msg.src.get_name() if msg.src else None)
        elif t == Gst.MessageType.EOS:
            self.eos = True
            self.log('EOS')
        elif t == Gst.MessageType.BUFFERING:
            pct = msg.parse_buffering()
            if not self.buffering or self.buffering[-1][1] != pct:
                self.buffering.append(
                    (round(time.perf_counter() - self.t0, 2), pct))
        elif t == Gst.MessageType.STATE_CHANGED:
            if msg.src == self.pipeline:
                old, new, pending = msg.parse_state_changed()
                self.log('STATE', old=old.value_nick, new=new.value_nick)
        elif t == Gst.MessageType.STREAM_COLLECTION:
            coll = msg.parse_stream_collection()
            self.log('STREAM_COLLECTION', n=coll.get_size())
        elif t == Gst.MessageType.STREAMS_SELECTED:
            self.log('STREAMS_SELECTED')
        elif t == Gst.MessageType.ELEMENT:
            s = msg.get_structure()
            if s and s.get_name() == 'missing-plugin':
                self.log('MISSING_PLUGIN', detail=s.to_string()[:200])
        elif t == Gst.MessageType.ASYNC_DONE:
            self.log('ASYNC_DONE')
        elif t == Gst.MessageType.DURATION_CHANGED:
            self.log('DURATION_CHANGED')

    def _on_sample(self, sample):
        buf = sample.get_buffer()
        caps = sample.get_caps()
        if self.size is None:
            s = caps.get_structure(0)
            self.size = (s.get_value('width'), s.get_value('height'))
            self.first_frame_s = round(time.perf_counter() - self.t0, 3)
            self.log('FIRST_FRAME', size=self.size, caps=caps.to_string()[:120])
        t = time.perf_counter()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytes(info.data)  # the copy a blit_buffer() would need
            buf.unmap(info)
            self.copy_ms.append((time.perf_counter() - t) * 1000)
            del data
        self.frames += 1

    def position(self):
        ok, pos = self.pipeline.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else None

    def duration(self):
        ok, dur = self.pipeline.query_duration(Gst.Format.TIME)
        return dur / Gst.SECOND if ok else None

    def unload(self):
        if self.pipeline is not None:
            t = time.perf_counter()
            self.pipeline.set_state(Gst.State.NULL)
            self.log('set_state(NULL)', ms=round((time.perf_counter() - t) * 1000, 1))
            self.pipeline = None


def run_case(name, binname, fmt, hz=60, seconds=None):
    uri, secs = CASES[name]
    secs = seconds or secs
    hls = None
    kill_at = None
    if uri == 'local-hls':
        hls = LocalHLS()
        files = hls.build()
        uri = hls.start()
        kill_at = 4.0
    elif callable(uri):
        uri = uri()
    p = Probe(uri, binname, fmt)
    killed_t = None
    cpu0, wall0 = time.process_time(), time.perf_counter()
    try:
        p.load()
        p.play()
        period = 1.0 / hz
        tick_ms = []
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            t = time.perf_counter()
            p.tick()
            tick_ms.append((time.perf_counter() - t) * 1000)
            if kill_at and killed_t is None and t - p.t0 > kill_at:
                hls.kill()
                killed_t = round(t - p.t0, 2)
                p.log('SERVER_KILLED')
            if p.error or p.eos:
                # keep draining briefly so we see follow-up messages
                for _ in range(10):
                    time.sleep(period)
                    p.tick()
                break
            time.sleep(max(0, period - (time.perf_counter() - t)))
        cpu_pct = round(100 * (time.process_time() - cpu0) /
                        (time.perf_counter() - wall0), 1)
        dur = p.duration()
        pos = p.position()
        p.unload()
    finally:
        if hls:
            hls.cleanup()
    tick_ms.sort()
    copy = sorted(p.copy_ms)
    res = dict(
        case=name, bin=binname, format=fmt, uri=uri,
        first_frame_s=p.first_frame_s, frames=p.frames, size=p.size,
        duration=dur, position=pos, eos=p.eos, error=p.error,
        elements=sorted(set(p.elements)),
        buffering=p.buffering[:12],
        tick_ms_p50=round(tick_ms[len(tick_ms) // 2], 3) if tick_ms else None,
        tick_ms_max=round(tick_ms[-1], 3) if tick_ms else None,
        copy_ms_p50=round(copy[len(copy) // 2], 3) if copy else None,
        server_killed_at=killed_t, cpu_pct_one_core=cpu_pct,
        events=p.events[:60],
    )
    if killed_t is not None and p.error:
        err_t = next((e['t'] for e in p.events if e['what'] == 'ERROR'), None)
        res['error_after_kill_s'] = round(err_t - killed_t, 2) if err_t else None
    return res


def summarize(r):
    err = r['error']
    line = (f"{r['case']:16s} {r['bin']:9s} {r['format']:5s} "
            f"first={r['first_frame_s']}s frames={r['frames']} size={r['size']} "
            f"eos={r['eos']} cpu={r['cpu_pct_one_core']}% tick_p50={r['tick_ms_p50']}ms copy_p50={r['copy_ms_p50']}ms")
    print(line)
    print('    elements:', ', '.join(r['elements']))
    if r['buffering']:
        print('    buffering:', r['buffering'])
    if err:
        print(f"    ERROR [{err['domain']}:{err['code']}] from {err['src']}: {err['message']}")
        if r.get('error_after_kill_s') is not None:
            print(f"    error arrived {r['error_after_kill_s']}s after the server died")
    for e in r['events']:
        if e['what'] in ('WARNING', 'MISSING_PLUGIN'):
            print('    ', e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', action='append')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--bin', default='playbin3', choices=['playbin', 'playbin3', 'both'])
    ap.add_argument('--format', default='RGBA')
    ap.add_argument('--seconds', type=float)
    ap.add_argument('--json')
    a = ap.parse_args()
    if a.list:
        print('\n'.join(CASES))
        return
    Gst.init(None)
    print(Gst.version_string(), platform.platform(), 'Python', platform.python_version())
    cases = list(CASES) if a.all else (a.case or ['local-1080p'])
    bins = ['playbin', 'playbin3'] if a.bin == 'both' else [a.bin]
    results = []
    for c in cases:
        for b in bins:
            r = run_case(c, b, a.format, seconds=a.seconds)
            summarize(r)
            results.append(r)
    if a.json:
        meta = dict(gst=Gst.version_string(), platform=platform.platform(),
                    python=platform.python_version(),
                    machine=platform.machine())
        with open(a.json, 'w') as f:
            json.dump(dict(meta=meta, results=results), f, indent=1, default=str)
        print('wrote', a.json)


if __name__ == '__main__':
    main()
