"""Spikes 2 and 3: the new ``gstplayer`` provider through Kivy's Video widget.

For each clip and render path (``options={'render': ...}``) it checks:

* first frame shown without playing (``on_load`` while state is 'stop');
* the picture: a 16x9 read-back of the video texture, compared between
  paths (a GL texture that isn't really shared reads back black or junk);
* a paused seek updates the picture;
* 50 seeks in 50 frames (a dragged slider) issue few pipeline seeks and end
  at the last target;
* play for N seconds: frame rate, CPU, tick cost;
* looping restarts from EOS; stop() returns to 0.

    KIVY_VIDEO=gstplayer python provider_check.py results/check-windows.json
    PCHECK_PATHS=gl,nv12 PCHECK_CLIPS=test_1080p30.mp4 ...
"""
import json
import os
import platform
import sys
import time

os.environ.setdefault('KIVY_VIDEO', 'gstplayer')
os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.graphics import Fbo, Rectangle, ClearColor, ClearBuffers  # noqa: E402
from kivy.logger import Logger  # noqa: E402
from kivy.uix.video import Video  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, 'clips')
PATHS = os.environ.get('PCHECK_PATHS', 'gl,nv12,rgba').split(',')
CLIPS_TO_RUN = os.environ.get(
    'PCHECK_CLIPS',
    'big_buck_bunny_360_10s.mp4,test_1080p30.mp4,hevc_2160p30.mp4').split(',')
PLAY_SECONDS = float(os.environ.get('PCHECK_PLAY', '5'))


def readback(texture):
    """Mean RGB of a 16x9 downscale of `texture`, plus the 144 pixels."""
    if texture is None:
        return None
    fbo = Fbo(size=(16, 9))
    with fbo:
        ClearColor(0, 0, 0, 1)
        ClearBuffers()
        Rectangle(size=(16, 9), texture=texture)
    fbo.draw()
    px = fbo.pixels
    rgb = [(px[i], px[i + 1], px[i + 2]) for i in range(0, len(px), 4)]
    mean = tuple(round(sum(c[k] for c in rgb) / len(rgb), 1) for k in range(3))
    return dict(mean=mean, pixels=rgb)


def diff(a, b):
    if not a or not b:
        return None
    return round(sum(abs(x[k] - y[k]) for x, y in zip(a['pixels'], b['pixels'])
                     for k in range(3)) / (3 * len(a['pixels'])), 2)


