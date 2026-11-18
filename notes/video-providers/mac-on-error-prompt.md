# Mac task: make AVFoundation report corrupt files and bad URLs through `on_error`

You are working in a Kivy checkout on macOS, on branch `video-on-error`
(from the fork's `origin`). This branch adds a `VideoBase.on_error` event to
Kivy 3.0. On Windows everything passes; on this Mac two cases of the check
script fail. Find out why, fix the AVFoundation provider, verify, commit,
push, and write up the results. The work was written on Windows, so the
Objective-C++ in this branch had never been compiled or run before this Mac.

## What `on_error` is (already implemented; don't redesign it)

- `kivy/core/video/__init__.py`: `VideoBase` has an `on_error(error)` event
  with an empty default handler, and `_do_error(error)`, which providers
  call once per failure: it stops playback (`state` becomes `''`), clears
  `buffering`, then dispatches `on_error`. Nothing is ever raised; an app
  that doesn't bind `on_error` just keeps running.
- `kivy/uix/video.py`: `Video` forwards it after setting `state = 'stop'`
  (and reports a missing local file itself, before any provider sees it).
  `kivy/uix/videoplayer.py` forwards it from its `Video`.
- Providers report from their Clock-driven `_update()`, never from
  `load()`: `load()` runs while the `Video` widget is still constructing
  the provider, before it has bound `on_error`. The native layer keeps the
  *first* failure behind `hasError()` / `lastError()`; `_update()` checks
  `hasError()` first, and on the first sighting logs it and calls
  `self._do_error(...)`, latched by `self._error_reported`.

AVFoundation's version of that, added on this branch (commit
`ac2480d81 Video: fire on_error from the AVFoundation provider`):

- `kivy/core/video/video_avfoundation_implem.h` / `.mm`:
  - `hasError()`, `lastError()`, and private `_setError(NSString *)`
    (first failure wins; `mError` under `mErrorLock`, then the atomic
    `mHasError`; cleared in `unload()`).
  - A KVO observer on `AVPlayerItem.status` (context `kItemStatusContext`,
    options `Initial | New`) calling `_onItemStatusChanged()`, which reports
    only when `status == AVPlayerItemStatusFailed`, using `item.error`.
  - `AVPlayerItemFailedToPlayToEndTimeNotification` -> `playbackFailed:` ->
    `_onPlaybackFailed()`.
  - `load()`: `[NSURL URLWithString:]` returning nil -> `_setError("invalid
    URL: ...")`.
  - The zero-copy failure in `_stageFrameFromBuffer` now calls `_setError`
    instead of faking an EOS.
- `kivy/core/video/video_avfoundation.pyx`: `_update()` reports
  `hasError()` as described above.

The `.mm` file uses manual retain/release (MRC, not ARC). KVO and
notification callbacks can arrive off the main thread, so they must only
touch atomics or mutex-guarded state, as the existing ones do.

## The failure

`python notes/video-providers/on_error_check.py` plays five cases through
the real `Video` widget and requires each to either *play* (loaded,
position advancing, no `on_error`) or *fail cleanly* (exactly one
`on_error`, `state` already `'stop'`, not loaded). Anything else is printed
as `STUCK/WRONG` and FAILs.

On this Mac: `mp4 (H.264)`, `mpg (MPEG-1)` and `missing file` PASS;
**`corrupt file` and `malformed URL` FAIL**. (`pytest
kivy/tests/test_video.py kivy/tests/test_video_avfoundation.py` all pass.)
The exact FAIL lines weren't captured -- start by capturing them.

The two cases:

- `corrupt file`: 4 KB of bytes `i % 251` saved as `corrupt.mp4` in the temp
  folder. Expected: fail cleanly.
- `malformed URL`: the source `'bad url://not a valid url'`. The `://` makes
  `Video` pass it straight to the provider. Either outcome is accepted, but
  it must not be stuck.

## Leads to check first (unverified hypotheses)

1. **No callback fires at all.** For an unreadable asset AVFoundation may
   leave `AVPlayerItem.status` at `Unknown` (or even reach
   `ReadyToPlay` with no playable tracks) instead of `Failed`, so the KVO
   path never reports. The canonical early check is
   `[asset loadValuesAsynchronouslyForKeys:@[@"playable", @"tracks"]
   completionHandler:...]`, then `statusOfValueForKey:error:` ==
   `AVKeyValueStatusFailed`, or `asset.playable == NO`, or no video track.
   `AVPlayer.status` (KVO on the player) is another failure signal.
   `loadValuesAsynchronouslyForKeys:` is the older API, deprecated since
   macOS 13 / iOS 16 in favour of the asynchronous `load` APIs (in
   Objective-C, for example `loadTracksWithMediaType:completionHandler:`);
   use whichever fits this file and Kivy's deployment target, and check
   the SDK headers rather than trusting this note.
2. **`URLWithString:` doesn't return nil.** Since macOS 14 it percent-encodes
   invalid characters instead of returning nil, so `'bad url://...'` may
   produce a non-nil NSURL with an unusable scheme, and then nothing reports
   the failure -- the same root cause as (1).
3. **The error fires but the outcome is still wrong** -- e.g. `on_error`
   fires twice, `state` isn't `'stop'` yet when it fires, or an EOS also
   arrives. The check script prints the error list; compare with the rules
   above.

Add temporary `NSLog`s (in `_onItemStatusChanged`, `_setError`, `load()`,
and wherever else helps) to see which path runs and what
`item.status` / `item.error` / `player.status` are. Remove them before
committing.

## Build and test

```bash
export KIVY_DEPS_ROOT=<path to your kivy-dependencies>
python setup.py build_ext --inplace
python notes/video-providers/on_error_check.py
python -m pytest kivy/tests/test_video.py kivy/tests/test_video_avfoundation.py -v
```

Done means:

- `on_error_check.py` prints `ALL PASSED`, and the MP4 line still shows
  `colorfmt=bgra` (zero-copy intact).
- Both pytest files pass.
- A normal MP4 still plays to the end with `eos='stop'` and loops with
  `eos='loop'` (for example `Video(source=..., state='play',
  options={'eos': 'loop'})` in a tiny app), i.e. the fix doesn't report
  errors for good files.

## Constraints

- Change only the AVFoundation provider (`video_avfoundation.pyx`,
  `video_avfoundation_implem.h`, `video_avfoundation_implem.mm`) unless the
  root cause is genuinely elsewhere -- if it is, explain why in the
  results file. Don't touch the Media Foundation (Windows) provider.
- Keep the design: first failure wins, report from `_update()` through
  `_do_error()`, never raise, never report from `load()` directly.
- Keep the check script's rules; don't loosen it to make a case pass. You
  may add diagnostics to it.

## Commit, push, report

1. Commit the provider fix on `video-on-error`, separate from any `notes/`
   changes. Message: an imperative summary line, then a body explaining the
   root cause and the fix. Commit only the files you changed.
2. Write `notes/video-providers/mac-on-error-results.md` with: the original
   FAIL lines (verbatim), the root cause, what you changed and why, and the
   final `on_error_check.py` and pytest output (verbatim). Commit it as a
   separate notes commit.
3. `git push origin video-on-error`. Don't open a PR and don't push any
   other branch.
