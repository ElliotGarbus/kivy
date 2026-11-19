"""Device check for the Media3 ExoPlayer video provider.

Runs a fixed list of cases through the public kivy.uix.video.Video widget and
prints one ``VCHECK`` line per event, for run_check.sh to read from logcat:

- ``VCHECK CASE_OK <name>`` / ``VCHECK CASE_FAIL <name> <reason>``
- ``VCHECK SHOT <name>``: the host should take a screenshot now
- ``VCHECK ACTION background``: the host should send the app to the
  background and bring it back
- ``VCHECK DONE pass=<n> fail=<n>``
"""

import os
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.video import Video

CLIPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips")
HLS = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
HTTP_MP4 = (
    "https://storage.googleapis.com/exoplayer-test-media-0/"
    "BigBuckBunny_320x180.mp4")
HTTP_403 = (
    "https://storage.googleapis.com/exoplayer-test-media-0/"
    "kivy-no-such-file.mp4")


def log(*parts):
    # One string: Android's stdout redirect logs each write() as its own
    # logcat line, and print() writes every argument separately.
    print(" ".join(["VCHECK"] + [str(part) for part in parts]), flush=True)


def clip(name):
    return os.path.join(CLIPS, name)


class CheckFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


class Wait:
    def __init__(self, predicate, timeout, what):
        self.predicate = predicate
        self.timeout = timeout
        self.what = what


def wait(predicate, timeout, what):
    return Wait(predicate, timeout, what)


# ---------- Cases. Each is a generator: yield seconds to sleep, or a Wait.


def case_provider(app):
    log("INFO", "provider", CoreVideo.__name__)
    check(CoreVideo.__name__ == "VideoAndroid",
          "selected provider is {}".format(CoreVideo.__name__))
    yield 0


def case_h264(app):
    video = app.new_video(clip("clip.mp4"), state="play")
    yield wait(lambda: video.loaded, 15, "on_load")
    log("INFO", "h264 texture", tuple(video.texture.size),
        "duration", round(video.duration, 2))
    core = video._video
    log("INFO", "h264 transform", [round(v, 4) for v in core._transform])
    for instruction in core._fbo.children:
        if hasattr(instruction, "tex_coords"):
            log("INFO", "h264 fbo", type(instruction).__name__,
                tuple(instruction.tex_coords))
    check(tuple(video.texture.size) == (640, 360),
          "texture size {}".format(tuple(video.texture.size)))
    yield 1.5
    start = video.position
    yield 1.0
    check(video.position - start > 0.5,
          "position not advancing: {} -> {}".format(start, video.position))
    log("SHOT", "h264")
    yield 3.0

    video.seek(0.5)
    yield 1.0
    check(abs(video.position - video.duration * 0.5) < 1.5,
          "seek to 50% landed at {}".format(video.position))

    video.state = "pause"
    yield 0.5
    paused_at = video.position
    yield 1.0
    check(abs(video.position - paused_at) < 0.05,
          "moved while paused: {} -> {}".format(paused_at, video.position))

    video.state = "play"
    video.seek(0.95)
    yield wait(lambda: video.eos, 5, "eos")
    yield 0.3
    check(video.state == "stop", "state after eos is {}".format(video.state))
    check(not video.errors, "unexpected errors {}".format(video.errors))


def case_rotated(app):
    video = app.new_video(clip("clip_rot90.mp4"), state="play")
    yield wait(lambda: video.loaded, 15, "on_load")
    size = tuple(video.texture.size)
    log("INFO", "rot90 texture", size)
    log("SHOT", "rot90")
    yield 3.0
    check(size == (360, 640), "rotated texture size {}".format(size))


def case_loop(app):
    video = app.new_video(clip("clip.mp4"), state="play",
                          options={"eos": "loop"})
    yield wait(lambda: video.loaded and video._video is not None, 15, "on_load")
    ends = []
    video._video.bind(on_eos=lambda *args: ends.append(time.monotonic()))
    video.seek(0.95)
    yield wait(lambda: len(ends) >= 1, 5, "first end")
    yield 1.5
    check(video.state == "play", "state after loop is {}".format(video.state))
    check(video.position < 3.0,
          "did not restart, position {}".format(video.position))
    video.seek(0.95)
    yield wait(lambda: len(ends) >= 2, 5, "second end")
    check(not video.errors, "unexpected errors {}".format(video.errors))


def case_errors(app):
    cases = [
        ("corrupt", clip("corrupt.mp4"), "ERROR_CODE_PARSING"),
        ("content", "content://org.kivyforge.nosuchprovider/video.mp4",
         "ERROR_CODE_IO"),
        ("http403", HTTP_403, "ERROR_CODE_IO_BAD_HTTP_STATUS"),
        ("missing", clip("no-such-clip.mp4"), "file not found"),
    ]
    for name, source, expected in cases:
        video = app.new_video(source, state="play")
        yield wait(lambda: video.errors, 20, "on_error for {}".format(name))
        yield 1.0
        log("INFO", "error", name, repr(video.errors))
        check(len(video.errors) == 1,
              "{}: on_error fired {} times".format(name, len(video.errors)))
        check(expected in video.errors[0],
              "{}: message {!r}".format(name, video.errors[0]))
        check(video.state == "stop",
              "{}: state {}".format(name, video.state))
        check(not video.loaded, "{}: loaded".format(name))


