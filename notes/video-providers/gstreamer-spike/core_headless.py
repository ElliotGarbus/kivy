"""Spike 1: the spike's headless cases through Kivy's new GStreamer core.

Uses ``kivy.lib.gstplayer`` (runtime-loaded C ABI, no PyGObject) from the
``gstreamer-video-provider`` branch. No window: frames are pulled and
counted, not uploaded.

Also answers "does first-run initialization block the UI thread?": with
``--fresh-registry`` GStreamer gets an empty registry file, so
``gst_init_check()`` rescans every plugin on the worker thread while this
script runs a 60 Hz loop and records the longest gap between ticks.

    python core_headless.py --all --fresh-registry --json results/core-windows.json
    python core_headless.py --case local-1080p --sink rgba
"""
import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, 'clips')

CASES = {
    'local-360p': ('clip:big_buck_bunny_360_10s.mp4', 12),
    'local-1080p': ('clip:test_1080p30.mp4', 8),
    'local-2160p': ('clip:test_2160p30.mp4', 8),
    'local-vp9': ('clip:vp9_1080p.webm', 6),
    'local-hevc-1080p': ('clip:hevc_1080p30.mp4', 8),
    'local-hevc-2160p': ('clip:hevc_2160p30.mp4', 8),
    'missing-file': ('clip:does_not_exist.mp4', 4),
    'not-a-video': ('clip:not_a_video.mp4', 4),
    'corrupt': ('clip:corrupt.mp4', 6),
    'http-404': ('https://gstreamer.freedesktop.org/data/does-not-exist.mp4', 8),
    'dns-fail': ('https://no-such-host.invalid/video.mp4', 8),
    'hls-ts': ('https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8', 15),
    'hls-fmp4': ('https://devstreaming-cdn.apple.com/videos/streaming/examples/'
                 'img_bipbop_adv_example_fmp4/master.m3u8', 15),
    'dash': ('https://dash.akamaized.net/akamai/bbb_30fps/bbb_30fps.mpd', 15),
    'hls-404': ('https://test-streams.mux.dev/no-such-stream/master.m3u8', 8),
    'hls-server-dies': ('local-hls', 25),
}