class Check:
    """A coroutine-style sequence driven by Clock callbacks."""

    def __init__(self, app, clip, path):
        self.app, self.clip, self.path = app, clip, path
        self.r = dict(clip=clip, path=path, events=[])
        self.t0 = time.perf_counter()

    def log(self, what, **kw):
        self.r['events'].append(dict(t=round(time.perf_counter() - self.t0, 3),
                                     what=what, **kw))

    def start(self):
        src = os.path.join(CLIPS, self.clip)
        self.v = Video(source=src, state='stop',
                       options={'render': self.path})
        self.app.root_widget.clear_widgets()
        self.app.root_widget.add_widget(self.v)
        # The widget creates its core video on the next frame.
        self.wait(lambda: self.v._video is not None, 5, self.attach)

    def attach(self, ok):
        if not ok:
            self.r['error'] = 'no core video'
            return self.finish()
        core = self.v._video
        self.core = core
        self.r['provider'] = type(core).__name__
        self.issued = 0
        orig = core._issue_seek

        def counting_issue(target):
            self.issued += 1
            return orig(target)
        core._issue_seek = counting_issue
        core.bind(on_load=lambda *a: self.log('on_load', state=core.state),
                  on_eos=lambda *a: self.log('on_eos'),
                  on_error=lambda c, e: self.log('on_error', error=e),
                  on_buffering=lambda c, b: self.log('on_buffering', value=b))
        self.frames = 0
        core.bind(on_frame=self._count)
        self.wait(lambda: core.texture is not None, 10, self.loaded)

    def _count(self, *a):
        self.frames += 1

    def wait(self, cond, timeout, then, step=None):
        t_end = time.perf_counter() + timeout

        def poll(dt):
            if step:
                step()
            if cond():
                then(True)
                return False
            if time.perf_counter() > t_end:
                then(False)
                return False
        Clock.schedule_interval(poll, 0)

    def after(self, seconds, then):
        Clock.schedule_once(lambda dt: then(), seconds)

    # -- steps --

    def loaded(self, ok):
        core = self.core
        self.r['first_frame_without_play'] = ok and core.state == ''
        self.r['load_s'] = round(time.perf_counter() - self.t0, 3)
        if not ok:
            self.r['error'] = 'no first frame'
            return self.finish()
        p = core._player
        self.r['render_path'] = core._path
        self.r['gl'] = (core._gl.platform, core._gl.api) if core._gl else None
        self.r['describe'] = p.describe() if p else None
        self.r['size'] = core.texture.size
        self.r['duration'] = core.duration
        self.after(0.3, self.first_readback)

    def first_readback(self):
        self.r['first'] = readback(self.core.texture)
        self.r['first_mean'] = self.r['first']['mean']
        # Paused seek to the middle: the picture must change.
        self.frames_before = self.frames
        self.core.seek(0.5)
        t = time.perf_counter()
        self.wait(lambda: self.frames > self.frames_before, 5,
                  lambda ok: self.paused_seek_done(ok, t))

    def paused_seek_done(self, ok, t):
        self.r['paused_seek_updates'] = ok
        self.r['paused_seek_s'] = round(time.perf_counter() - t, 3)
        self.after(0.2, self.paused_seek_check)

    def paused_seek_check(self):
        mid = readback(self.core.texture)
        self.r['mid_mean'] = mid['mean']
        self.r['paused_seek_picture_diff'] = diff(self.r['first'], mid)
        self.r['position_after_paused_seek'] = round(self.core.position, 2)
        # A dragged slider: one seek per frame for 50 frames.
        self.issued = 0
        self.n = 0
        self.max_in_flight = 0

        def step():
            if self.n < 50:
                self.core.seek(0.1 + 0.6 * self.n / 49)
                self.n += 1
            q = self.core._seeks
            self.max_in_flight = max(self.max_in_flight,
                                     int(q.in_flight is not None))
        t = time.perf_counter()
        self.wait(lambda: self.n >= 50 and self.core._seeks.pending is None,
                  10, lambda ok: self.burst_done(ok, t), step=step)

    def burst_done(self, ok, t):
        self.r['burst_settled'] = ok
        self.r['burst_s'] = round(time.perf_counter() - t, 3)
        self.r['burst_pipeline_seeks'] = self.issued
        dur = self.core.duration
        self.r['burst_final_position'] = round(self.core.position, 2)
        self.r['burst_target'] = round(0.7 * dur, 2)
        # Play from the start.
        self.core.seek(0)
        self.after(0.5, self.play)

    def play(self):
        self.frames0 = self.frames
        self.cpu0, self.wall0 = time.process_time(), time.perf_counter()
        self.v.state = 'play'
        self.after(PLAY_SECONDS, self.played)

    def played(self):
        wall = time.perf_counter() - self.wall0
        self.r['play_fps'] = round((self.frames - self.frames0) / wall, 1)
        self.r['play_cpu_pct'] = round(
            100 * (time.process_time() - self.cpu0) / wall, 1)
        self.r['play_readback_mean'] = readback(self.core.texture)['mean']
        # Loop: from near the end, EOS must restart at 0 and keep playing.
        self.core.eos = 'loop'
        self.eos_seen = False
        self.v.state = 'play'
        self.core.bind(on_eos=self._eos)
        self.core.seek(max(0.0, 1 - 1.5 / max(self.core.duration, 2)))
        t = time.perf_counter()
        self.wait(lambda: self.eos_seen and self.core.position < 1.0 and
                  self.core.state == 'playing', 8,
                  lambda ok: self.looped(ok, t))

    def _eos(self, *a):
        self.eos_seen = True

    def looped(self, ok, t):
        self.r['loop_restarts'] = ok
        self.r['loop_s'] = round(time.perf_counter() - t, 3)
        self.frames1 = self.frames
        self.after(1.0, self.loop_continues)

    def loop_continues(self):
        self.r['frames_after_loop_1s'] = self.frames - self.frames1
        self.v.state = 'stop'
        self.after(0.8, self.stopped)

    def stopped(self):
        self.r['position_after_stop'] = round(self.core.position, 2)
        self.r['state_after_stop'] = self.core.state
        self.finish()

    def finish(self):
        self.r.pop('first', None)
        self.r['events'] = self.r['events'][:40]
        try:
            self.v.unload()
        except Exception as e:
            self.r['unload_error'] = repr(e)
        self.app.next_check(self.r)


class CheckApp(App):
    def build(self):
        from kivy.uix.floatlayout import FloatLayout
        self.root_widget = FloatLayout()
        self.results = []
        self.queue = [(c, p) for c in CLIPS_TO_RUN for p in PATHS]
        Clock.schedule_once(lambda dt: self.next_check(None), 0.5)
        return self.root_widget

    def next_check(self, result):
        if result is not None:
            self.results.append(result)
            Logger.info('Check: %s', json.dumps(
                {k: v for k, v in result.items() if k != 'events'},
                default=str))
        if not self.queue:
            return self.done()
        clip, path = self.queue.pop(0)
        Clock.schedule_once(lambda dt: Check(self, clip, path).start(), 0.3)

    def done(self):
        from kivy.lib.gstplayer import get_runtime_info
        from kivy.graphics.opengl import glGetString, GL_VERSION, GL_RENDERER
        meta = dict(platform=platform.platform(), machine=platform.machine(),
                    python=platform.python_version(),
                    gst=get_runtime_info(),
                    gl_version=str(glGetString(GL_VERSION)),
                    gl_renderer=str(glGetString(GL_RENDERER)),
                    kivy_gl_backend=os.environ.get('KIVY_GL_BACKEND'),
                    window=type(Window).__name__,
                    sdl_video_driver=os.environ.get('SDL_VIDEO_DRIVER'))
        out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
            HERE, 'results', 'provider-check.json')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, 'w') as f:
            json.dump(dict(meta=meta, results=self.results), f, indent=1,
                      default=str)
        Logger.info('Check: wrote %s', out)
        self.stop()


if __name__ == '__main__':
    CheckApp().run()
