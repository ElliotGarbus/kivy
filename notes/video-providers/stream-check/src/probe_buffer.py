"""Play URL 8 s, pause 3 s, resume 12 s, seek 50 %, 15 s; log the gstplayer
provider's buffering state every 0.5 s.  python probe_buffer.py URL"""
import sys
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.video import Video

URL = sys.argv[1]
PLAN = [(8, None), (3, "pause"), (12, "play"), (15, "seek"), (0, "quit")]


class P(App):
    def build(self):
        self.t0 = time.monotonic()
        self.frames = 0
        import os
        opts = {"render": os.environ["PROBE_RENDER"]} if os.environ.get(
            "PROBE_RENDER") else {}
        self.v = Video(source=URL, state="play", options=opts)
        self.v.bind(texture=self.on_tex)
        self.v.bind(on_error=lambda *a: print("BUF on_error", a[1:], flush=True))
        self.v.bind(on_buffering=lambda inst, val: print(
            "BUF on_buffering", round(time.monotonic() - self.t0, 1), val,
            flush=True))
        Clock.schedule_interval(self.tick, 0.5)
        self.step = 0
        self.next_at = PLAN[0][0]
        return self.v

    def on_tex(self, *a):
        core = self.v._video
        if core is not None and not getattr(self, "bound", False):
            self.bound = True
            core.bind(on_frame=self.on_frame)

    def on_frame(self, *a):
        self.frames += 1

    def tick(self, dt):
        t = round(time.monotonic() - self.t0, 1)
        core = self.v._video
        if core is not None:
            print("BUF t={} frames={} state={} target={} pct={} live={} "
                  "prerolled={} pos={:.1f}".format(
                      t, self.frames, core._state, core._gst_target,
                      core._buffer_percent, core._live, core._prerolled,
                      core.position), flush=True)
        if t >= self.next_at:
            self.step += 1
            _, action = PLAN[self.step]
            print("BUF action", action, "at", t, flush=True)
            if action == "pause":
                self.v.state = "pause"
            elif action == "play":
                self.v.state = "play"
            elif action == "seek":
                self.v.seek(0.5)
            elif action == "quit":
                self.stop()
                return
            self.next_at = t + PLAN[self.step][0]


P().run()
