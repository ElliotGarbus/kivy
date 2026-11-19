"""Android video demo: plays a few formats through Kivy's Media3 ExoPlayer
provider (kivy.core.video.video_android).

Pick a source from the list; the player shows Kivy's VideoPlayer controls
(play/pause, stop, seek bar, volume; double-tap for full screen) and the panel
under it shows what is playing, its size, position and any error.

Two clips ship in the app. The rest stream from the public test media that
ExoPlayer's own demo app uses, so they need a network connection.
"""

import os

from kivy.app import App
from kivy.clock import Clock
from kivy.core.video import Video as CoreVideo
from kivy.metrics import dp, sp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.videoplayer import VideoPlayer

CLIPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips")
GCS = "https://storage.googleapis.com/"
APPLE = "https://devstreaming-cdn.apple.com/videos/streaming/examples/"

# (section, [(title, details, source), ...])
SOURCES = [
    ("In the app", [
        ("H.264 MP4", "640x360, AAC audio, 10 s",
         os.path.join(CLIPS, "clip.mp4")),
        ("H.264 MP4, portrait", "The same clip tagged with a 90 degree "
         "rotation, as a phone records it", os.path.join(CLIPS, "clip_rot90.mp4")),
    ]),
    ("Progressive download", [
        ("VP9 WebM", "360p, no audio",
         GCS + "exoplayer-test-media-1/gen-3/screens/dash-vod-single-segment/"
         "video-vp9-360.webm"),
        ("AV1 MP4", "Big Buck Bunny 480p",
         GCS + "downloads.webmproject.org/av1/exoplayer/bbb-av1-480p.mp4"),
        ("H.264 Matroska (MKV)", "1280x720, AAC audio",
         GCS + "exoplayer-test-media-1/mkv/"
         "android-screens-lavf-56.36.100-aac-avc-main-1280x720.mkv"),
        ("MPEG-TS", "Apple test segment, 10 s",
         APPLE + "bipbop_4x3/gear1/fileSequence0.ts"),
    ]),
    ("HLS", [
        ("HLS, MPEG-TS segments", "Apple 16:9 multi-bitrate stream",
         APPLE + "bipbop_16x9/bipbop_16x9_variant.m3u8"),
        ("HLS, fMP4 segments", "Apple advanced stream, up to 1080p",
         APPLE + "img_bipbop_adv_example_fmp4/master.m3u8"),
    ]),
    ("DASH", [
        ("DASH H.264", "Tears of Steel, adaptive up to 1080p",
         GCS + "wvmedia/clear/h264/tears/tears.mpd"),
        ("DASH HEVC (H.265)", "Tears of Steel, adaptive up to 1080p",
         GCS + "wvmedia/clear/hevc/tears/tears.mpd"),
        ("DASH VP9", "Tears of Steel, adaptive up to 1080p",
         GCS + "wvmedia/clear/vp9/tears/tears.mpd"),
        ("DASH H.264, 60 fps", "Big Buck Bunny 1080p60",
         GCS + "exoplayer-test-media-1/60fps/bbb-clear-1080/manifest.mpd"),
    ]),
]


def log(*parts):
    # One string: Android's stdout redirect logs each write() as its own
    # logcat line, and print() writes every argument separately.
    print(" ".join(["VDEMO"] + [str(part) for part in parts]), flush=True)


def fmt_time(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    return "{}:{:02d}".format(seconds // 60, seconds % 60)


class SourceButton(Button):
    source = StringProperty("")


class Demo(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=dp(4), **kwargs)
        self.current = None
        self.error = ""
        self.logged = None

        self.player = VideoPlayer(size_hint_y=0.42, options={"eos": "loop"})
        self.player.bind(on_error=self.on_player_error)
        self.add_widget(self.player)

        self.info = Label(
            size_hint_y=None, height=dp(96), font_size=sp(14), markup=True,
            halign="left", valign="top", padding=(dp(12), dp(6)))
        self.info.bind(size=lambda label, size: setattr(
            label, "text_size", size))
        self.add_widget(self.info)

        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(2),
                          padding=(dp(8), 0, dp(8), dp(8)))
        grid.bind(minimum_height=grid.setter("height"))
        for section, items in SOURCES:
            grid.add_widget(Label(
                text="[b]{}[/b]".format(section), markup=True,
                size_hint_y=None, height=dp(36), font_size=sp(15),
                halign="left", valign="bottom",
                color=(0.6, 0.8, 1, 1)))
            grid.children[0].bind(size=lambda label, size: setattr(
                label, "text_size", size))
            for title, details, source in items:
                button = SourceButton(
                    text="[b]{}[/b]\n[size={}]{}[/size]".format(
                        title, int(sp(12)), details),
                    markup=True, source=source, size_hint_y=None,
                    height=dp(60), halign="left", valign="middle",
                    font_size=sp(15), padding=(dp(12), dp(4)))
                button.bind(size=lambda b, size: setattr(b, "text_size", size))
                button.title = title
                button.bind(on_release=self.play)
                grid.add_widget(button)
        scroll = ScrollView(size_hint_y=0.58, do_scroll_x=False)
        scroll.add_widget(grid)
        self.add_widget(scroll)

        Clock.schedule_interval(self.refresh, 0.5)
        self.refresh()

    def play(self, button, *args):
        self.current = button
        self.error = ""
        log("PLAY", button.title, button.source)
        self.player.state = "stop"
        self.player.source = button.source
        self.player.state = "play"

    def on_player_error(self, player, error):
        self.error = str(error)
        log("ERROR", self.current.title if self.current else "-", self.error)

    def refresh(self, *args):
        if self.current is None:
            self.info.text = (
                "Provider: {}\nPick a video below.".format(
                    CoreVideo.__name__ if CoreVideo else "none"))
            return
        video = self.player._video
        texture = video.texture if video is not None else None
        size = ("{} x {}".format(*texture.size) if texture is not None
                else "loading...")
        lines = [
            "[b]{}[/b]".format(self.current.title),
            "Texture {}   {}   {} / {}".format(
                size, self.player.state,
                fmt_time(self.player.position),
                fmt_time(self.player.duration)),
        ]
        if self.error:
            lines.append("[color=ff6060]Error: {}[/color]".format(self.error))
        else:
            lines.append("Provider: {}".format(CoreVideo.__name__))
        self.info.text = "\n".join(lines)
        if texture is not None and self.logged != (self.current, texture.size):
            self.logged = (self.current, texture.size)
            log("SIZE", self.current.title, texture.size)


class VideoDemoApp(App):
    title = "Kivy Video Demo"

    def build(self):
        return Demo()

    def on_pause(self):
        return True


if __name__ == "__main__":
    VideoDemoApp().run()
