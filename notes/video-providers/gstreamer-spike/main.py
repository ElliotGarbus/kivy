"""GStreamer video provider spike for Kivy 3.0 -- Kivy app.

A provider-shaped ``GstSpikeVideo(VideoBase)`` built on the *official*
GStreamer Python bindings (``gi`` + ``Gst``, from ``pip install
gstreamer-meta`` on Windows/macOS or the distro's python3-gi/gir1.2-gst-*
on Linux), plus a small UI to drive it.

Design under test (see ../GSTREAMER-SPIKE.md):

* ``playbin3`` with an ``appsink`` video sink (``emit-signals=False``).
* Everything happens on Kivy's thread inside ``_update()``: drain the bus
  with ``Gst.Bus.pop()``, pull the newest frame with
  ``appsink.try_pull_sample(0)``. No GLib main loop, no Python on
  GStreamer's streaming threads (except the GL context hand-off below).
* Failures are reported once from ``_update()`` through
  ``VideoBase._do_error()`` (polyfilled when the installed Kivy predates
  kivy/kivy#9528); ``load()`` never raises.

Render modes (``options={'mode': ...}``):

``rgba``  appsink caps ``video/x-raw,format=RGBA``; GStreamer converts on the
          CPU, Kivy uploads with ``Texture.blit_buffer``.
``nv12``  appsink caps ``video/x-raw,format=NV12``; two plane textures
          (luminance + luminance_alpha) and a YUV->RGB shader into an Fbo.
          No CPU colour conversion, 1.5 bytes/pixel uploaded instead of 4.
``gl``    Kivy's own GL context is wrapped as a ``GstGLContext`` and given
          to the pipeline as ``gst.gl.app_context``; the video sink is
          ``glupload ! glcolorconvert ! appsink(memory:GLMemory)``. Kivy
          draws GStreamer's texture id directly -- no Python-side copy or
          upload. (Windows: desktop GL / ``glew`` backend only; the
          official GstGL build has no EGL, so ANGLE can't share.)

Run::

    python main.py                       # interactive
    python main.py --auto results/x.json # benchmark every mode, then quit
"""
import ctypes
import json
import os
import platform
import sys
import time

os.environ.setdefault('KIVY_NO_ARGS', '1')

