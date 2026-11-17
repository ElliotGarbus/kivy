import os
os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.uix.widget import Widget


class ThumbApp(App):
    def build(self):
        Clock.schedule_once(self.check, 0.3)
        return Widget()

    def check(self, dt):
        print('CALLING generate_thumbnail...')
        tex = CoreVideo.generate_thumbnail(
            'notes/video-providers/prototype/mf_playback_test_clip.mp4', 1.0, size=(64, 64))
        print('THUMBNAIL:', tex, tex.size if tex else None, tex.colorfmt if tex else None)
        if tex:
            tex.save('notes/video-providers/prototype/thumbnail.png', flipped=False)
            print('THUMBNAIL_SAVED')
        self.stop()


ThumbApp().run()
