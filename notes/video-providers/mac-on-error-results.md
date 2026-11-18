# Mac results: AVFoundation `on_error` for corrupt files and bad URLs

Machine: macOS 26.6.2 (25G83), Apple M5 Pro, Python 3.14.7, ANGLE Metal
backend, branch `video-on-error` at `d3e2f4a60`.

## Outcome

No provider change was needed. The two FAILs came from a stale build:
the `video_avfoundation` extension that ran was compiled on Oct 28, before
this branch existed, so it had none of the `on_error` code. After
`python setup.py build_ext --inplace`, the branch's AVFoundation provider,
unchanged from `ac2480d81`, passes every case. There is no provider commit.

## Original FAIL lines (verbatim)

From the first run on this Mac, with the old binary:

```
Provider: VideoAVFoundation  (darwin)

PASS  mp4 (H.264)    played          state=play loaded=True pos=5.57 errors=0 colorfmt=bgra
PASS  mpg (MPEG-1)   played          state=play loaded=True pos=5.51 errors=0 colorfmt=bgra
PASS  missing file   failed cleanly  state=stop loaded=False pos=0.00 errors=1
      on_error: 'file not found: /var/folders/vn/6m2fpc2s2bbdyxl635r97d5m0000gn/T/kivy_on_error_check/does_not_exist.mp4' (state=stop, loaded=False)
FAIL  corrupt file   STUCK/WRONG     state=play loaded=False pos=0.00 errors=0
FAIL  malformed URL  STUCK/WRONG     state=play loaded=False pos=0.00 errors=0

2 FAILED
```

The same run logged `VideoAVFoundation: invalid URL bad url://not a valid
url` through `NSLog`. That is the pre-branch code path; `ac2480d81` replaced
the `NSLog` with `_setError("invalid URL: ...")`.

## Root cause

- `kivy/core/video/video_avfoundation.cpython-314-darwin.so` was dated
  Oct 28 and the generated `video_avfoundation.cpp` Oct 20; the branch
  sources are from Nov 18. The extension had not been rebuilt since the
  branch was checked out.
- Confirmed by building the pre-branch provider sources (`aaa105639`) and
  running the check: it reproduces the two FAIL lines above exactly
  (missing file still passes because the `Video` widget reports it before
  the provider sees it). Building the branch sources makes both pass.
- The `notes/` hypotheses, checked against the branch build:
  1. For the corrupt file, `AVPlayerItem.status` does reach
     `AVPlayerItemStatusFailed`; the KVO path reports `Cannot Open
     (AVFoundationErrorDomain -11829)`. No asset-loading check is needed.
  2. On macOS 26.6.2, `[NSURL URLWithString:@"bad url://not a valid url"]`
     still returns nil (the scheme contains a space), so `load()` reports
     `invalid URL: ...`.
  3. Each failure fires `on_error` exactly once, with `state` already
     `'stop'` and `loaded` False.

## What changed and why

Nothing in the provider. The Objective-C++ from Windows compiles cleanly
(only the usual SDK/libc++ availability warnings) and behaves as designed.
Changing it would only add risk.

One thing to know when running the check: the first run right after a
rebuild can start playback slowly. On the first launch after rebuilding,
the MP4 and MPEG cases had only reached about 1.4 s, or once had not
loaded at all, with no error, when the 6 s report fired. Every later run
reached about 5.6 s. This looks like a one-time cost of loading the newly
built binary rather than a provider issue: it happened with the pre-branch
build as well, and never on repeat runs.

## Final `on_error_check.py` output (verbatim)

```
Provider: VideoAVFoundation  (darwin)

PASS  mp4 (H.264)    played          state=play loaded=True pos=5.58 errors=0 colorfmt=bgra
PASS  mpg (MPEG-1)   played          state=play loaded=True pos=5.58 errors=0 colorfmt=bgra
PASS  missing file   failed cleanly  state=stop loaded=False pos=0.00 errors=1
      on_error: 'file not found: /var/folders/vn/6m2fpc2s2bbdyxl635r97d5m0000gn/T/kivy_on_error_check/does_not_exist.mp4' (state=stop, loaded=False)
PASS  corrupt file   failed cleanly  state=stop loaded=False pos=0.00 errors=1
      on_error: 'Cannot Open (AVFoundationErrorDomain -11829)' (state=stop, loaded=False)
PASS  malformed URL  failed cleanly  state=stop loaded=False pos=0.00 errors=1
      on_error: 'invalid URL: bad url://not a valid url' (state=stop, loaded=False)

ALL PASSED
```

