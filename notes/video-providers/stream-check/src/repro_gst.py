"""Kivy-free repro: stock playbin3 with default sinks, driven through ctypes.

    python repro_gst.py URI [PLAYBIN_PROPERTIES]

Plays 8 s, pauses 3 s, resumes 12 s, seeks to 50 % (flush + key unit), plays
15 s. Prints the position each second, flags a stall (PLAYING but the
position hasn't moved for 3 s), and prints bus errors and EOS.
PLAYBIN_PROPERTIES is appended to the launch line, e.g.
'audio-sink="fakesink sync=true"'. Uses the gstreamer-libs wheel's
environment (pip install gstreamer-libs gstreamer-plugins ...).
"""

import ctypes
import os
import sys
import time
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_int64, \
    c_uint32, c_uint64, c_void_p

import gstreamer_libs

env, dll_dirs = gstreamer_libs.gstreamer_env()
os.environ.update(env)
for d in dll_dirs.split(os.pathsep):
    if d and os.path.isdir(d):
        os.add_dll_directory(d)

gst = ctypes.CDLL("gstreamer-1.0-0.dll")
glib = ctypes.CDLL("glib-2.0-0.dll")


class GError(Structure):
    _fields_ = [("domain", c_uint32), ("code", c_int), ("message", c_char_p)]


gst.gst_init.argtypes = [c_void_p, c_void_p]
gst.gst_version_string.restype = c_char_p
gst.gst_parse_launch.argtypes = [c_char_p, POINTER(POINTER(GError))]
gst.gst_parse_launch.restype = c_void_p
gst.gst_element_set_state.argtypes = [c_void_p, c_int]
gst.gst_element_get_bus.argtypes = [c_void_p]
gst.gst_element_get_bus.restype = c_void_p
gst.gst_bus_timed_pop_filtered.argtypes = [c_void_p, c_uint64, c_int]
gst.gst_bus_timed_pop_filtered.restype = c_void_p
gst.gst_message_parse_error.argtypes = [
    c_void_p, POINTER(POINTER(GError)), POINTER(c_char_p)]
gst.gst_mini_object_unref.argtypes = [c_void_p]
gst.gst_element_query_position.argtypes = [c_void_p, c_int, POINTER(c_int64)]
gst.gst_element_query_duration.argtypes = [c_void_p, c_int, POINTER(c_int64)]
gst.gst_element_seek_simple.argtypes = [c_void_p, c_int, c_int, c_int64]

NULL_, PAUSED, PLAYING = 1, 3, 4
MSG_EOS, MSG_ERROR = 1, 2
FORMAT_TIME = 3
SEEK_FLUSH, SEEK_KEY_UNIT = 1, 4
SECOND = 1000000000

uri = sys.argv[1]
extra = sys.argv[2] if len(sys.argv) > 2 else ""
gst.gst_init(None, None)
print("REPRO", gst.gst_version_string().decode(), uri, extra, flush=True)
err = POINTER(GError)()
pipeline = gst.gst_parse_launch(
    "playbin3 uri={} {}".format(uri, extra).encode(), byref(err))
if not pipeline:
    sys.exit("parse failed: " + err.contents.message.decode())
bus = gst.gst_element_get_bus(pipeline)


def position():
    v = c_int64(-1)
    gst.gst_element_query_position(pipeline, FORMAT_TIME, byref(v))
    return v.value / SECOND


def duration():
    v = c_int64(-1)
    gst.gst_element_query_duration(pipeline, FORMAT_TIME, byref(v))
    return v.value / SECOND


def drain():
    while True:
        m = gst.gst_bus_timed_pop_filtered(bus, 0, MSG_ERROR)
        if not m:
            break
        e, dbg = POINTER(GError)(), c_char_p()
        gst.gst_message_parse_error(m, byref(e), byref(dbg))
        print("REPRO ERROR", e.contents.message.decode(),
              "|", (dbg.value or b"").decode(errors="replace"), flush=True)
        gst.gst_mini_object_unref(m)
        return True
    m = gst.gst_bus_timed_pop_filtered(bus, 0, MSG_EOS)
    if m:
        print("REPRO EOS at", round(position(), 1), flush=True)
        gst.gst_mini_object_unref(m)
        return True
    return False


def run(seconds, label, playing):
    t_end = time.monotonic() + seconds
    last, still_since, stalled = None, None, False
    while time.monotonic() < t_end:
        if drain():
            return False
        time.sleep(0.25)
        pos = round(position(), 1)
        now = time.monotonic()
        if playing and pos == last:
            still_since = still_since or now
            if now - still_since > 3 and not stalled:
                stalled = True
                print("REPRO STALL during", label, "at", pos, flush=True)
        else:
            still_since = None
        last = pos
    print("REPRO", label, "ended at", last,
          "STALLED" if stalled else "", flush=True)
    return True


t0 = time.monotonic()
gst.gst_element_set_state(pipeline, PLAYING)
ok = run(8, "play", True)
if ok:
    gst.gst_element_set_state(pipeline, PAUSED)
    ok = run(3, "pause", False)
if ok:
    gst.gst_element_set_state(pipeline, PLAYING)
    ok = run(12, "resume", True)
if ok:
    target = duration() / 2
    print("REPRO seek to", round(target, 1), flush=True)
    gst.gst_element_seek_simple(pipeline, FORMAT_TIME,
                                SEEK_FLUSH | SEEK_KEY_UNIT,
                                int(target * SECOND))
    run(15, "after seek", True)
gst.gst_element_set_state(pipeline, NULL_)
print("REPRO done", flush=True)
