import os, sys
os.environ.setdefault('KIVY_NO_ARGS', '1')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'examples', 'widgets', 'video'))

from kivy.clock import Clock
import thumbnail_demo as td


class TestApp(td.ThumbnailDemoApp):
    def on_start(self):
        super().on_start()
        Clock.schedule_once(self.check_thumb, 4.0)

    def check_thumb(self, dt):
        r = self.root_widget
        print('STATUS:', r.status)
        print('READY:', r.ready)
        print('THUMB_TEXTURE:', r.thumb_texture)
        if r.thumb_texture:
            r.export_to_png('thumb_demo_screenshot.png')
            print('SCREENSHOT_OK')
        r.toggle_play()
        Clock.schedule_once(self.check_playing, 1.0)

    def check_playing(self, dt):
        r = self.root_widget
        print('PLAYING:', r.playing, 'VIDEO_STATE:', r.video_state)
        print('VIDEO_POSITION:', r.ids.video.position)
        r.export_to_png('thumb_demo_playing_screenshot.png')
        print('SCREENSHOT_PLAYING_OK')
        self.stop()


TestApp().run()
