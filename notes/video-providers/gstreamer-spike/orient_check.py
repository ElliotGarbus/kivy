"""Spike 9: rotation, pixel aspect ratio and colour, per render path.

Clips are generated with GStreamer (``videotestsrc`` SMPTE bars, which are
asymmetric left/right and top/bottom):

* one MP4 per ``image-orientation`` value (``taginject`` before ``mp4mux``,
  which writes it as the track matrix; ``qtdemux`` reads it back);
* WebM with 2:1 and 1:2 pixels;
* WebM tagged BT.601, BT.709 and full-range BT.601.

For each clip and path the provider's first frame is read back on a 16x16
grid and compared with GStreamer's own rendering of the same frame
(``videoflip video-direction=auto`` + ``videoconvert`` to RGBA, the pixel
aspect ratio applied by scaling). Mean absolute difference per channel, in
levels (0-255); and the texture size against the expected display size.

    KIVY_VIDEO=gstplayer python orient_check.py results/orient-windows.json
"""
import json
import os
import platform
import sys
import tempfile
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')
from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.graphics import Fbo, Rectangle, ClearColor, ClearBuffers  # noqa: E402
from kivy.graphics.texture import Texture  # noqa: E402
from kivy.uix.widget import Widget  # noqa: E402

PATHS = os.environ.get('OCHECK_PATHS', 'gl,nv12,rgba').split(',')
ORIENTATIONS = ['rotate-0', 'rotate-90', 'rotate-180', 'rotate-270',
                'flip-rotate-0', 'flip-rotate-90', 'flip-rotate-180',
                'flip-rotate-270']
W, H = 320, 180


def grid(texture, n=16):
    fbo = Fbo(size=(n, n))
    with fbo:
        ClearColor(0, 0, 0, 1)
        ClearBuffers()
        Rectangle(size=(n, n), texture=texture)
    fbo.draw()
    px = fbo.pixels
    return [tuple(px[i:i + 3]) for i in range(0, len(px), 4)]


def diff(a, b):
    return round(sum(abs(x[k] - y[k]) for x, y in zip(a, b) for k in range(3))
                 / (3.0 * len(a)), 2)


