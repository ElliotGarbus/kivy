"""Play one URL and report when it loads or fails, waiting up to N seconds.

    python probe.py URL [SECONDS]
"""

import sys
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.uix.video import Video

URL = sys.argv[1]
LIMIT = float(sys.argv[2]) if len(sys.argv) > 2 else 180


class ProbeApp(App):
    def build(self):
        self.t0 = time.monotonic()
        self.video = Video(source=URL, state="play")
        self.video.bind(on_error=self.on_error, loaded=self.on_loaded)
        Clock.schedule_interval(self.tick, 1)
        print("PROBE start", CoreVideo.__name__, URL, flush=True)
        return self.video

    def elapsed(self):
        return round(time.monotonic() - self.t0, 1)

    def on_loaded(self, instance, value):
        if value:
            print("PROBE loaded after", self.elapsed(), "s", flush=True)
            Clock.schedule_once(lambda dt: self.stop(), 3)

    def on_error(self, instance, error):
        print("PROBE on_error after", self.elapsed(), "s:", error, flush=True)
        Clock.schedule_once(lambda dt: self.stop(), 1)

    def tick(self, dt):
        if self.elapsed() > LIMIT:
            print("PROBE nothing after", self.elapsed(), "s", flush=True)
            self.stop()


ProbeApp().run()
