"""Network-stream check for Kivy 3's video providers.

Plays each stream from ``streams.json`` (served by ``server/streams.py`` on the
Pi) through the public kivy.uix.video.Video widget and prints one ``SCHECK``
line per event. The same file runs on the desktop and, built with kivyforge,
on Android.

Lines:

- ``SCHECK CASE_START <name> <url>``
- ``SCHECK INFO <name> <key> <value>``: a measurement
- ``SCHECK STEP_OK <name> <step>`` / ``SCHECK STEP_FAIL <name> <step> <why>``
- ``SCHECK ON_ERROR <name> <message>``
- ``SCHECK CASE_END <name> ok=<n> fail=<n>``
- ``SCHECK DONE ok=<n> fail=<n>``

Also writes ``results-<provider>.jsonl`` next to this file on the desktop
(``user_data_dir`` on Android).

Settings, from the environment or, on Android, from ``config.json``:
``SCHECK_HOST`` (the Pi), ``SCHECK_ONLY`` (comma-separated case names),
``SCHECK_SKIP``.
"""

import json
import os
import time
import urllib.request

from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.video import Video

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    config = {}
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            config.update(json.load(f))
    for key in ("SCHECK_HOST", "SCHECK_ONLY", "SCHECK_SKIP"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


CONFIG = load_config()
HOST = CONFIG.get("SCHECK_HOST", "10.168.168.202")

# name, url, kind. kind: "live", "vod" (has a duration and an end) or
# "error" (must fire on_error and never load).
STREAMS = [
    ("rtsp_h264", "rtsp://{h}:8554/h264", "live"),
    ("rtsp_h265", "rtsp://{h}:8554/h265", "live"),
    ("rtsp_h264_tcp", "rtspt://{h}:8554/h264", "live"),
    ("hls_live_ts", "http://{h}:8080/live-ts/index.m3u8", "live"),
    ("hls_live_fmp4", "http://{h}:8080/live-fmp4/index.m3u8", "live"),
    ("hls_vod", "http://{h}:8080/vod/index.m3u8", "vod"),
    ("dash_vod", "http://{h}:8080/dash/manifest.mpd", "vod"),
    ("mjpeg", "http://{h}:8090/cam.mjpg", "live"),
    ("hls_public_mux",
     "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "vod"),
    ("hls_public_apple",
     "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
     "img_bipbop_adv_example_fmp4/master.m3u8", "vod"),
    ("rtsp_bad_path", "rtsp://{h}:8554/no-such-path", "error"),
    ("rtsp_bad_port", "rtsp://{h}:8555/h264", "error"),
    ("hls_bad_path", "http://{h}:8080/no-such/index.m3u8", "error"),
    # Plays rtsp_h264, then asks the server to stop it: on_error should fire.
    ("rtsp_server_drop", "rtsp://{h}:8554/h264", "drop"),
    ("hls_server_drop", "http://{h}:8080/live-ts/index.m3u8", "drop"),
]

LOAD_TIMEOUT = 25
ERROR_GRACE = 60
RESULTS = []
PROVIDER = CoreVideo.__name__ if CoreVideo else "None"


def log(*parts):
    # One string: Android's stdout redirect logs each write() separately.
    print(" ".join(["SCHECK"] + [str(part) for part in parts]), flush=True)


def record(case, key, value):
    log("INFO", case, key, value)
    RESULTS.append({"case": case, "key": key, "value": value})


class Wait:
    def __init__(self, predicate, timeout, what):
        self.predicate = predicate
        self.timeout = timeout
        self.what = what


class StepFailed(Exception):
    pass


def server(action, name):
    url = "http://{}:8099/{}?name={}".format(HOST, action, name)
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


class FrameCounter:
    def __init__(self, core):
        self.count = 0
        self.core = core
        core.bind(on_frame=self.on_frame)

    def on_frame(self, *args):
        self.count += 1

    def detach(self):
        self.core.unbind(on_frame=self.on_frame)


def stream_case(app, name, url, kind):
    """A generator: yields seconds to sleep or a Wait; steps report
    themselves through app.step()."""
    video = app.new_video(url, state="play")
    started = time.monotonic()
    yield Wait(lambda: video.loaded or video.errors, LOAD_TIMEOUT,
               "loaded or on_error")
    load_time = round(time.monotonic() - started, 2)

    if kind == "error":
        app.step(name, "fires_on_error", bool(video.errors) and not video.loaded,
                 "loaded={} errors={}".format(video.loaded, video.errors))
        record(name, "error_after_s", load_time)
        if not video.errors:
            yield Wait(lambda: video.errors, 30, "on_error")
            app.step(name, "on_error_eventually", bool(video.errors), "")
        return

    if not video.loaded:
        if not video.errors:
            # Neither loaded nor failed: does on_error ever come?
            t1 = time.monotonic()
            yield Wait(lambda: video.loaded or video.errors
                       or time.monotonic() - t1 > ERROR_GRACE, ERROR_GRACE + 5,
                       "late load or on_error")
            record(name, "late_result_after_s",
                   round(time.monotonic() - started, 1))
            record(name, "late_result", "loaded" if video.loaded else
                   ("on_error" if video.errors else "nothing"))
        if not video.loaded:
            app.step(name, "loads", False, "on_error: {}".format(video.errors))
            app.step(name, "reports_failure", bool(video.errors),
                     "no on_error {} s after play".format(
                         round(time.monotonic() - started)))
            return
    app.step(name, "loads", True, "")
    record(name, "load_s", load_time)
    record(name, "texture", tuple(video.texture.size) if video.texture else None)
    record(name, "duration", round(video.duration, 2))

    core = video._video
    counter = FrameCounter(core)
    try:
        # Frames over 8 s.
        before, t0, p0 = counter.count, time.monotonic(), video.position
        yield 8
        frames = counter.count - before
        fps = round(frames / (time.monotonic() - t0), 1)
        record(name, "fps", fps)
        record(name, "position_advance", round(video.position - p0, 2))
        app.step(name, "plays", fps >= 5, "fps={}".format(fps))

        if kind == "drop":
            what = name.split("_")[0]
            server("drop", what)
            record(name, "server", "dropped")
            t0 = time.monotonic()
            try:
                yield Wait(lambda: video.errors or video.eos
                           or time.monotonic() - t0 > ERROR_GRACE,
                           ERROR_GRACE + 5, "on_error")
            finally:
                server("restore", what)
            record(name, "error_after_drop_s", round(time.monotonic() - t0, 1))
            app.step(name, "on_error_after_drop", bool(video.errors),
                     "eos={} errors={}".format(video.eos, video.errors))
            return

        # Pause: frames stop.
        video.state = "pause"
        yield 1
        before = counter.count
        yield 2
        app.step(name, "pause_stops_frames", counter.count - before <= 1,
                 "frames while paused={}".format(counter.count - before))
        # Resume.
        video.state = "play"
        before = counter.count
        yield 4
        app.step(name, "resume_plays", counter.count - before >= 8,
                 "frames after resume={}".format(counter.count - before))

        # Seek: a live stream should ignore it; a VOD stream should move.
        before_pos = video.position
        video.seek(0.5)
        yield 4
        before = counter.count
        yield 2
        record(name, "position_after_seek",
               "{} -> {}".format(round(before_pos, 1), round(video.position, 1)))
        app.step(name, "plays_after_seek", counter.count - before >= 4,
                 "frames={}".format(counter.count - before))
        if kind == "vod" and video.duration > 0:
            near_half = abs(video.position / video.duration - 0.5) < 0.15
            app.step(name, "seek_moves", near_half,
                     "position={} duration={}".format(
                         round(video.position, 1), round(video.duration, 1)))

        # Stop and play again: should reconnect.
        video.state = "stop"
        yield 2
        video.state = "play"
        t0 = time.monotonic()
        before = counter.count
        yield Wait(lambda: counter.count - before >= 10, 20, "frames after replay")
        record(name, "replay_s", round(time.monotonic() - t0, 2))
        app.step(name, "stop_then_play", True, "")

        # Unload must not hang (#6228, #5191).
        t0 = time.monotonic()
        video.unload()
        unload_s = round(time.monotonic() - t0, 2)
        record(name, "unload_s", unload_s)
        app.step(name, "unload_quick", unload_s < 2, "unload took {} s".format(
            unload_s))
    finally:
        counter.detach()


def selected_streams():
    only = [s for s in CONFIG.get("SCHECK_ONLY", "").split(",") if s]
    skip = [s for s in CONFIG.get("SCHECK_SKIP", "").split(",") if s]
    for name, url, kind in STREAMS:
        if only and name not in only:
            continue
        if name in skip:
            continue
        yield name, url.format(h=HOST), kind


class Runner:
    def __init__(self, app):
        self.app = app
        self.cases = list(selected_streams())
        self.name = None
        self.gen = None
        self.pending = None
        self.deadline = 0
        self.sleep_until = 0
        self.ok = 0
        self.fail = 0
        self.case_ok = 0
        self.case_fail = 0
        Clock.schedule_interval(self.tick, 0.05)

    def tick(self, dt):
        now = time.monotonic()
        if self.pending is not None:
            if self.pending.predicate():
                self.pending = None
            elif now > self.deadline:
                self.app.step(self.name, "wait", False,
                              "timed out waiting for " + self.pending.what)
                self.end_case()
                return
            else:
                return
        if now < self.sleep_until:
            return
        if self.gen is None:
            if not self.cases:
                log("DONE", "ok={}".format(self.ok), "fail={}".format(self.fail))
                self.app.status.text = "done: {} ok, {} failed".format(
                    self.ok, self.fail)
                self.app.write_results()
                from kivy.utils import platform
                if platform != "android":
                    # run_android.sh watches logcat; on a desktop, exit.
                    Clock.schedule_once(lambda dt: self.app.stop(), 1)
                return False
            self.name, url, kind = self.cases.pop(0)
            self.case_ok = self.case_fail = 0
            self.app.status.text = "{}: {}".format(self.name, url)
            log("CASE_START", self.name, url)
            self.gen = stream_case(self.app, self.name, url, kind)
        try:
            item = next(self.gen)
        except StopIteration:
            self.end_case()
            return
        except Exception as e:
            self.app.step(self.name, "exception", False,
                          "{}: {}".format(type(e).__name__, e))
            self.end_case()
            return
        if isinstance(item, Wait):
            self.pending = item
            self.deadline = now + item.timeout
        else:
            self.sleep_until = now + item

    def end_case(self):
        log("CASE_END", self.name, "ok={}".format(self.case_ok),
            "fail={}".format(self.case_fail))
        self.gen.close()
        self.gen = None
        self.pending = None
        self.sleep_until = 0
        self.app.clear()


class StreamCheckApp(App):
    def build(self):
        root = BoxLayout(orientation="vertical")
        self.status = Label(size_hint_y=None, height="60dp", text="starting")
        self.holder = BoxLayout()
        root.add_widget(self.status)
        root.add_widget(self.holder)
        return root

    def on_start(self):
        log("INFO", "-", "provider", PROVIDER)
        log("INFO", "-", "host", HOST)
        RESULTS.append({"case": "-", "key": "provider", "value": PROVIDER})
        # Kept on the app: the Clock holds the runner's tick only weakly.
        Clock.schedule_once(
            lambda dt: setattr(self, "runner", Runner(self)), 1.0)

    def on_pause(self):
        return True

    def step(self, case, step, ok, detail):
        if ok:
            self.runner.ok += 1
            self.runner.case_ok += 1
            log("STEP_OK", case, step)
        else:
            self.runner.fail += 1
            self.runner.case_fail += 1
            log("STEP_FAIL", case, step, detail)
        RESULTS.append({"case": case, "step": step, "ok": bool(ok),
                        "detail": detail})

    def new_video(self, source, **kwargs):
        self.clear()
        video = Video(source=source, **kwargs)
        video.errors = []
        case = self.runner.name

        def on_error(instance, error):
            video.errors.append(error)
            log("ON_ERROR", case, repr(error))

        video.bind(on_error=on_error)
        self.holder.add_widget(video)
        return video

    def clear(self):
        for widget in list(self.holder.children):
            if isinstance(widget, Video):
                widget.unload()
        self.holder.clear_widgets()

    def write_results(self):
        try:
            from kivy.utils import platform
            folder = self.user_data_dir if platform == "android" else HERE
            path = os.path.join(folder, "results-{}.jsonl".format(PROVIDER))
            with open(path, "w", encoding="utf-8") as f:
                for row in RESULTS:
                    f.write(json.dumps(row) + "\n")
            log("INFO", "-", "results", path)
        except Exception as e:
            log("INFO", "-", "results_not_written", e)


StreamCheckApp().run()
