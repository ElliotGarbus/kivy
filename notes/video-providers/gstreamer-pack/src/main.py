"""Spike 8: does a kivyforge-built app play video through the gstplayer
provider with GStreamer bundled (Windows wheels) or from the system (Linux)?

Plays a bundled clip, an HTTPS HLS stream, an HTTPS DASH stream and an
HTTPS 404, then writes a JSON report and quits. The report goes to
PACKCHECK_OUT, else <user_data_dir>/packcheck.json; its path is printed.
"""
import json
import os
import sys
import time

T_START = time.perf_counter()
os.environ.setdefault('KIVY_VIDEO', 'gstplayer')
os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.uix.video import Video  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = [
    ('local', os.path.join(HERE, 'clip.mp4'), 4),
    ('hls-fmp4', 'https://devstreaming-cdn.apple.com/videos/streaming/'
                 'examples/img_bipbop_adv_example_fmp4/master.m3u8', 8),
    ('dash', 'https://dash.akamaized.net/akamai/bbb_30fps/bbb_30fps.mpd', 8),
    ('https-404',
     'https://gstreamer.freedesktop.org/data/does-not-exist.mp4', 6),
]


class PackCheck(App):
    def build(self):
        self.report = dict(frozen=getattr(sys, 'frozen', False),
                           executable=sys.executable, prefix=sys.prefix,
                           import_kivy_s=round(time.perf_counter() - T_START, 3),
                           env={k: os.environ.get(k) for k in (
                               'GST_REGISTRY_1_0', 'GST_PLUGIN_SYSTEM_PATH_1_0',
                               'GIO_EXTRA_MODULES', 'KIVY_GSTREAMER_ROOT')},
                           cases=[])
        self.queue = list(CASES)
        self.video = None
        Clock.schedule_once(self.next, 0.2)
        return Video(state='stop')

    def next(self, *a):
        if self.video is not None:
            self.video.unload()
            self.root.remove_widget(self.video) if self.video is not \
                self.root else None
        if not self.queue:
            return self.done()
        name, src, secs = self.queue.pop(0)
        v = self.root
        rec = dict(case=name, src=src, t_load=time.perf_counter())
        rec['frames'], rec['errors'] = 0, []
        self.rec = rec

        def frame(*a):
            if rec['frames'] == 0:
                rec['first_frame_s'] = round(
                    time.perf_counter() - rec['t_load'], 3)
            rec['frames'] += 1
        v.source = ''
        v.source = src
        v.state = 'play'
        Clock.schedule_once(lambda dt: self.attach(frame), 0)
        Clock.schedule_once(lambda dt: self.finish(rec, secs), secs)

    def attach(self, frame):
        core = self.root._video
        if core is None:
            return Clock.schedule_once(lambda dt: self.attach(frame), 0)
        self.rec['provider'] = type(core).__name__
        core.bind(on_frame=frame,
                  on_error=lambda c, e: self.rec['errors'].append(e))

    def finish(self, rec, secs):
        core = self.root._video
        rec['fps'] = round(rec['frames'] / float(secs), 1)
        if core is not None and hasattr(core, '_path'):
            rec['render_path'] = core._path
            if core._player is not None:
                rec['describe'] = core._player.describe()
        self.report['cases'].append(rec)
        print('packcheck:', json.dumps(
            {k: v for k, v in rec.items() if k != 't_load'}, default=str),
            flush=True)
        self.root.state = 'stop'
        Clock.schedule_once(self.next, 0.3)

    def done(self):
        try:
            from kivy.lib.gstplayer import get_runtime_info, init_seconds
            self.report['gst_runtime'] = get_runtime_info()
            self.report['gst_init_s'] = init_seconds()
        except ImportError as e:
            self.report['gst_runtime'] = 'unavailable: {}'.format(e)
        out = os.environ.get('PACKCHECK_OUT') or os.path.join(
            self.user_data_dir, 'packcheck.json')
        for c in self.report['cases']:
            c.pop('t_load', None)
        with open(out, 'w') as f:
            json.dump(self.report, f, indent=1, default=str)
        print('packcheck: wrote', out, flush=True)
        self.stop()


if __name__ == '__main__':
    PackCheck().run()
