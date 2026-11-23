"""Check the docs' reconnect snippet: play a live stream, drop the server,
restore it, and see whether the snippet brings playback back.

    python reconnect_check.py URL DROP_NAME     (DROP_NAME: rtsp or hls)
"""
import sys
import time
import urllib.request

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.video import Video

URL, WHAT = sys.argv[1], sys.argv[2]
CONTROL = "http://10.168.168.202:8099/{}?name=" + WHAT


def control(action):
    urllib.request.urlopen(CONTROL.format(action), timeout=5).read()


class R(App):
    def build(self):
        self.t0 = time.monotonic()
        self.frames = 0
        video = self.video = Video(source=URL, state="play")

        # --- the snippet from the docs ---
        def reconnect(*args):
            self.say("reconnect")
            source = video.source
            video.source = ""
            video.source = source
            video.state = "play"

        reconnect_later = Clock.create_trigger(reconnect, 3)
        video.bind(on_error=lambda *args: reconnect_later())
        video.bind(eos=lambda instance, value: value and reconnect_later())
        # --- end of the snippet ---

        video.bind(on_error=lambda i, e: self.say("on_error", e))
        video.bind(eos=lambda i, v: v and self.say("eos"))
        video.bind(texture=self.count, position=self.count)
        Clock.schedule_once(lambda dt: (control("drop"), self.say("dropped")), 10)
        self.restore_ev = None
        Clock.schedule_interval(self.tick, 1)
        return video

    def say(self, *a):
        print("RECON", round(time.monotonic() - self.t0, 1), *a, flush=True)

    def count(self, *a):
        self.frames += 1

    def tick(self, dt):
        t = time.monotonic() - self.t0
        if self.restore_ev is None and t > 10 and (self.video.state == "stop"):
            # Restore as soon as the provider has given up on the stream.
            self.restore_ev = True
            control("restore")
            self.say("restored")
            self.mark = self.frames
        if t > 75 or (self.restore_ev and t > 10 and
                      self.frames - getattr(self, "mark", 0) > 60):
            self.say("frames after restore:",
                     self.frames - getattr(self, "mark", self.frames),
                     "state", self.video.state)
            control("restore")
            self.stop()


R().run()