Four further runs also printed `ALL PASSED`, with the MP4 line at
`pos=5.58`-`5.61` and `colorfmt=bgra` each time.

## Final pytest output (verbatim)

```
============================= test session starts ==============================
platform darwin -- Python 3.14.7, pytest-9.1.1, pluggy-1.7.0 -- /Users/elliotgarbus/Developer/kivyPRs/.venv/bin/python
cachedir: .pytest_cache
benchmark: 5.3.0 (defaults: timer=time.perf_counter disable_gc=False min_rounds=5 min_time=0.000005 max_time=1.0 calibration_precision=10 warmup=False warmup_iterations=100000)
rootdir: /Users/elliotgarbus/Developer/kivyPRs/kivy/kivy/tests
configfile: pytest.ini
plugins: cov-7.1.0, timeout-2.4.0, asyncio-1.4.0, benchmark-5.3.0, pytest_httpserver-1.1.5, platformdirs-4.13.5
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 19 items
kivy/tests/test_video.py::VideoTestCase::test_video_unload PASSED        [  5%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_buffering_default_is_false PASSED [ 10%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_do_error_stops_then_dispatches_once PASSED [ 15%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_do_error_without_handler_does_not_raise PASSED [ 21%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_generate_thumbnail_default_returns_none PASSED [ 26%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_on_buffering_dispatches_with_value PASSED [ 31%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_on_buffering_event_registered PASSED [ 36%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_on_error_event_registered PASSED [ 42%]
kivy/tests/test_video.py::VideoBaseAPITestCase::test_options_default_is_empty_dict PASSED [ 47%]
kivy/tests/test_video.py::VideoWidgetErrorTestCase::test_core_error_stops_widget PASSED [ 52%]
kivy/tests/test_video.py::VideoWidgetErrorTestCase::test_missing_file_stops_and_dispatches_on_error PASSED [ 57%]
kivy/tests/test_video.py::VideoWidgetErrorTestCase::test_video_player_forwards_on_error PASSED [ 63%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationImportTestCase::test_avfoundation_is_default_on_darwin PASSED [ 68%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationImportTestCase::test_provider_class_importable PASSED [ 73%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationOptionsTestCase::test_auto_wait_option_roundtrip PASSED [ 78%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationOptionsTestCase::test_options_default_is_empty PASSED [ 84%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationOptionsTestCase::test_unknown_option_is_ignored_not_raised PASSED [ 89%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationThumbnailTestCase::test_generate_thumbnail_bad_path_returns_none PASSED [ 94%]
kivy/tests/test_video_avfoundation.py::VideoAVFoundationThumbnailTestCase::test_generate_thumbnail_returns_texture_or_none PASSED [100%]
============================== 19 passed in 0.55s ==============================
```

## Good files still play and loop

A small app played the 10 s Big Buck Bunny clip in two `Video` widgets for
16 s, sampling `position` every 0.1 s:

```
eos=stop state=stop loaded=True duration=10.00 max_pos=9.98 pos=0.00 eos_events=1 wraps=1 errors=[]
eos=loop state=play loaded=True duration=10.00 max_pos=9.98 pos=5.57 eos_events=0 wraps=1 errors=[]
EOS CHECK PASSED
```

`eos='stop'` played to the end, fired EOS once and stopped (the "wrap" is
the stop rewinding to 0); `eos='loop'` wrapped back to the start and kept
playing. Neither reported `on_error`.

Unrelated to this branch: passing `options={'eos': ...}` to `Video` logs
`VideoAVFoundation: ignoring unknown options keys ['eos']`, because the
widget forwards the whole `options` dict to the provider. That behaviour
predates `on_error` and was left alone.
