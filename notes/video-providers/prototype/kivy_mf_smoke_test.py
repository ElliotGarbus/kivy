"""Smoke test for the real video_mediafoundation provider, run inside an
actual Kivy App (not the throwaway C++ prototypes) using the public
kivy.uix.video.Video widget -- exercises the real end-to-end integration:
provider auto-selection, Clock-driven _update, texture wrapping, and
whatever GL backend is actually active (glew/sdl3 default, or
angle_sdl3 if KIVY_GL_BACKEND=angle_sdl3 is set before this runs).

Usage: python kivy_mf_smoke_test.py <clip_path> <screenshot_out.png>
"""
import os
import sys

os.environ.setdefault('KIVY_NO_ARGS', '1')

clip_path = sys.argv[1]
screenshot_out = sys.argv[2]

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.video import Video


class SmokeApp(App):
    def build(self):
        self.video = Video(source=clip_path, state='play', options={})
        Clock.schedule_once(self.check, 1.5)
        return self.video

    def check(self, dt):
        v = self.video
        print('PROVIDER_CLASS:', type(v._video).__name__)
        print('TEXTURE:', v.texture)
        print('TEXTURE_SIZE:', v.texture.size if v.texture else None)
        print('POSITION:', v.position)
        print('DURATION:', v.duration)
        print('STATE:', v.state)
        try:
            v.export_to_png(screenshot_out)
            print('SCREENSHOT_OK:', screenshot_out)
        except Exception as e:
            print('SCREENSHOT_FAILED:', repr(e))
        Clock.schedule_once(self.stop_app, 0.5)

    def stop_app(self, dt):
        self.stop()


if __name__ == '__main__':
    SmokeApp().run()
