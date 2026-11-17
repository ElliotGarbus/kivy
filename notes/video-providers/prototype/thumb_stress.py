"""Stress test for VideoMediaFoundation.generate_thumbnail(): calls it
STRESS_N times in a row inside a running Kivy app (one call per Clock
tick, so the window pumps between calls), appending to the same trace
file the native layer writes to when KIVY_MF_TRACE is set.

Env: KIVY_MF_TRACE (trace file), STRESS_CLIP (video path), STRESS_N.
"""
import os
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')
TRACE = os.environ['KIVY_MF_TRACE']
CLIP = os.environ['STRESS_CLIP']
N = int(os.environ.get('STRESS_N', '10'))


def log(msg):
    with open(TRACE, 'a') as f:
        f.write(f'PY {msg}\n')


from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.core.window import Window
from kivy.uix.widget import Widget


class StressApp(App):
    def build(self):
        Window.size = (400, 300)
        return Widget()

    def on_start(self):
        self.i = 0
        Clock.schedule_once(self.step, 0.2)

    def step(self, dt):
        if self.i >= N:
            log('ALL_DONE')
            self.stop()
            return
        self.i += 1
        t0 = time.time()
        log(f'iter {self.i} start')
        tex = CoreVideo.generate_thumbnail(CLIP, 2, size=(480, 270))
        log(f'iter {self.i} end ok={tex is not None} {time.time() - t0:.2f}s')
        Clock.schedule_once(self.step, 0.05)


StressApp().run()