import gi  # noqa: E402
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('GstGL', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import (  # noqa: E402
    GObject, Gst, GstApp, GstVideo, GstGL, GstPbutils)

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.video import VideoBase  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.graphics import (  # noqa: E402
    Color, Fbo, Rectangle, BindTexture, ClearColor, ClearBuffers)
from kivy.graphics.texture import Texture  # noqa: E402
from kivy.lang import Builder  # noqa: E402
from kivy.logger import Logger  # noqa: E402
from kivy.properties import StringProperty, ObjectProperty  # noqa: E402
from kivy.uix.boxlayout import BoxLayout  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GL_TEXTURE_2D = 0x0DE1

NV12_FS = '''
$HEADER$
uniform sampler2D tex_uv;
uniform float bt709;
void main(void) {
    float y = 1.16438 * (texture2D(texture0, tex_coord0).r - 0.0625);
    vec2 uv = texture2D(tex_uv, tex_coord0).ra - vec2(0.5);
    vec3 rgb;
    if (bt709 > 0.5)
        rgb = vec3(y + 1.79274 * uv.y,
                   y - 0.21325 * uv.x - 0.53291 * uv.y,
                   y + 2.11240 * uv.x);
    else
        rgb = vec3(y + 1.59603 * uv.y,
                   y - 0.39176 * uv.x - 0.81297 * uv.y,
                   y + 2.01723 * uv.x);
    gl_FragColor = vec4(rgb, 1.0);
}
'''

_HAS_ON_ERROR = hasattr(VideoBase, '_do_error')


class _GstC:
    '''ctypes escape hatch for the two GstGL calls PyGObject can't make:

    * gst-python's ``Gst.Memory.map()`` override drops non-core flags, so
      ``GstGL.MAP_GL`` silently becomes a plain READ map -- i.e. a
      glReadPixels download of the whole frame -- instead of returning
      the texture id.
    * ``Gst.Buffer.get_meta()`` returns a base ``Gst.Meta`` that
      ``GstGL.GLSyncMeta.wait`` refuses (``Expected GstGL.GLSyncMeta``).

    Raw pointers come from PyGObject's wrapper layout (boxed: the pointer
    follows PyObject_HEAD; GObject: the ``__gpointer__`` capsule). A real
    provider would do this in Cython against the C API instead.
    '''

    class MapInfo(ctypes.Structure):
        _fields_ = [('memory', ctypes.c_void_p), ('flags', ctypes.c_int),
                    ('data', ctypes.c_void_p), ('size', ctypes.c_size_t),
                    ('maxsize', ctypes.c_size_t),
                    ('user_data', ctypes.c_void_p * 4),
                    ('_gst_reserved', ctypes.c_void_p * 4)]

    def __init__(self):
        ext = {'win32': '-0.dll', 'darwin': '.0.dylib'}.get(sys.platform, '.so.0')
        pre = '' if sys.platform == 'win32' else 'lib'
        gst = ctypes.CDLL(f'{pre}gstreamer-1.0{ext}')
        gl = ctypes.CDLL(f'{pre}gstgl-1.0{ext}')
        P, MI = ctypes.c_void_p, ctypes.POINTER(self.MapInfo)
        gst.gst_memory_map.argtypes = [P, MI, ctypes.c_int]
        gst.gst_memory_unmap.argtypes = [P, MI]
        gst.gst_buffer_get_meta.argtypes = [P, ctypes.c_size_t]
        gst.gst_buffer_get_meta.restype = P
        gl.gst_gl_sync_meta_api_get_type.restype = ctypes.c_size_t
        gl.gst_gl_sync_meta_wait.argtypes = [P, P]
        self.gst, self.gl = gst, gl
        self.sync_api = gl.gst_gl_sync_meta_api_get_type()
        cap = ctypes.pythonapi.PyCapsule_GetPointer
        cap.restype, cap.argtypes = P, [ctypes.py_object, ctypes.c_char_p]
        self._cap = cap

    @staticmethod
    def boxed_ptr(obj):
        return ctypes.c_void_p.from_address(id(obj) + 2 * ctypes.sizeof(
            ctypes.c_void_p)).value

    def gobject_ptr(self, obj):
        return self._cap(obj.__gpointer__, None)

    def tex_id(self, mem):
        mi = self.MapInfo()
        raw = self.boxed_ptr(mem)
        if not self.gst.gst_memory_map(raw, ctypes.byref(mi),
                                       1 | GstGL.MAP_GL):
            return 0
        tid = ctypes.c_uint.from_address(mi.data).value
        self.gst.gst_memory_unmap(raw, ctypes.byref(mi))
        return tid

    def sync_wait(self, buf, ctx):
        meta = self.gst.gst_buffer_get_meta(self.boxed_ptr(buf), self.sync_api)
        if meta:
            self.gl.gst_gl_sync_meta_wait(meta, self.gobject_ptr(ctx))
        return bool(meta)


class _GLHost:
    """Kivy's current GL context, as GstGL needs to see it.

    Windows: WGL (desktop GL; the official GstGL build has no EGL).
    Linux: EGL if a context is current (Wayland, KMSDRM, X11 with
    SDL_VIDEO_X11_FORCE_EGL=1) -- needed for DMABuf -> EGLImage zero-copy --
    else GLX. ``release()`` / ``restore()`` un-current Kivy's context while
    GstGL creates its shared one (mandatory on WGL; GSTSPIKE_GL_RELEASE=0
    skips it, to test whether EGL/GLX need it).
    """

    def __init__(self):
        P = ctypes.c_void_p
        self.platform = None
        if sys.platform == 'win32':
            wgl = ctypes.windll.opengl32
            wgl.wglGetCurrentContext.restype = P
            wgl.wglGetCurrentDC.restype = P
            wgl.wglMakeCurrent.argtypes = [P, P]
            self.ctx = wgl.wglGetCurrentContext()
            if self.ctx:
                self.platform, self.lib = 'wgl', wgl
                self.dc = wgl.wglGetCurrentDC()
            return
        try:
            egl = ctypes.CDLL('libEGL.so.1')
            for fn in ('eglGetCurrentContext', 'eglGetCurrentDisplay'):
                getattr(egl, fn).restype = P
            egl.eglGetCurrentSurface.restype = P
            egl.eglGetCurrentSurface.argtypes = [ctypes.c_int]
            egl.eglMakeCurrent.argtypes = [P, P, P, P]
            egl.eglQueryAPI.restype = ctypes.c_uint
            self.ctx = egl.eglGetCurrentContext()
            if self.ctx:
                self.platform, self.lib = 'egl', egl
                self.dpy = egl.eglGetCurrentDisplay()
                self.draw = egl.eglGetCurrentSurface(0x3059)  # EGL_DRAW
                self.read = egl.eglGetCurrentSurface(0x305A)  # EGL_READ
                return
        except OSError:
            pass
        try:
            glx = ctypes.CDLL('libGL.so.1')
            for fn in ('glXGetCurrentContext', 'glXGetCurrentDisplay',
                       'glXGetCurrentDrawable'):
                getattr(glx, fn).restype = P
            glx.glXMakeCurrent.argtypes = [P, ctypes.c_ulong, P]
            self.ctx = glx.glXGetCurrentContext()
            if self.ctx:
                self.platform, self.lib = 'glx', glx
                self.dpy = glx.glXGetCurrentDisplay()
                self.drawable = glx.glXGetCurrentDrawable()
        except OSError:
            pass

    def gst_display(self):
        if self.platform == 'egl':
            gi.require_version('GstGLEGL', '1.0')
            from gi.repository import GstGLEGL
            return GstGLEGL.GLDisplayEGL.new_with_egl_display(self.dpy)
        if self.platform == 'glx':
            gi.require_version('GstGLX11', '1.0')
            from gi.repository import GstGLX11
            # new_with_display(Display*) isn't introspectable, so PyGObject
            # can't wrap Kivy's own X connection; open a second one by name.
            fn = getattr(GstGLX11.GLDisplayX11, 'new_with_display', None)
            if fn is not None:
                return fn(self.dpy)
            return GstGLX11.GLDisplayX11.new(os.environ.get('DISPLAY'))
        return GstGL.GLDisplay.new()

    def gst_platform(self):
        return {'wgl': GstGL.GLPlatform.WGL, 'egl': GstGL.GLPlatform.EGL,
                'glx': GstGL.GLPlatform.GLX}[self.platform]

    def release(self):
        if self.platform == 'wgl':
            self.lib.wglMakeCurrent(None, None)
        elif self.platform == 'egl':
            self.lib.eglMakeCurrent(self.dpy, None, None, None)
        elif self.platform == 'glx':
            self.lib.glXMakeCurrent(self.dpy, 0, None)

    def restore(self):
        if self.platform == 'wgl':
            self.lib.wglMakeCurrent(self.dc, self.ctx)
        elif self.platform == 'egl':
            self.lib.eglMakeCurrent(self.dpy, self.draw, self.read, self.ctx)
        elif self.platform == 'glx':
            self.lib.glXMakeCurrent(self.dpy, self.drawable, self.ctx)


def _kivy_gl_is_es():
    from kivy.graphics.opengl import glGetString, GL_VERSION
    v = glGetString(GL_VERSION)
    v = v.decode() if isinstance(v, bytes) else str(v)
    return 'OpenGL ES' in v, v


_LEAKED_TEXTURES = []  # Kivy < 3.0 has no Texture(nofree=): never free these


class GstSpikeVideo(VideoBase):
    '''Prototype of the modernized provider.'''

    if not _HAS_ON_ERROR:
        __events__ = VideoBase.__events__ + tuple(
            e for e in ('on_error', 'on_buffering')
            if e not in VideoBase.__events__)

        def on_buffering(self, value):
            pass

        def on_error(self, error):
            pass

        def _do_error(self, error):
            self.stop()
            if self._buffering:
                self._buffering = False
                self.dispatch('on_buffering', False)
            self.dispatch('on_error', error)

    _gst_ready = False

    def __init__(self, **kwargs):
        if not GstSpikeVideo._gst_ready:
            t = time.perf_counter()
            Gst.init(None)
            GstSpikeVideo._gst_ready = True
            Logger.info('GstSpike: Gst.init %.2fs, %s',
                        time.perf_counter() - t, Gst.version_string())
        self.pipeline = None
        self.sink = None
        self.bus = None
        self.stats = {}
        self._reset_state()
        # Kivy < 3.0 VideoBase has no options/buffering; set them before
        # super().__init__(), which calls load().
        self._options = kwargs.get('options', {})
        self._buffering = False
        super().__init__(**kwargs)

    def _reset_state(self):
        self._error_reported = False
        self._pending_error = None
        self._tick_errors = []
        self._missing = []
        self._duration = -1
        self._held_sample = None
        self._gl_tex_cache = {}
        self._fbo = None
        self._ytex = self._uvtex = None
        self._first_frame_t = None
        self.stats = dict(frames=0, update_ms=[], first_frame_s=None,
                          path=None, notes=[])

    # ------------------------------------------------------------ setup

    def load(self):
        self.unload()
        self._reset_state()
        if not self._filename:
            return
        self._t_load = time.perf_counter()
        mode = self._options.get('mode', 'rgba')
        binname = self._options.get('bin', 'playbin3')
        uri = self._filename
        if '://' not in uri:
            uri = Gst.filename_to_uri(os.path.abspath(uri))
        # Nothing in here raises: a failure is remembered and reported by
        # _update() on the next tick, after the caller has bound on_error.
        try:
            pipeline = Gst.ElementFactory.make(binname, None)
            if pipeline is None:
                raise RuntimeError(f'GStreamer has no "{binname}" element')
            pipeline.set_property('uri', uri)
            pipeline.set_property('volume', self._volume)
            if mode == 'gl':
                vsink, self.sink = self._make_gl_sink(pipeline)
            else:
                fmt = 'NV12' if mode == 'nv12' else 'RGBA'
                self.sink = vsink = self._make_appsink(
                    f'video/x-raw,format={fmt}')
            pipeline.set_property('video-sink', vsink)
            self.bus = pipeline.get_bus()
            self.pipeline = pipeline
            self.stats['path'] = mode
            # Preroll -> first frame -> on_load. A synchronous FAILURE
            # (e.g. missing file) still leaves the real ERROR message on
            # the bus, which _update() reports with its own text.
            pipeline.set_state(Gst.State.PAUSED)
        except Exception as e:  # noqa: BLE001 -- reported via on_error
            self._pending_error = str(e)

    def _make_appsink(self, caps):
        sink = Gst.ElementFactory.make('appsink', None)
        sink.set_property('caps', Gst.Caps.from_string(caps))
        sink.set_property('max-buffers', 1)
        sink.set_property('drop', True)
        sink.set_property('sync', True)
        sink.set_property('emit-signals', False)
        sink.set_property('enable-last-sample', False)
        return sink

    def _make_gl_sink(self, pipeline):
        host = _GLHost()
        if host.platform is None:
            raise RuntimeError('gl mode: no current WGL/EGL/GLX context '
                               '(on Windows this needs the desktop-GL "glew" '
                               'backend, not ANGLE)')
        is_es, version = _kivy_gl_is_es()
        api = GstGL.GLAPI.GLES2 if is_es else \
            GstGL.GLAPI.OPENGL | GstGL.GLAPI.OPENGL3
        self._gl_display = host.gst_display()
        self._gl_ctx = GstGL.GLContext.new_wrapped(
            self._gl_display, host.ctx, host.gst_platform(), api)
        if self._gl_ctx is None:
            raise RuntimeError('gl mode: GstGLContext.new_wrapped failed')
        self._gl_ctx.activate(True)
        self._gstc = _GstC()
        if not self._gl_ctx.fill_info():  # raises GLib.Error on failure
            raise RuntimeError('gl mode: GstGLContext.fill_info failed')
        self.stats['notes'].append(
            f'wrapped {host.platform.upper()} context ({version})')
        # Create GStreamer's own (shared) GL context here, on Kivy's thread.
        # WGL can't create a context that shares with one that is current
        # on another thread (every GL version fails), so Kivy's context is
        # released for the duration. Registered on the display,
        # glupload/glcolorconvert reuse it.
        release = os.environ.get('GSTSPIKE_GL_RELEASE', '1') != '0'
        if release:
            host.release()
        try:
            ok, gst_ctx = self._gl_display.create_context(self._gl_ctx)
        finally:
            if release:
                host.restore()
        if not ok or gst_ctx is None:
            raise RuntimeError('gl mode: could not create a GL context '
                               'shared with the Kivy one')
        if not release:
            self.stats['notes'].append('shared context created WITHOUT release')
        self._gl_display.add_context(gst_ctx)
        self._gst_gl_ctx = gst_ctx
        # NEED_CONTEXT is posted on a streaming thread and must be answered
        # synchronously, so this one piece needs a bus sync handler.
        pipeline.get_bus().set_sync_handler(self._on_sync_message)
        sink = self._make_appsink(
            'video/x-raw(memory:GLMemory),format=RGBA,texture-target=2D')
        bin_ = Gst.Bin.new('glvideosink')
        up = Gst.ElementFactory.make('glupload', None)
        conv = Gst.ElementFactory.make('glcolorconvert', None)
        for e in (up, conv, sink):
            bin_.add(e)
        up.link(conv)
        conv.link(sink)
        bin_.add_pad(Gst.GhostPad.new('sink', up.get_static_pad('sink')))
        return bin_, sink

    def _gl_readback(self, tex_id, w, h):
        '''Debug: read the texture back *in Kivy's context* and summarise.'''
        if sys.platform != 'win32':
            return
        gl = ctypes.windll.opengl32
        buf = (ctypes.c_ubyte * (w * h * 4))()
        gl.glBindTexture(GL_TEXTURE_2D, tex_id)
        iw, ih, ifmt = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, 0x1000, ctypes.byref(iw))
        gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, 0x1001, ctypes.byref(ih))
        gl.glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, 0x1003, ctypes.byref(ifmt))
        gl.glGetTexImage(GL_TEXTURE_2D, 0, 0x1908, 0x1401, buf)
        err = gl.glGetError()
        gl.glBindTexture(GL_TEXTURE_2D, 0)
        data = bytes(buf)
        px = [data[i:i + 4].hex() for i in (0, (h // 2 * w + w // 2) * 4)]
        Logger.info('GstSpike: readback tex %d: %dx%d ifmt=%#x err=%#x '
                    'mean=%.1f alpha_mean=%.1f px=%s', tex_id, iw.value,
                    ih.value, ifmt.value, err, sum(data[0::4 * 97]) /
                    len(data[0::4 * 97]), sum(data[3::4 * 97]) /
                    len(data[3::4 * 97]), px)

    def _on_sync_message(self, bus, msg):
        if msg.type == Gst.MessageType.NEED_CONTEXT:
            ok, ctype = msg.parse_context_type()
            Logger.info('GstSpike: NEED_CONTEXT %s from %s', ctype,
                        msg.src.get_name())
            if ctype == GstGL.GL_DISPLAY_CONTEXT_TYPE:
                ctx = Gst.Context.new(ctype, True)
                GstGL.context_set_gl_display(ctx, self._gl_display)
                msg.src.set_context(ctx)
            elif ctype == 'gst.gl.app_context':
                ctx = Gst.Context.new(ctype, True)
                # gst-python 1.28: writable_structure() is a context manager.
                # The value must be typed GstGLContext: set_value() with the
                # bare object stores a plain GObject, which GstGL's typed
                # gst_structure_get() rejects, silently creating an
                # UNSHARED context instead.
                val = GObject.Value(GstGL.GLContext.__gtype__, self._gl_ctx)
                st = ctx.writable_structure()
                if hasattr(st, '__enter__'):   # gst-python >= 1.28
                    with st as s:
                        s.set_value('context', val)
                else:                          # gst-python <= 1.26
                    st.set_value('context', val)
                msg.src.set_context(ctx)
        return Gst.BusSyncReply.PASS

    def unload(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().set_sync_handler(None)
        self.pipeline = self.sink = self.bus = None
        self._held_sample = None
        self._texture = None
        super().unload()

    # ---------------------------------------------------------- control

    def play(self):
        super().play()
        if self.pipeline is not None and not self._buffering:
            self.pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        super().pause()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.PAUSED)

    def stop(self):
        super().stop()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)

    def seek(self, percent, precise=True):
        if self.pipeline is None or self._get_duration() <= 0:
            return
        flags = Gst.SeekFlags.FLUSH | (
            Gst.SeekFlags.ACCURATE if precise else Gst.SeekFlags.KEY_UNIT)
        self.pipeline.seek_simple(
            Gst.Format.TIME, flags,
            int(percent * self._get_duration() * Gst.SECOND))

    def _get_position(self):
        if self.pipeline is None:
            return 0
        ok, pos = self.pipeline.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0

    def _get_duration(self):
        if self.pipeline is None:
            return -1
        if self._duration <= 0:
            ok, dur = self.pipeline.query_duration(Gst.Format.TIME)
            if ok:
                self._duration = dur / Gst.SECOND
        return self._duration

    def _set_volume(self, value):
        self._volume = value
        if self.pipeline is not None:
            self.pipeline.set_property('volume', value)

    # ------------------------------------------------------------- tick

    def _update(self, dt):
        if self._pending_error and not self._error_reported:
            self._report(self._pending_error)
            return
        if self.pipeline is None or self._error_reported:
            return
        t0 = time.perf_counter()
        self._tick_errors = []
        while True:
            msg = self.bus.pop()
            if msg is None:
                break
            self._on_message(msg)
            if self.pipeline is None:
                return
        if self._tick_errors:
            # Drain first, then report once: playbin's generic "state change
            # failed" can precede the element's own, more useful, error.
            self._tick_errors.sort(key=lambda e: e[0])
            self._report(self._tick_errors[0][1])
            return
        sample = self.sink.try_pull_sample(0)
        if sample is not None:
            self._on_sample(sample)
            self.stats['update_ms'].append((time.perf_counter() - t0) * 1000)

    def _report(self, text):
        self._error_reported = True
        Logger.error('GstSpike: <%s>: %s', self._filename, text)
        self._do_error(text)

    def _on_message(self, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            generic = err.matches(Gst.CoreError.quark(),
                                  Gst.CoreError.STATE_CHANGE)
            self._tick_errors.append((generic, self._error_text(msg)))
        elif t == Gst.MessageType.EOS:
            if self._state != '':
                self._do_eos()
        elif t == Gst.MessageType.BUFFERING:
            pct = msg.parse_buffering()
            stalled = pct < 100
            if stalled != self._buffering and self._state == 'playing':
                # Standard GStreamer buffering protocol: hold the pipeline
                # in PAUSED until the queue is full again.
                self.pipeline.set_state(
                    Gst.State.PAUSED if stalled else Gst.State.PLAYING)
                self._buffering = stalled
                self.dispatch('on_buffering', stalled)
        elif t == Gst.MessageType.ELEMENT:
            if GstPbutils.is_missing_plugin_message(msg):
                self._missing.append(
                    GstPbutils.missing_plugin_message_get_description(msg))
        elif t == Gst.MessageType.DURATION_CHANGED:
            self._duration = -1
        elif t == Gst.MessageType.WARNING:
            err, _dbg = msg.parse_warning()
            Logger.warning('GstSpike: %s', err.message)

    def _error_text(self, msg):
        err, dbg = msg.parse_error()
        text = err.message
        if self._missing:
            text = 'missing GStreamer plugin: ' + ', '.join(
                sorted(set(self._missing)))
        elif err.matches(Gst.StreamError.quark(), Gst.StreamError.FAILED) \
                and dbg:
            # "Internal data stream error." alone says nothing; the debug
            # string's last line names the element and the flow return.
            text = f'{text} ({dbg.splitlines()[-1].strip()[-160:]})'
        return text

    # ----------------------------------------------------------- frames

    def _on_sample(self, sample):
        caps = sample.get_caps()
        s = caps.get_structure(0)
        w, h = s.get_value('width'), s.get_value('height')
        mode = self.stats['path']
        first = self._texture is None
        if mode == 'gl':
            self._frame_gl(sample, w, h)
        elif mode == 'nv12':
            self._frame_nv12(sample, caps, w, h)
        else:
            self._frame_rgba(sample, caps, w, h)
        self.stats['frames'] += 1
        if first and self._texture is not None:
            self.stats['first_frame_s'] = round(
                time.perf_counter() - self._t_load, 3)
            self.stats['size'] = (w, h)
            self._describe_pipeline()
            self.dispatch('on_load')
        self.dispatch('on_frame')

    def _describe_pipeline(self):
        '''Record which video decoder got autoplugged and, in gl mode, what
        glupload was fed (memory:DMABuf on Linux = zero-copy import).'''
        it = self.pipeline.iterate_recurse()
        while True:
            ok, el = it.next()
            if ok != Gst.IteratorResult.OK:
                break
            f = el.get_factory()
            if f is None:
                continue
            klass = f.get_metadata('klass') or ''
            if 'Decoder' in klass and 'Video' in klass:
                self.stats['decoder'] = f.get_name()
                pad = el.get_static_pad('src')
                caps = pad.get_current_caps() if pad else None
                self.stats['decoder_out'] = caps.to_string()[:160] if caps else None
            elif f.get_name() == 'glupload':
                caps = el.get_static_pad('sink').get_current_caps()
                self.stats['glupload_in'] = caps.to_string()[:160] if caps else None
        Logger.info('GstSpike: decoder=%s out=%s glupload_in=%s',
                    self.stats.get('decoder'), self.stats.get('decoder_out'),
                    self.stats.get('glupload_in'))

    def _frame_rgba(self, sample, caps, w, h):
        buf = sample.get_buffer()
        vi = GstVideo.VideoInfo.new_from_caps(caps)
        if self._texture is None or self._texture.size != (w, h):
            self._texture = Texture.create(size=(w, h), colorfmt='rgba')
            self._texture.flip_vertical()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            # blit_buffer wants bytes or a *writable* buffer, and the
            # mapped memoryview is read-only, so this is one CPU copy.
            self._texture.blit_buffer(
                bytes(info.data), colorfmt='rgba',
                rowlength=vi.stride[0])  # Kivy's rowlength is in bytes
        finally:
            buf.unmap(info)

    def _frame_nv12(self, sample, caps, w, h):
        buf = sample.get_buffer()
        vi = GstVideo.VideoInfo.new_from_caps(caps)
        meta = GstVideo.buffer_get_video_meta(buf)
        offs = list(meta.offset[:2]) if meta else [vi.offset[0], vi.offset[1]]
        strides = list(meta.stride[:2]) if meta else [vi.stride[0], vi.stride[1]]
        if self._ytex is None or self._ytex.size != (w, h):
            self._ytex = Texture.create(size=(w, h), colorfmt='luminance')
            self._uvtex = Texture.create(size=(w // 2, h // 2),
                                         colorfmt='luminance_alpha')
            self._fbo = Fbo(size=(w, h))
            self._fbo.shader.fs = NV12_FS
            self._fbo['tex_uv'] = 1
            self._fbo['bt709'] = 1.0 if h >= 720 else 0.0
            with self._fbo:
                BindTexture(texture=self._uvtex, index=1)
                Rectangle(size=(w, h), texture=self._ytex,
                          tex_coords=(0, 1, 1, 1, 1, 0, 0, 0))
            self._texture = self._fbo.texture
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            data = bytes(info.data)
            yend = offs[0] + strides[0] * h
            self._ytex.blit_buffer(data[offs[0]:yend], colorfmt='luminance',
                                   rowlength=strides[0])  # bytes
            self._uvtex.blit_buffer(
                data[offs[1]:offs[1] + strides[1] * (h // 2)],
                colorfmt='luminance_alpha', rowlength=strides[1])
        finally:
            buf.unmap(info)
        self._fbo.ask_update()  # Fbo.draw() is a no-op unless flagged
        self._fbo.draw()

    def _frame_gl(self, sample, w, h):
        buf = sample.get_buffer()
        if not self._gstc.sync_wait(buf, self._gl_ctx):  # glWaitSync
            if 'no sync meta' not in self.stats['notes']:
                self.stats['notes'].append('no sync meta')
        mem = buf.peek_memory(0)
        if not GstGL.is_gl_memory(mem):
            if 'not GL memory' not in self.stats['notes']:
                self.stats['notes'].append('not GL memory')
            return
        tex_id = self._gstc.tex_id(mem)
        if not tex_id:
            return
        if os.environ.get('GSTSPIKE_GLDEBUG') and self.stats['frames'] in (5, 60):
            self._gl_readback(tex_id, w, h)
        tex = self._gl_tex_cache.get(tex_id)
        if tex is None or tex.size != (w, h):
            try:
                tex = Texture(width=w, height=h, target=GL_TEXTURE_2D,
                              texid=tex_id, colorfmt='rgba', nofree=True)
            except TypeError:  # Kivy < 3.0
                tex = Texture(width=w, height=h, target=GL_TEXTURE_2D,
                              texid=tex_id, colorfmt='rgba')
                _LEAKED_TEXTURES.append(tex)
            tex.flip_vertical()
            self._gl_tex_cache[tex_id] = tex
        self._texture = tex
        # Keep the buffer (and so its texture) out of the pool until the
        # next frame replaces it.
        self._held_sample = sample


KV = '''
<SpikeRoot>:
    orientation: 'vertical'
    padding: 6
    spacing: 6
    VideoArea:
        id: area
    Label:
        size_hint_y: None
        height: 66
        text: root.status
        text_size: self.width, None
        halign: 'left'
        font_size: 14
    BoxLayout:
        size_hint_y: None
        height: 34
        spacing: 4
        Spinner:
            id: src
            text: 'test_1080p30.mp4'
            values: root.sources
        Spinner:
            id: mode
            text: 'rgba'
            values: ['rgba', 'nv12', 'gl']
            size_hint_x: 0.35
        Spinner:
            id: bin
            text: 'playbin3'
            values: ['playbin3', 'playbin']
            size_hint_x: 0.45
        Button:
            text: 'Load'
            size_hint_x: 0.4
            on_release: root.load(src.text, mode.text, bin.text)
    BoxLayout:
        size_hint_y: None
        height: 34
        spacing: 4
        Button:
            text: 'Play'
            on_release: root.video and root.video.play()
        Button:
            text: 'Pause'
            on_release: root.video and root.video.pause()
        Button:
            text: 'Stop'
            on_release: root.video and root.video.stop()
        Button:
            text: '+10 s'
            on_release: root.seek_rel(10)
        Button:
            text: 'Loop: ' + root.eos_mode
            on_release: root.toggle_loop()
'''


class VideoArea(BoxLayout):
    texture = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            Color(0.1, 0.1, 0.1)
            self._bg = Rectangle()
            Color(1, 1, 1)
            self._rect = Rectangle()
        self.bind(pos=self._layout, size=self._layout, texture=self._layout)

    def _layout(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        tex = self.texture
        self._rect.texture = tex
        if tex is None:
            self._rect.size = (0, 0)
            return
        tw, th = tex.size
        scale = min(self.width / tw, self.height / th)
        w, h = tw * scale, th * scale
        self._rect.size = (w, h)
        self._rect.pos = (self.x + (self.width - w) / 2,
                          self.y + (self.height - h) / 2)


class SpikeRoot(BoxLayout):
    status = StringProperty('pick a source, a mode and Load')
    eos_mode = StringProperty('stop')
    sources = [
        'test_1080p30.mp4', 'test_2160p30.mp4', 'big_buck_bunny_360_10s.mp4',
        'vp9_1080p.webm', 'does_not_exist.mp4', 'not_a_video.mp4',
        'corrupt.mp4',
        'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8',
        'https://devstreaming-cdn.apple.com/videos/streaming/examples/'
        'img_bipbop_adv_example_fmp4/master.m3u8',
        'https://dash.akamaized.net/akamai/bbb_30fps/bbb_30fps.mpd',
        'https://no-such-host.invalid/video.mp4',
    ]
    video = None
    last_error = ''
    last_event = ''

    def load(self, src, mode, binname):
        if self.video is not None:
            self.video.unload()
            self.video.unbind(on_frame=self._on_frame)
        if '://' not in src:
            src = os.path.join(HERE, 'clips', src)
        self.last_error = ''
        self.last_event = 'loading'
        self.video = GstSpikeVideo(
            filename=src, eos=self.eos_mode, fps=60,
            options={'mode': mode, 'bin': binname})
        self.video.bind(on_frame=self._on_frame, on_load=self._on_load,
                        on_error=self._on_error, on_eos=self._on_eos,
                        on_buffering=self._on_buffering)
        self.video.play()
        self._fps_t, self._fps_n, self.fps = time.perf_counter(), 0, 0
        Clock.unschedule(self._refresh)
        Clock.schedule_interval(self._refresh, 0.25)

    def seek_rel(self, secs):
        v = self.video
        if v and v.duration > 0:
            v.seek(min(1, (v.position + secs) / v.duration))

    def toggle_loop(self):
        self.eos_mode = 'loop' if self.eos_mode == 'stop' else 'stop'
        if self.video:
            self.video.eos = self.eos_mode

    def _on_frame(self, video):
        area = self.ids.area
        if area.texture is not video.texture:
            area.texture = video.texture
        area.canvas.ask_update()  # same Texture object, new contents
        self._fps_n += 1

    def _on_load(self, video):
        self.last_event = 'on_load %.3fs' % video.stats['first_frame_s']

    def _on_error(self, video, error):
        self.last_error = error
        self.ids.area.texture = None

    def _on_eos(self, video):
        self.last_event = 'on_eos'

    def _on_buffering(self, video, value):
        self.last_event = f'on_buffering {value}'

    def _refresh(self, dt):
        v = self.video
        if v is None:
            return
        now = time.perf_counter()
        if now - self._fps_t >= 1:
            self.fps = self._fps_n / (now - self._fps_t)
            self._fps_t, self._fps_n = now, 0
        ums = v.stats['update_ms'][-60:]
        self.status = (
            f"[{v.stats['path']}/{v._options.get('bin')}] state={v.state or 'stopped'} "
            f"pos={v.position:.1f}/{v.duration:.1f}s fps={self.fps:.1f} "
            f"update={sum(ums) / max(1, len(ums)):.2f}ms buffering={v._buffering}\n"
            f"event: {self.last_event}   notes: {'; '.join(v.stats['notes'])}\n"
            + (f'on_error: {self.last_error}' if self.last_error else ''))


class SpikeApp(App):
    title = 'GStreamer provider spike'

    def build(self):
        Builder.load_string(KV)
        self.root_w = SpikeRoot()
        if '--auto' in sys.argv:
            Clock.schedule_once(lambda dt: self._auto_start(), 1.0)
        return self.root_w

    # ----------------------------------------------- automated benchmark

    def _auto_start(self):
        out = sys.argv[sys.argv.index('--auto') + 1]
        clips = os.environ.get(
            'GSTSPIKE_CLIPS', 'test_1080p30.mp4,test_2160p30.mp4').split(',')
        modes = os.environ.get('GSTSPIKE_MODES', 'rgba,nv12,gl').split(',')
        self._plan = [(c, m) for c in clips for m in modes]
        self._plan += [('does_not_exist.mp4', 'rgba'), ('corrupt.mp4', 'rgba'),
                       ('https://no-such-host.invalid/video.mp4', 'rgba')]
        self._results, self._out = [], out
        self._next()

    def _next(self, *_):
        if not self._plan:
            import kivy
            try:
                from kivy.graphics.cgl import cgl_get_initialized_backend_name
                cgl = cgl_get_initialized_backend_name()
            except ImportError:
                cgl = None
            sdl_driver = None
            for lib in ('libSDL3.so.0', 'libSDL2-2.0.so.0'):
                try:
                    sdl = ctypes.CDLL(lib)
                    sdl.SDL_GetCurrentVideoDriver.restype = ctypes.c_char_p
                    drv = sdl.SDL_GetCurrentVideoDriver()
                    if drv:
                        sdl_driver = drv.decode()
                        break
                except (OSError, AttributeError):
                    pass
            meta = dict(cgl_backend=cgl, sdl_video_driver=sdl_driver,
                        gl_platform=_GLHost().platform,gst=Gst.version_string(), platform=platform.platform(),
                        python=platform.python_version(), kivy=kivy.__version__,
                        gl=_kivy_gl_is_es()[1], machine=platform.machine(),
                        gl_backend=os.environ.get('KIVY_GL_BACKEND', 'default'),
                        gl_release=os.environ.get('GSTSPIKE_GL_RELEASE', '1'))
            with open(self._out, 'w') as f:
                json.dump(dict(meta=meta, results=self._results), f, indent=1)
            Logger.info('GstSpike: wrote %s', self._out)
            self.stop()
            return
        clip, mode = self._plan.pop(0)
        r = self.root_w
        r.load(clip, mode, 'playbin3')
        self._cpu0, self._wall0 = time.process_time(), time.perf_counter()
        self._cur = (clip, mode)
        secs = 6 if '://' not in clip and 'exist' not in clip             and 'corrupt' not in clip else 3
        Clock.schedule_once(self._shot, secs - 0.5)
        Clock.schedule_once(self._collect, secs)

    def _shot(self, *_):
        clip, mode = self._cur
        if '://' not in clip:
            tag = os.environ.get('GSTSPIKE_TAG') or os.environ.get(
                'KIVY_GL_BACKEND', 'glew')
            name = os.path.join(HERE, 'results', f'shot-{tag}-'
                                f'{os.path.splitext(clip)[0]}-{mode}.png')
            Window.screenshot(name=name)

    def _collect(self, *_):
        clip, mode = self._cur
        v = self.root_w.video
        wall = time.perf_counter() - self._wall0
        ums = sorted(v.stats['update_ms'])
        res = dict(
            clip=clip, mode=mode, frames=v.stats['frames'],
            fps=round(v.stats['frames'] / wall, 1),
            first_frame_s=v.stats['first_frame_s'],
            update_ms_p50=round(ums[len(ums) // 2], 3) if ums else None,
            update_ms_p95=round(ums[int(len(ums) * .95)], 3) if ums else None,
            cpu_pct_one_core=round(
                100 * (time.process_time() - self._cpu0) / wall, 1),
            error=self.root_w.last_error or None,
            decoder=v.stats.get('decoder'),
            decoder_out=v.stats.get('decoder_out'),
            glupload_in=v.stats.get('glupload_in'),
            notes=v.stats['notes'])
        Logger.info('GstSpike: RESULT %s', res)
        self._results.append(res)
        v.unload()
        Clock.schedule_once(self._next, 0.5)


if __name__ == '__main__':
    SpikeApp().run()
