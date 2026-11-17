"""Checks for a white flash right after pressing Play in thumbnail_demo:
waits for the thumbnail, presses Play, snapshots the root widget at
several delays, and reports the fraction of near-white pixels in the
video area for each snapshot.
"""
import os
import sys

os.environ.setdefault('KIVY_NO_ARGS', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'examples', 'widgets', 'video'))

from PIL import Image as PILImage

from kivy.clock import Clock
import thumbnail_demo as td

DELAYS = [0.0, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3, 0.5]


def white_fraction(path):
    im = PILImage.open(path).convert('RGB')
    w, h = im.size
    px = list(im.crop((int(w * 0.2), int(h * 0.3), int(w * 0.8), int(h * 0.8))).getdata())
    return sum(1 for r, g, b in px if r > 240 and g > 240 and b > 240) / len(px)


class FlashTestApp(td.ThumbnailDemoApp):
    def on_start(self):
        super().on_start()
        Clock.schedule_once(self._wait_ready, 0.1)

    def _wait_ready(self, dt):
        r = self.root_widget
        if not r.ready or r.thumb_texture is None:
            Clock.schedule_once(self._wait_ready, 0.1)
            return
        Clock.schedule_once(self._press_play, 0.3)

    def _press_play(self, dt):
        self._pending = list(range(len(DELAYS)))
        self.root_widget.toggle_play()
        for d in DELAYS:
            Clock.schedule_once(self._snap_next, d)
        Clock.schedule_once(self._finish, DELAYS[-1] + 0.3)

    def _snap_next(self, dt):
        i = self._pending.pop(0)
        path = os.path.join(HERE, f'flash_{i}.png')
        self.root_widget.export_to_png(path)
        print(f'snap {i} t={DELAYS[i]:.2f}s white={white_fraction(path):.3f} '
              f'loaded={self.root_widget.ids.video.loaded}', flush=True)

    def _finish(self, dt):
        self.stop()


FlashTestApp().run()
