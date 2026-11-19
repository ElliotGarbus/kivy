"""gi-free GStreamer player core: ctypes straight onto libgstreamer.

Proves the provider doesn't need PyGObject at all: the same poll-per-tick
design as gst_headless.py (playbin3 + appsink, Gst.Bus.pop(),
appsink.try_pull_sample(0)) using only the stable GStreamer 1.x C ABI,
loaded at *runtime*. A real provider would do this in Cython with
dlopen()/LoadLibrary instead of ctypes, so Kivy's wheels (manylinux, Pi,
Windows, macOS) need no GStreamer at build time and pick up whatever
runtime is installed: distro packages on Linux, the official installer or
the official ``gstreamer-libs``/``gstreamer-plugins`` wheels on Windows and
macOS.

    python gst_nogi.py [clip-or-uri] [seconds]
"""
import ctypes
import ctypes.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

P = ctypes.c_void_p
GST_STATE_NULL, GST_STATE_PAUSED, GST_STATE_PLAYING = 1, 3, 4
GST_FORMAT_TIME = 3
GST_MAP_READ = 1
MSG_EOS, MSG_ERROR, MSG_WARNING, MSG_BUFFERING = 1 << 0, 1 << 1, 1 << 2, 1 << 5


class GError(ctypes.Structure):
    _fields_ = [('domain', ctypes.c_uint32), ('code', ctypes.c_int),
                ('message', ctypes.c_char_p)]


class GstMapInfo(ctypes.Structure):
    _fields_ = [('memory', P), ('flags', ctypes.c_int), ('data', P),
                ('size', ctypes.c_size_t), ('maxsize', ctypes.c_size_t),
                ('user_data', P * 4), ('_gst_reserved', P * 4)]


class GstMiniObject(ctypes.Structure):
    _fields_ = [('type', ctypes.c_size_t), ('refcount', ctypes.c_int),
                ('lockstate', ctypes.c_int), ('flags', ctypes.c_uint),
                ('copy', P), ('dispose', P), ('free', P),
                ('priv_uint', ctypes.c_uint), ('priv_pointer', P)]


class GstMessage(ctypes.Structure):
    _fields_ = [('mini_object', GstMiniObject), ('type', ctypes.c_int),
                ('timestamp', ctypes.c_uint64), ('src', P),
                ('seqnum', ctypes.c_uint32)]


def _load(name):
    if sys.platform == 'win32':
        # Official wheels: their .pth only prepends PATH, which Python 3.8+
        # ignores for DLL lookup (PyGObject copes via PYGI_DLL_DIRS). A
        # non-gi consumer must register the dirs itself; gstreamer_env()
        # returns them.
        try:
            import gstreamer_libs
            for d in gstreamer_libs.gstreamer_env()[1].split(os.pathsep):
                if d and os.path.isdir(d):
                    os.add_dll_directory(d)
        except ImportError:
            pass
        return ctypes.CDLL(f'{name}-1.0-0.dll')
    path = ctypes.util.find_library(f'{name}-1.0') or f'lib{name}-1.0.so.0'
    return ctypes.CDLL(path)


gst = _load('gstreamer')
gstapp = _load('gstapp')
gobj = ctypes.CDLL('gobject-2.0-0.dll' if sys.platform == 'win32'
                   else ctypes.util.find_library('gobject-2.0'))

for fn, res, args in [
    ('gst_init_check', ctypes.c_int, [P, P, P]),
    ('gst_version_string', ctypes.c_char_p, []),
    ('gst_element_factory_make', P, [ctypes.c_char_p, ctypes.c_char_p]),
    ('gst_element_get_bus', P, [P]),
    ('gst_element_set_state', ctypes.c_int, [P, ctypes.c_int]),
    ('gst_element_query_position', ctypes.c_int,
     [P, ctypes.c_int, ctypes.POINTER(ctypes.c_int64)]),
    ('gst_element_query_duration', ctypes.c_int,
     [P, ctypes.c_int, ctypes.POINTER(ctypes.c_int64)]),
    ('gst_bus_pop', ctypes.POINTER(GstMessage), [P]),
    ('gst_message_parse_error', None,
     [P, ctypes.POINTER(ctypes.POINTER(GError)), ctypes.POINTER(ctypes.c_char_p)]),
    ('gst_message_parse_buffering', None, [P, ctypes.POINTER(ctypes.c_int)]),
    ('gst_mini_object_unref', None, [P]),
    ('gst_object_unref', None, [P]),
    ('gst_caps_from_string', P, [ctypes.c_char_p]),
    ('gst_sample_get_buffer', P, [P]),
    ('gst_buffer_map', ctypes.c_int, [P, ctypes.POINTER(GstMapInfo), ctypes.c_int]),
    ('gst_buffer_unmap', None, [P, ctypes.POINTER(GstMapInfo)]),
    ('gst_filename_to_uri', ctypes.c_char_p, [ctypes.c_char_p, P]),
    ('gst_core_error_quark', ctypes.c_uint32, []),
]:
    f = getattr(gst, fn)
    f.restype, f.argtypes = res, args