class OrientApp(App):
    def build(self):
        Clock.schedule_once(self.start, 0.5)
        return Widget()

    def make_clips(self, g):
        d = tempfile.mkdtemp(prefix='kivy-orient-')
        clips = []
        enc = None
        for e in ('x264enc', 'openh264enc', 'vp9enc'):
            if g.element_available(e):
                enc = e
                break
        parse = {'x264enc': 'h264parse', 'openh264enc': 'h264parse',
                 'vp9enc': 'vp9parse'}[enc]
        encopts = {'x264enc': 'speed-preset=ultrafast', 'openh264enc': '',
                   'vp9enc': 'deadline=1'}[enc]
        src = ('videotestsrc num-buffers=15 pattern=smpte ! '
               'video/x-raw,width={},height={},framerate=30/1 ! '
               'videoconvert ! video/x-raw,format=I420'.format(W, H))
        for o in ORIENTATIONS:
            path = os.path.join(d, o + '.mp4').replace('\\', '/')
            err = g.run_pipeline(
                '{} ! taginject tags="image-orientation={}" ! {} {} ! {} ! '
                'mp4mux ! filesink location="{}"'.format(
                    src, o, enc, encopts, parse, path))
            clips.append(dict(name=o, path=path, error=err, kind='orient',
                              orientation=o))
        for par in ('2/1', '1/2'):
            path = os.path.join(d, 'par-{}.webm'.format(
                par.replace('/', '_'))).replace('\\', '/')
            err = g.run_pipeline(
                'videotestsrc num-buffers=15 pattern=smpte ! '
                'video/x-raw,width={},height={},framerate=30/1,'
                'pixel-aspect-ratio={} ! videoconvert ! vp8enc deadline=1 ! '
                'webmmux ! filesink location="{}"'.format(W, H, par, path))
            clips.append(dict(name='par ' + par, path=path, error=err,
                              kind='par', par=par))
        for c in ('bt601', 'bt709', '1:4:0:0'):
            path = os.path.join(d, 'colour-{}.webm'.format(
                c.replace(':', '_'))).replace('\\', '/')
            err = g.run_pipeline(
                'videotestsrc num-buffers=15 pattern=smpte ! '
                # Generated in RGB, so videoconvert does the conversion to
                # the tagged colorimetry (videotestsrc's own YUV output is
                # limited range whatever the caps say).
                'video/x-raw,format=RGB,width=320,height=240,framerate=30/1 ! '
                'videoconvert ! video/x-raw,format=I420,colorimetry={} ! '
                'vp8enc deadline=1 ! webmmux ! filesink location="{}"'.format(
                    c, path))
            clips.append(dict(name='colour ' + c, path=path, error=err,
                              kind='colour', colorimetry=c))
        # The source itself, lossless and in RGB: what the colour clips
        # should look like.
        self.png = os.path.join(d, 'smpte.png').replace('\\', '/')
        self.png_error = g.run_pipeline(
            'videotestsrc num-buffers=1 pattern=smpte ! '
            'video/x-raw,width=320,height=240,format=RGB ! pngenc ! '
            'filesink location="{}"'.format(self.png))
        return clips, enc

    def reference(self, g, path):
        '''GStreamer's own rendering: videoflip (auto) + videoconvert, the
        pixel aspect ratio applied by drawing at the display size.'''
        from kivy.lib.gstplayer._policy import display_size
        p = g.GstPlayer(g.filename_to_uri(path), sink='rgba', audio=False,
                        flip=True)
        p.build()
        p.set_state(g.STATE_PAUSED)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if any(m[0] == 'async-done' for m in p.pop_messages()):
                break
            time.sleep(0.005)
        if not p.pull(preroll=True):
            p.teardown()
            return None, None, None
        tex = Texture.create(size=(p.width, p.height), colorfmt='rgba')
        tex.flip_vertical()
        p.upload_rgba(tex)
        size = display_size(p.width, p.height, p.par_n, p.par_d)
        info = dict(par=(p.par_n, p.par_d))
        p.teardown()
        return grid(tex), size, info

    def start(self, dt):
        from kivy.lib import gstplayer as g
        g.init_wait(120)
        self.g = g
        self.clips, self.encoder = self.make_clips(g)
        self.queue = [(c, p) for c in self.clips if not c['error']
                      for p in PATHS]
        self.results = []
        Clock.schedule_once(self.next, 0)

    def next(self, *a):
        from kivy.core.video.video_gstplayer import VideoGstplayer
        if not self.queue:
            return self.done()
        clip, path = self.queue.pop(0)
        ref, ref_size, info = self.reference(self.g, clip['path'])
        v = VideoGstplayer(filename=clip['path'], options={'render': path})
        t_end = time.monotonic() + 10

        def poll(dt):
            if v.texture is None and time.monotonic() < t_end:
                return True
            # one more frame for the orientation tag to land
            Clock.schedule_once(lambda dt: finish(), 0.3)
            return False

        def finish():
            r = dict(clip=clip['name'], path=path,
                     render_path=v._path, orientation_seen=v._orientation)
            r.update(info or {})
            # The decoder's caps as the provider saw them (the reference's
            # are its RGBA output).
            if v._player is not None:
                r['caps_colorimetry'] = v._player.colorimetry
                r['format'] = v._player.format
            if v.texture is None or ref is None:
                r['error'] = 'no frame'
            else:
                r['size'] = tuple(v.texture.size)
                r['expected_size'] = tuple(ref_size)
                r['size_ok'] = r['size'] == r['expected_size']
                r['diff'] = diff(grid(v.texture), ref)
                if clip['kind'] == 'colour' and not self.png_error:
                    from kivy.core.image import Image as CoreImage
                    src = CoreImage(self.png).texture
                    r['diff_vs_source'] = diff(grid(v.texture), grid(src))
                    r['ref_vs_source'] = diff(ref, grid(src))
            v.unload()
            self.results.append(r)
            print('{:18s} {:5s} {:5s} size={} expected={} diff={} seen={} '
                  'vs_source={} ref_vs_source={} caps={}'.format(
                      r['clip'], path, str(r.get('render_path')),
                      r.get('size'), r.get('expected_size'), r.get('diff'),
                      r.get('orientation_seen'), r.get('diff_vs_source'),
                      r.get('ref_vs_source'), r.get('caps_colorimetry')),
                  flush=True)
            Clock.schedule_once(self.next, 0)
        Clock.schedule_interval(poll, 0)

    def done(self):
        out = sys.argv[1] if len(sys.argv) > 1 else 'orient.json'
        with open(out, 'w') as f:
            json.dump(dict(meta=dict(platform=platform.platform(),
                                     encoder=self.encoder,
                                     gst=self.g.get_runtime_info()[0],
                                     clip_errors={c['name']: c['error']
                                                  for c in self.clips
                                                  if c['error']}),
                           results=self.results), f, indent=1)
        print('clip errors:', {c['name']: c['error'] for c in self.clips
                               if c['error']})
        self.stop()


if __name__ == '__main__':
    OrientApp().run()