class LocalHLS:
    """Serve clips/hls-cmaf (packaged once by pack_hls.py) rate-limited,
    and kill the server on request."""

    def __init__(self):
        self.dir = os.path.join(CLIPS, 'hls-cmaf')
        self.proc = None

    def start(self):
        if not os.path.isfile(os.path.join(self.dir, 'index.m3u8')):
            raise RuntimeError('clips/hls-cmaf missing: run pack_hls.py')
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'slow_http.py'), str(port),
             self.dir, '400000'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        return 'http://127.0.0.1:{}/index.m3u8'.format(port)

    def kill(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


def run_case(g, policy, name, sink, seconds=None, hz=60):
    src, secs = CASES[name]
    secs = seconds or secs
    hls, kill_at, killed_t = None, None, None
    if src == 'local-hls':
        hls = LocalHLS()
        uri = hls.start()
        kill_at = 4.0
    elif src.startswith('clip:'):
        uri = g.filename_to_uri(os.path.join(CLIPS, src[5:]))
    else:
        uri = src
    p = g.GstPlayer(uri, sink=sink)
    res = dict(case=name, sink=sink, uri=uri, frames=0, first_frame_s=None,
               eos=False, error=None, size=None, buffering=[], warnings=[],
               missing=[])
    tick_ms = []
    t0 = time.perf_counter()
    cpu0 = time.process_time()
    try:
        p.build()
        res['set_state'] = p.set_state(g.STATE_PLAYING)
        errors = []
        end = t0 + secs
        drain_left = None
        while time.perf_counter() < end:
            t = time.perf_counter()
            for m in p.pop_messages():
                kind = m[0]
                if kind == 'error':
                    errors.append(policy.BusError(*m[1:]))
                    res.setdefault('raw_errors', []).append(
                        dict(kind=m[1], domain=m[2], code=m[3], message=m[4],
                             http=m[6], src=m[7],
                             debug=(m[5] or '')[-300:],
                             t=round(t - t0, 3)))
                elif kind == 'eos':
                    res['eos'] = True
                elif kind == 'buffering':
                    if not res['buffering'] or res['buffering'][-1][1] != m[1]:
                        res['buffering'].append((round(t - t0, 2), m[1]))
                elif kind == 'missing-plugin':
                    res['missing'].append(m[1])
                elif kind == 'warning':
                    res['warnings'].append(m[1])
            if p.pull():
                res['frames'] += 1
                if res['first_frame_s'] is None:
                    res['first_frame_s'] = round(t - t0, 3)
                    res['size'] = (p.width, p.height)
                    res['format'] = p.format
                    res['colorimetry'] = p.colorimetry
            tick_ms.append((time.perf_counter() - t) * 1000)
            if kill_at and killed_t is None and t - t0 > kill_at:
                hls.kill()
                killed_t = round(t - t0, 2)
            if (errors or res['eos']) and drain_left is None:
                drain_left = 10   # keep draining a little, as the provider would
            if drain_left is not None:
                drain_left -= 1
                if drain_left <= 0:
                    break
            time.sleep(max(0, 1 / hz - (time.perf_counter() - t)))
        if errors:
            res['error'] = policy.error_text(errors, res['missing'])
            if killed_t is not None:
                first_err = res['raw_errors'][0]['t']
                res['error_after_kill_s'] = round(first_err - killed_t, 2)
        res['describe'] = p.describe()
        res['position'] = p.position()
        res['duration'] = p.duration()
    except RuntimeError as e:
        res['error'] = 'build: {}'.format(e)
    finally:
        t = time.perf_counter()
        if os.environ.get('CORE_TRACE'):
            print('  teardown', name, flush=True)
        tmode = os.environ.get('CORE_TEARDOWN', '')
        if tmode.startswith('ready'):
            p.set_state(g.STATE_READY)
        if tmode.endswith('delay'):
            time.sleep(1.0)
        p.teardown()
        if os.environ.get('CORE_TRACE'):
            print('  teardown done', flush=True)
        res['teardown_ms'] = round((time.perf_counter() - t) * 1000, 1)
        if hls:
            hls.kill()
    res['server_killed_at'] = killed_t
    res['cpu_pct_one_core'] = round(
        100 * (time.process_time() - cpu0) / (time.perf_counter() - t0), 1)
    tick_ms.sort()
    if tick_ms:
        res['tick_ms_p50'] = round(tick_ms[len(tick_ms) // 2], 3)
        res['tick_ms_max'] = round(tick_ms[-1], 3)
    return res


def summarize(r):
    print('{case:17s} {sink:4s} first={first_frame_s}s frames={frames} '
          'size={size} eos={eos} cpu={cpu_pct_one_core}% '
          'tick_p50={tick} teardown={teardown_ms}ms'.format(
              tick=r.get('tick_ms_p50'), **r))
    d = r.get('describe') or {}
    if d.get('decoder'):
        print('    decoder:', d['decoder'], '->', (d['decoder_caps'] or '')[:90])
    if r['buffering']:
        print('    buffering:', r['buffering'][:10])
    if r['error']:
        print('    ERROR:', r['error'])
        if r.get('error_after_kill_s') is not None:
            print('    error arrived {}s after the server died'.format(
                r['error_after_kill_s']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', action='append')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--sink', default='nv12', choices=['nv12', 'rgba'])
    ap.add_argument('--seconds', type=float)
    ap.add_argument('--fresh-registry', action='store_true')
    ap.add_argument('--json')
    a = ap.parse_args()

    meta = dict(platform=platform.platform(), python=platform.python_version(),
                machine=platform.machine())
    reg_dir = None
    if a.fresh_registry:
        reg_dir = tempfile.mkdtemp(prefix='kivy-gst-reg-')
        os.environ['GST_REGISTRY_1_0'] = os.path.join(reg_dir, 'registry.bin')
    os.environ.setdefault('KIVY_NO_ARGS', '1')

    t = time.perf_counter()
    from kivy.lib import gstplayer as g
    from kivy.lib.gstplayer import _policy as policy
    meta['import_s'] = round(time.perf_counter() - t, 3)
    meta['runtime'] = g.get_runtime_info()
    meta['has_gl'] = g.has_gl()
    meta['gi_imported'] = 'gi' in sys.modules

    mode = os.environ.get('CORE_INIT_MAIN')
    if mode:
        # Diagnostic: initialize first via ctypes (same library already
        # loaded), so init_start()'s call is a no-op. 1 = on this thread,
        # 2 = on a thread that then exits, 3 = on a thread that stays alive.
        import ctypes
        import threading
        lib = ctypes.CDLL(meta['runtime'][1])
        lib.gst_init_check.argtypes = [ctypes.c_void_p] * 3
        done = threading.Event()

        def init():
            print('gst_init_check on', threading.current_thread().name, ':',
                  lib.gst_init_check(None, None, None))
            done.set()
            if mode == '3':
                threading.Event().wait()
        if mode == '1':
            init()
        else:
            threading.Thread(target=init, daemon=True).start()
            done.wait()
            time.sleep(0.2)

    # Initialization on the worker thread, with a 60 Hz loop on this one.
    t = time.perf_counter()
    g.init_start()
    meta['init_start_ms'] = round((time.perf_counter() - t) * 1000, 3)
    gaps, last = [], time.perf_counter()
    while g.init_status()[0] == 'running':
        time.sleep(1 / 60)
        now = time.perf_counter()
        gaps.append((now - last) * 1000)
        last = now
    meta['init_status'] = g.init_status()
    meta['init_s'] = round(g.init_seconds(), 3)
    meta['ui_ticks_during_init'] = len(gaps)
    meta['ui_max_gap_ms'] = round(max(gaps), 1) if gaps else None
    meta['fresh_registry'] = a.fresh_registry
    print('runtime', meta['runtime'])
    print('init {init_s}s, status {init_status}; init_start() {init_start_ms} ms; '
          '{ui_ticks_during_init} UI ticks meanwhile, longest gap '
          '{ui_max_gap_ms} ms; gi imported: {gi_imported}'.format(**meta))
    if meta['init_status'][0] != 'ok':
        sys.exit(1)
    meta['elements'] = {n: g.element_available(n) for n in (
        'playbin3', 'glupload', 'tsdemux', 'hlsdemux2', 'dashdemux2',
        'souphttpsrc', 'avdec_h264', 'avdec_h265', 'v4l2slh265dec',
        'vah264dec', 'd3d11h264dec', 'd3d12h264dec', 'nvh264dec')}

    cases = list(CASES) if a.all else (a.case or ['local-1080p'])
    results = []
    for c in cases:
        r = run_case(g, policy, c, a.sink, a.seconds)
        summarize(r)
        results.append(r)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, 'w') as f:
            json.dump(dict(meta=meta, results=results), f, indent=1,
                      default=str)
        print('wrote', a.json)
    if reg_dir:
        shutil.rmtree(reg_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