gstapp.gst_app_sink_try_pull_sample.restype = P
gstapp.gst_app_sink_try_pull_sample.argtypes = [P, ctypes.c_uint64]
gobj.g_object_set.restype = None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, 'clips', 'test_1080p30.mp4')
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6
    if not gst.gst_init_check(None, None, None):
        sys.exit('gst_init_check failed')
    print('no-gi:', gst.gst_version_string().decode())
    uri = src.encode() if '://' in src else \
        gst.gst_filename_to_uri(os.path.abspath(src).encode(), None)

    playbin = gst.gst_element_factory_make(b'playbin3', None)
    sink = gst.gst_element_factory_make(b'appsink', None)
    caps = gst.gst_caps_from_string(b'video/x-raw,format=NV12')
    gobj.g_object_set(P(sink), b'caps', P(caps), b'max-buffers', ctypes.c_uint(1),
                      b'drop', ctypes.c_int(1), b'emit-signals', ctypes.c_int(0), None)
    gst.gst_mini_object_unref(caps)
    gobj.g_object_set(P(playbin), b'uri', ctypes.c_char_p(uri),
                      b'video-sink', P(sink), None)
    bus = gst.gst_element_get_bus(playbin)
    gst.gst_element_set_state(playbin, GST_STATE_PLAYING)

    t0 = time.perf_counter()
    frames, err, eos, tick_us = 0, None, False, []
    core_q, GST_CORE_ERROR_STATE_CHANGE = gst.gst_core_error_quark(), 4
    while time.perf_counter() - t0 < secs and not (err or eos):
        t = time.perf_counter()
        errors = []
        while True:  # drain the bus
            msg = gst.gst_bus_pop(bus)
            if not msg:
                break
            mtype = msg.contents.type
            if mtype == MSG_ERROR:
                e, dbg = ctypes.POINTER(GError)(), ctypes.c_char_p()
                gst.gst_message_parse_error(msg, ctypes.byref(e), ctypes.byref(dbg))
                generic = (e.contents.domain == core_q and
                           e.contents.code == GST_CORE_ERROR_STATE_CHANGE)
                errors.append((generic, e.contents.message.decode()))
            elif mtype == MSG_EOS:
                eos = True
            gst.gst_mini_object_unref(msg)
        if errors:
            # Several ERRORs can land in one tick, and playbin's generic
            # "state change failed" may arrive before the element's own
            # reason: report the most specific one (stable sort keeps order).
            err = sorted(errors, key=lambda x: x[0])[0][1]
        sample = gstapp.gst_app_sink_try_pull_sample(sink, 0)
        if sample:
            buf = gst.gst_sample_get_buffer(sample)
            info = GstMapInfo()
            if gst.gst_buffer_map(buf, ctypes.byref(info), GST_MAP_READ):
                # A provider would hand info.data/info.size to blit_buffer
                # (or, in Cython, straight to glTexSubImage2D).
                gst.gst_buffer_unmap(buf, ctypes.byref(info))
            gst.gst_mini_object_unref(sample)
            frames += 1
        tick_us.append((time.perf_counter() - t) * 1e6)
        time.sleep(max(0, 1 / 60 - (time.perf_counter() - t)))
    pos, dur = ctypes.c_int64(), ctypes.c_int64()
    gst.gst_element_query_position(playbin, GST_FORMAT_TIME, ctypes.byref(pos))
    gst.gst_element_query_duration(playbin, GST_FORMAT_TIME, ctypes.byref(dur))
    gst.gst_element_set_state(playbin, GST_STATE_NULL)
    gst.gst_object_unref(bus)
    gst.gst_object_unref(playbin)
    tick_us.sort()
    print(f'frames={frames} pos={pos.value / 1e9:.2f}s dur={dur.value / 1e9:.2f}s '
          f'eos={eos} error={err!r} tick_p50={tick_us[len(tick_us) // 2]:.0f}us')


if __name__ == '__main__':
    main()