def case_http(app):
    video = app.new_video(HTTP_MP4, state="play")
    yield wait(lambda: video.loaded, 30, "on_load")
    log("INFO", "http texture", tuple(video.texture.size))
    yield 2.0
    start = video.position
    yield 1.5
    check(video.position - start > 0.5,
          "position not advancing: {} -> {}".format(start, video.position))
    check(not video.errors, "unexpected errors {}".format(video.errors))


def case_hls(app):
    video = app.new_video(HLS, state="play")
    yield wait(lambda: video.loaded, 30, "on_load")
    log("INFO", "hls texture", tuple(video.texture.size))
    yield 3.0
    start = video.position
    yield 2.0
    check(video.position - start > 1.0,
          "position not advancing: {} -> {}".format(start, video.position))
    log("SHOT", "hls")
    yield 3.0
    check(not video.errors, "unexpected errors {}".format(video.errors))


def case_thumbnail(app):
    app.clear()
    texture = CoreVideo.generate_thumbnail(clip("clip.mp4"), 5.0, size=(320, 180))
    check(texture is not None, "no thumbnail")
    log("INFO", "thumbnail", tuple(texture.size))
    check(tuple(texture.size) == (320, 180),
          "thumbnail size {}".format(tuple(texture.size)))
    app.holder.add_widget(Image(texture=texture, fit_mode="contain"))
    log("SHOT", "thumbnail")
    yield 3.0
    missing = CoreVideo.generate_thumbnail(clip("no-such-clip.mp4"), 1.0)
    check(missing is None, "thumbnail of a missing file")


def case_pause_resume(app):
    video = app.new_video(clip("clip.mp4"), state="play",
                          options={"eos": "loop"})
    yield wait(lambda: video.loaded, 15, "on_load")
    resumed = app.resumed
    log("ACTION", "background")
    yield wait(lambda: app.resumed > resumed, 60, "app resume")
    yield 2.0
    start = video.position
    yield 1.5
    check(video.position - start > 0.5,
          "not playing after resume: {} -> {}".format(start, video.position))
    log("SHOT", "resumed")
    yield 3.0
    check(not video.errors, "unexpected errors {}".format(video.errors))


CASES = [
    ("provider", case_provider),
    ("h264", case_h264),
    ("rotated", case_rotated),
    ("loop", case_loop),
    ("errors", case_errors),
    ("http", case_http),
    ("hls", case_hls),
    ("thumbnail", case_thumbnail),
    ("pause_resume", case_pause_resume),
]
# A file named "quick" next to this one limits the run to the picture cases.
if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "quick")):
    CASES = [case for case in CASES if case[0] in ("provider", "h264", "rotated")]


class Runner:
    def __init__(self, app, cases):
        self.app = app
        self.cases = list(cases)
        self.passed = []
        self.failed = []
        self.name = None
        self.case = None
        self.pending = None
        self.deadline = 0
        self.sleep_until = 0
        Clock.schedule_interval(self.tick, 0.05)

    def tick(self, dt):
        now = time.monotonic()
        if self.pending is not None:
            if self.pending.predicate():
                self.pending = None
            elif now > self.deadline:
                self.finish_case("timed out waiting for " + self.pending.what)
                return
            else:
                return
        if now < self.sleep_until:
            return
        if self.case is None:
            if not self.cases:
                log("DONE", "pass={}".format(len(self.passed)),
                    "fail={}".format(len(self.failed)))
                self.app.status.text = "done: {} passed, {} failed".format(
                    len(self.passed), len(self.failed))
                return False
            self.name, factory = self.cases.pop(0)
            self.app.status.text = "running " + self.name
            log("CASE_START", self.name)
            self.case = factory(self.app)
        try:
            item = next(self.case)
        except StopIteration:
            self.finish_case(None)
            return
        except CheckFailed as e:
            self.finish_case(str(e))
            return
        except Exception as e:
            self.finish_case("{}: {}".format(type(e).__name__, e))
            return
        if isinstance(item, Wait):
            self.pending = item
            self.deadline = now + item.timeout
        else:
            self.sleep_until = now + item

    def finish_case(self, failure):
        if failure is None:
            self.passed.append(self.name)
            log("CASE_OK", self.name)
        else:
            self.failed.append(self.name)
            log("CASE_FAIL", self.name, failure)
        self.case.close()
        self.case = None
        self.pending = None
        self.sleep_until = 0
        self.app.clear()


class CheckApp(App):
    resumed = 0

    def build(self):
        root = BoxLayout(orientation="vertical")
        self.status = Label(size_hint_y=None, height="80dp", text="starting")
        self.holder = BoxLayout()
        root.add_widget(self.status)
        root.add_widget(self.holder)
        return root

    def on_start(self):
        # Kept on the app: the Clock holds the runner's tick only weakly.
        Clock.schedule_once(lambda dt: setattr(self, "runner", Runner(self, CASES)), 1.0)

    def on_pause(self):
        return True

    def on_resume(self):
        self.resumed += 1
        log("INFO", "app resumed")

    def new_video(self, source, **kwargs):
        self.clear()
        video = Video(source=source, **kwargs)
        video.errors = []

        def on_error(instance, error):
            video.errors.append(error)
            log("ON_ERROR", repr(error))

        video.bind(on_error=on_error)
        self.holder.add_widget(video)
        return video

    def clear(self):
        for widget in list(self.holder.children):
            if isinstance(widget, Video):
                widget.unload()
        self.holder.clear_widgets()


CheckApp().run()
