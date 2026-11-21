"""Spike 10: VideoGstplayer.generate_thumbnail timing and failure modes.

Local files at several times, a local HTTP server (progressive and HLS),
a missing file, a non-video, a dead URL. Writes JSON.

    python thumb_check.py results/thumbs-windows.json
"""
import json
import os
import platform
import socket
import subprocess
import sys
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')
from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.uix.widget import Widget  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, 'clips')


def serve(directory):
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'slow_http.py'), str(port),
         directory, '50000000'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    return proc, 'http://127.0.0.1:{}/'.format(port)


class ThumbApp(App):
    def build(self):
        Clock.schedule_once(self.run_checks, 0.5)
        return Widget()

    def run_checks(self, dt):
        from kivy.core.video.video_gstplayer import VideoGstplayer
        from kivy.lib.gstplayer import init_wait
        t = time.perf_counter()
        init_wait(120)
        init_s = time.perf_counter() - t
        files, hls = serve(CLIPS), serve(os.path.join(CLIPS, 'hls-cmaf'))
        cases = [
            ('local 1080p H.264 t=0', os.path.join(CLIPS, 'test_1080p30.mp4'), 0),
            ('local 1080p H.264 t=10', os.path.join(CLIPS, 'test_1080p30.mp4'), 10),
            ('local 1080p H.264 t=19.9', os.path.join(CLIPS, 'test_1080p30.mp4'), 19.9),
            ('local 1080p past end t=60', os.path.join(CLIPS, 'test_1080p30.mp4'), 60),
            ('local 2160p HEVC t=5', os.path.join(CLIPS, 'hevc_2160p30.mp4'), 5),
            ('local 360p t=5, size 160x90', os.path.join(CLIPS, 'big_buck_bunny_360_10s.mp4'), 5),
            ('local VP9 t=2', os.path.join(CLIPS, 'vp9_1080p.webm'), 2),
            ('http progressive t=5', files[1] + 'test_1080p30.mp4', 5),
            ('http HLS t=5', hls[1] + 'index.m3u8', 5),
            ('missing file', os.path.join(CLIPS, 'nope.mp4'), 0),
            ('not a video', os.path.join(CLIPS, 'not_a_video.mp4'), 0),
            ('corrupt', os.path.join(CLIPS, 'corrupt.mp4'), 0),
            ('dead port', 'http://127.0.0.1:9/x.mp4', 0),
        ]
        results = []
        for name, src, at in cases:
            size = (160, 90) if '160x90' in name else None
            t = time.perf_counter()
            try:
                tex = VideoGstplayer.generate_thumbnail(src, at, size=size)
                err = None
            except Exception as e:  # must never raise
                tex, err = None, repr(e)
            s = round(time.perf_counter() - t, 3)
            results.append(dict(case=name, seconds=s,
                                size=tex.size if tex is not None else None,
                                raised=err))
            print('{:32s} {:7.3f}s {}{}'.format(
                name, s, tex.size if tex is not None else None,
                ' RAISED ' + err if err else ''), flush=True)
        for p in (files[0], hls[0]):
            p.kill()
        out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
            HERE, 'results', 'thumbs.json')
        with open(out, 'w') as f:
            json.dump(dict(meta=dict(platform=platform.platform(),
                                     init_s=round(init_s, 3)),
                           results=results), f, indent=1)
        self.stop()


if __name__ == '__main__':
    ThumbApp().run()
