"""Check Video.on_error with the platform's default video provider.

Runs on macOS (AVFoundation) and Windows (Media Foundation). Each case
plays a file through the real kivy.uix.video.Video widget and must end
in one of two clean outcomes:

  played  -- loaded, position advanced, no on_error
  failed  -- exactly one on_error, state already 'stop', not loaded

Anything else (still waiting with no error, two errors, an error with
the state left at 'play') is a FAIL: that is the stuck-video case
on_error exists to prevent.

Usage, from the repo root:

    python notes/video-providers/on_error_check.py [--mp4 PATH]

Without --mp4 it downloads a 1 MB Big Buck Bunny clip (H.264/AAC) once,
the same clip examples/widgets/video/thumbnail_demo.py uses, into a temp
folder. Exits 0 when every case passes.
"""
import argparse
import os
import sys
import tempfile
import time
import urllib.request

CLIP_URL = ('https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/'
            'Big_Buck_Bunny_360_10s_1MB.mp4')
WORK = os.path.join(tempfile.gettempdir(), 'kivy_on_error_check')
WAIT = 6.0  # seconds to let every case settle


def fetch_clip():
    path = os.path.join(WORK, 'bbb_360_10s.mp4')
    if not os.path.exists(path):
        print(f'Downloading {CLIP_URL} ...')
        # Some CDNs refuse urllib's default user agent.
        req = urllib.request.Request(
            CLIP_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as r, \
                open(path, 'wb') as f:
            f.write(r.read())
    return path


def make_corrupt():
    path = os.path.join(WORK, 'corrupt.mp4')
    with open(path, 'wb') as f:
        f.write(bytes(i % 251 for i in range(4096)))
    return path


parser = argparse.ArgumentParser()
parser.add_argument('--mp4', help='a playable H.264 MP4 to use')
args, kivy_args = parser.parse_known_args()
sys.argv = sys.argv[:1] + kivy_args

os.makedirs(WORK, exist_ok=True)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# name -> (path, expectation): 'play', 'fail', or 'either'.
CASES = {
    'mp4 (H.264)': (args.mp4 or fetch_clip(), 'play'),
    'mpg (MPEG-1)': (os.path.join(REPO, 'examples', 'widgets',
                                  'cityCC0.mpg'), 'either'),
    'missing file': (os.path.join(WORK, 'does_not_exist.mp4'), 'fail'),
    'corrupt file': (make_corrupt(), 'fail'),
    # '://' skips the widget's file lookup, so the provider sees it.
    'malformed URL': ('bad url://not a valid url', 'either'),
}

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.video import Video as CoreVideo  # noqa: E402
from kivy.uix.gridlayout import GridLayout  # noqa: E402
from kivy.uix.video import Video  # noqa: E402


class Check(App):
    def build(self):
        root = GridLayout(cols=3)
        self.rows = []
        for name, (path, expect) in CASES.items():
            rec = {'name': name, 'expect': expect, 'errors': []}
            v = Video(source=path, state='play', volume=0)
            v.bind(on_error=lambda inst, err, rec=rec: rec['errors'].append(
                (err, inst.state, inst.loaded)))
            rec['video'] = v
            root.add_widget(v)
            self.rows.append(rec)
        self.t0 = time.perf_counter()
        Clock.schedule_once(self.report, WAIT)
        return root

    def report(self, dt):
        print(f'\nProvider: {CoreVideo.__name__}  ({sys.platform})\n')
        failures = 0
        for rec in self.rows:
            v, errors = rec['video'], rec['errors']
            core = v._video
            tex = core.texture if core is not None else None
            played = v.loaded and v.position > 0 and not errors
            failed_clean = (len(errors) == 1 and errors[0][1] == 'stop'
                            and not errors[0][2] and v.state == 'stop')
            if rec['expect'] == 'play':
                ok = played
            elif rec['expect'] == 'fail':
                ok = failed_clean
            else:
                ok = played or failed_clean
            outcome = ('played' if played else
                       'failed cleanly' if failed_clean else 'STUCK/WRONG')
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {rec['name']:14} {outcome:15}"
                  f" state={v.state} loaded={v.loaded} "
                  f"pos={max(v.position, 0):.2f} errors={len(errors)}"
                  + (f" colorfmt={tex.colorfmt}" if tex is not None else ''))
            for err, state, loaded in errors:
                print(f'      on_error: {err!r} (state={state}, '
                      f'loaded={loaded})')
        print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
        self.exit_code = 1 if failures else 0
        self.stop()


app = Check()
app.exit_code = 1
app.run()
sys.exit(app.exit_code)
