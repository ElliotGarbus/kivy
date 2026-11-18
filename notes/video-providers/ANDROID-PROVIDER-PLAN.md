# Android video provider (Media3 ExoPlayer) -- implementation plan

Rewrite `kivy/core/video/video_android.py` on androidx.media3 ExoPlayer, make it
the default (and only) video provider on Android, fire `on_error`, and document
it. Builds on the spike in `ANDROID-EXOPLAYER-SPIKE.md` and on the `on_error`
contract in kivy/kivy#9528. Roadmap context: [[kivy3-video-provider-roadmap]].

Written 2026-11-18. Nothing here is implemented yet.

## Scope

In scope:

- `VideoAndroid(VideoBase)`: load, play, pause, stop, seek, position, duration,
  volume, `eos` handling, `buffering`, `on_load` / `on_frame` / `on_eos`, and
  `on_error` for every failure the provider can see.
- Zero-copy rendering: ExoPlayer decodes into a `SurfaceTexture` bound to a Kivy
  GL texture; an FBO turns the external (OES) texture into a normal 2D texture.
- Sources: local paths, `file://`, `http(s)://` (progressive, plus HLS/DASH
  when the app adds those Media3 modules), and `content://` URIs.
- `generate_thumbnail()`.
- Registration as the `android` provider, unit tests that run on desktop CI,
  device validation, and docs.

Not in scope: a Java helper class shipped with Kivy (Kivy ships no Java, and
kivyforge's native-integration sidecar that would deliver one is not
implemented), DRM, subtitles, track selection, picture-in-picture, background
audio playback.

## Prerequisites and sequencing

1. **kivy/kivy#9528 (`on_error`) lands first**, or the Android branch is stacked
   on `video-on-error-event`. The provider calls `VideoBase._do_error()`, and the
   docs edit text that #9528 adds.
2. **A Kivy 3 Android build to test against.** The spike ran on Kivy 2.3.1
   because that is what the kivy-mobile-wheels index serves. The provider has to
   be validated on a Kivy 3 wheel built from its own branch: follow kivyforge's
   `docs/guides/guides/cross-platform/kivy-dev-build.md` (or the
   `ci/android-ios-wheels` cibuildwheel job) to produce an arm64_v8a and an
   x86_64 wheel, and point the spike app's `pyproject.toml` at them. Do this
   first: it is the slowest dependency, and every later step needs it.
3. The old `video_android.py` is replaced in place. Its registration was already
   dropped by the provider-registry refactor (#9266), so nothing depends on it.

## Design decisions

Each decision cites the spike finding behind it.

### D1. Pure pyjnius, using Kivy's own UI-thread helpers

No Java ships with Kivy, so the provider is Python + pyjnius like
`audio_android`, `camera_android` and `clipboard_android`. It gets its Activity,
Context and UI thread from `kivy.mobile._platform.android` (`get_activity()`,
`get_app_context()`, `run_on_ui_thread()`, `_on_ui_thread()`), never from
`org.kivy.android.PythonActivity` or the bootstrap's `android.runnable`. That
keeps it on the Kivy 3 bootstrap contract (#9352).

### D2. Threading: the player lives on the Android UI thread

Spike finding 3: every `Player` call throws unless it is made on the player's
application thread, and Kivy's thread (`SDLThread`) can't be that thread.

- Build with `ExoPlayer$Builder(context).setLooper(Looper.getMainLooper())`.
  Build itself may run on any thread.
- Every `Player` method call goes through `run_on_ui_thread()`. Calls run in
  the order they were posted, so `setMediaItem` -> `prepare` -> `play` stays
  ordered without extra bookkeeping.
- The Kivy thread never blocks on the UI thread during playback. The blocking
  `_on_ui_thread()` is allowed only in `unload()`, where the player has to be
  released before the `SurfaceTexture` it draws into, with a short timeout.
  Blocking every frame would stutter, and would deadlock when SDL's activity
  callbacks wait on the Kivy thread during pause.

### D3. Poll the player, don't implement `Player.Listener`

Spike findings 4 and 5: a pyjnius `Player.Listener` prints a "method missing"
traceback for every callback it doesn't implement (dozens a second), and
`onVideoSizeChanged` never arrived at all. Polling avoids both. It also mirrors
the Media Foundation provider, whose `_update()` polls
`hasError / isBuffering / isEOS / hasNewFrame` once per Clock tick.

- The provider owns one persistent `Runnable` (a `PythonJavaClass`, reused, not
  allocated per tick). Each `_update()` posts it, unless the previous post
  hasn't run yet.
- On the UI thread it reads `getPlaybackState()`, `isPlaying()`,
  `getCurrentPosition()`, `getDuration()`, `getVideoSize()` and
  `getPlayerError()`, and stores them in a small snapshot under a
  `threading.Lock`.
- `_update()` on the Kivy thread acts on the latest snapshot: errors, buffering
  changes, EOS, texture (re)sizing. The snapshot is one tick old, which is fine
  for all of these.
- `getVideoSize()` through polling is untested (the spike used the listener).
  If it also reports 0x0, fall back to the selected video track's `Format` from
  `getCurrentTracks()`, which the spike proved works. Settle this in step 3.

### D4. Rendering: SurfaceTexture, FBO, and the transform matrix

Keep the zero-copy path the old provider and the spike used: a Kivy `Texture`
whose id backs a `SurfaceTexture`, wrapped in a `Surface` and handed to
`setVideoSurface`; each tick `updateTexImage()` runs on the Kivy (GL) thread and
the FBO's external-texture shader draws it into a normal texture that becomes
`self._texture`.

Two changes from the old provider:

- **Use `SurfaceTexture.getTransformMatrix()` instead of the rotation metadata.**
  The matrix carries the decoder's crop (for example, 1080p decoded into a
  1088-row buffer) and the rotation MediaCodec applies for a surface. Rotating
  by `Format.rotationDegrees` on top risks rotating twice. Pass the matrix as a
  `mat4` uniform. Size the FBO from the displayed size, with width and height
  swapped for 90 and 270 degrees. Needs a portrait phone clip to verify (step 10).
- **Only report new frames.** Compare `getTimestamp()` before and after
  `updateTexImage()`; draw the FBO and dispatch `on_frame` only when it changed.
  `on_load` fires on the first real frame, as in Media Foundation.

Tick rate: `VideoBase` already schedules `_update` at `1 / fps` (default 30).
60 fps sources drop every other frame at that rate. Decide in step 4 whether to
schedule the frame pump every frame (interval 0) and keep the UI-thread poll at
the `fps` rate.

### D5. Errors and `on_error`

Every failure goes through `VideoBase._do_error(message)` exactly once per load,
**from `_update()`**, never from `load()`. The Media Foundation commit for #9528
explains why: `load()` runs while the `Video` widget is still constructing the
provider, before it has bound `on_error`. The provider keeps a
`_pending_error` / `_error_reported` pair like Media Foundation does. Sources:

| Failure | Detected | Message (logged at error level, then `on_error`) |
|---|---|---|
| Media3 classes missing from the APK | `autoclass` raises in `load()` | Names `androidx.media3:media3-exoplayer` and links the docs section |
| No Activity / Context (for example, a service) | `get_app_context()` is `None` in `load()` | "no Android Activity to play video in" |
| Building the player or the surface fails | `JavaException` in `load()` / surface setup | the Java exception text |
| File missing, unsupported codec, corrupt file, HTTP error, decoder failure mid-stream | `getPlayerError()` in the poll | `getErrorCodeName()`, a colon, then `getMessage()`, for example `ERROR_CODE_IO_FILE_NOT_FOUND: ...` |

Media3 is resolved lazily, in `load()`, not at import time. `core_select_lib`
logs an `ImportError` only at debug level, so failing the import would hide the
most common setup mistake (forgetting the Maven dependency) and silently fall
through to the null provider. Android has no secondary provider in the roadmap;
an app that really wants one sets `KIVY_VIDEO`.

A missing local file is already reported by the `Video` widget (#9528), since
its `resource_find()` check runs before the provider sees the filename.
`content://` URIs contain `://`, so they skip that check and reach the provider.
`VideoAndroid` used directly as a core class still reports a missing file,
through `getPlayerError()`.

### D6. Position, duration, seek, volume, loop

- `_get_position()` / `_get_duration()` return the snapshot, in seconds.
  Duration is `C.TIME_UNSET` until known, which maps to 0.
- `seek(percent, precise)`: target = `percent * duration`. `precise=True` uses
  `SeekParameters.EXACT`, `False` uses `CLOSEST_SYNC`. Update the snapshot
  position to the target right away, so a slider bound to `position` doesn't
  jump back for a tick.
- `_set_volume()`: `player.setVolume(v)` on the UI thread, plus `self._volume`.
- `eos`: on `STATE_ENDED`, call `VideoBase._do_eos()` once per end, so `'loop'`
  seeks to 0 and plays, and `on_eos` fires every loop, like the other providers.
  Media3's gapless `REPEAT_MODE_ONE` would never reach `STATE_ENDED`, so it is
  not used.
- `stop()`: `pause()` plus a seek to 0 (`Player.stop()` drops the media and would
  force a full re-prepare), state `''`.

### D7. Sources

A `filename` with a scheme (`http`, `https`, `content`, `file`, `rtsp`) goes
through `Uri.parse()`; anything else is a path, through `Uri.fromFile(File(path))`.
`MediaItem.fromUri(uri)` covers all of them. HLS and DASH play when the app adds
`media3-exoplayer-hls` / `media3-exoplayer-dash`: `DefaultMediaSourceFactory`
picks them up by extension (`.m3u8`, `.mpd`) with no provider code. A
`mime_type` option for extension-less stream URLs is a possible follow-up, not
part of this change; v1 exposes no `options` keys and warns on unknown ones,
like Media Foundation.

### D8. App pause and resume

On Android the GL context can be lost while the app is paused, which takes the
`SurfaceTexture`'s texture id with it.

- Bind `App.on_pause` / `on_resume` (or the Window equivalents) in the provider:
  on pause, remember whether it was playing and pause the player; on resume,
  rebuild texture, `SurfaceTexture`, `Surface` and FBO, call `setVideoSurface`
  with the new surface, and resume if it was playing.
- Verify on the device by backgrounding mid-playback, then by rotating it.
  `camera_android` has the same exposure; check how it handles resume and
  match it.

### D9. Audio focus

Build with `setAudioAttributes(AudioAttributes(USAGE_MEDIA, CONTENT_TYPE_MOVIE),
handleAudioFocus=True)`. ExoPlayer then pauses for a phone call and ducks for
notifications, which users expect from a video player. Note it in the class
docstring. Check that it doesn't fight `SoundLoader` sounds playing through
`audio_android` at the same time.

### D10. Thumbnails

`generate_thumbnail(filename, time, size)` uses `MediaMetadataRetriever`, not
ExoPlayer: it is synchronous and safe on any thread, which suits a classmethod.
`setDataSource(context, uri)` for `content://`, `setDataSource(url, headers)`
for http(s), `setDataSource(path)` otherwise. `getScaledFrameAtTime()` when
`size` is given (API 27+, else scale after), `getFrameAtTime(us,
OPTION_CLOSEST_SYNC)` otherwise. Copy the `Bitmap` into a `Texture`: try
`copyPixelsToBuffer` into a `ByteBuffer` first; if pyjnius hands the bytes back
slowly, compress to PNG and load with `CoreImage`. Measure both on the device.
Return `None` (and log) on any failure, per the `VideoBase` contract.

## Implementation steps

Each step ends with something checkable. Steps 1 to 8 are one module,
`kivy/core/video/video_android.py`; tests come in step 9 but should be written
alongside.

1. **Registration and skeleton.** Add `('android', 'video_android')` to
   `PROVIDER_CONFIGS['video']` in `kivy/core/__init__.py`. In
   `kivy/core/video/__init__.py`, append
   `make_provider_tuple('android', all_providers, 'VideoAndroid')` first, under
   `if platform == 'android':`, as `camera` and `audio_output` do. Module imports
   only `jnius` and Android framework classes (Media3 is resolved later, D5),
   with `__all__ = ('VideoAndroid',)` and the `_KNOWN_OPTIONS` warning.
   *Check:* on the device, the log shows `Video: Provider: android`.
2. **UI-thread command layer and poller.** Small private helpers that post a
   `Player` call with `run_on_ui_thread()`; the persistent poll `Runnable`; the
   locked snapshot.
   *Check:* a unit test with fake jnius sees calls posted in order and the
   snapshot updated.
3. **`load()` / `unload()`.** `load()`: unload any previous player, resolve
   Media3 (record a pending error on failure), build the player (D2, D9), post
   `setMediaItem` + `prepare`. `unload()`: post `clearVideoSurface` + `release`,
   wait for it (`_on_ui_thread`, short timeout), then release `Surface`,
   `SurfaceTexture`, FBO and textures, and reset state, buffering and the error
   flags. Settle D3's `getVideoSize()` question here.
   *Check:* load, unload and load again on the device without leaking players
   (`adb shell dumpsys media.player` / logcat `ExoPlayerImpl: Release`).
4. **Rendering (D4).** Surface creation when the size is first known; transform
   matrix uniform; new-frame detection; `on_load` on the first frame; rebuild on
   a size change. Decide the tick rate.
   *Check:* picture correct on the Pixel 8a; FBO redrawn only for new frames.
5. **Playback state (D6).** play / pause / stop / seek / volume, position and
   duration from the snapshot, `buffering` from `STATE_BUFFERING` while play was
   requested (always `False` when paused or stopped, per the `buffering`
   contract), EOS via `_do_eos()`.
   *Check:* each control behaves on the device; `eos='loop'` loops with
   `on_eos` each time.
6. **`on_error` (D5).** Pending-error plumbing; every row of the table.
   *Check:* each failure in the step 10 list fires `on_error` exactly once, after
   the widget has bound it, with `state` `'stop'` and `buffering` `False`.
7. **Pause and resume (D8).**
   *Check:* background and foreground mid-playback, and rotate, without a black
   or frozen picture.
8. **`generate_thumbnail()` (D10).**
   *Check:* `examples/widgets/video/thumbnail_demo.py` (added by the Media
   Foundation PR) shows thumbnails and plays on tap on the device.
9. **Unit tests**, new `kivy/tests/test_video_android.py`, using the fake-jnius
   pattern of `test_audio_android.py` and `test_clipboard_android.py`: fake
   `jnius` and `kivy.mobile._platform.android` in `sys.modules`, load
   `video_android.py` by file path, and fake `run_on_ui_thread` to run inline.
   Cases:
   - path vs scheme -> `Uri.fromFile` vs `Uri.parse`, including `content://`;
   - `load()` posts `setMediaItem` then `prepare`, through the UI-thread helper,
     never directly;
   - Media3 missing -> no exception from `load()`, `on_error` fires on the first
     `_update()` with the dependency message, once;
   - `getPlayerError()` in the snapshot -> `state` `''`, `buffering` cleared,
     `on_error(code + message)` once, not repeated on later ticks;
   - `STATE_ENDED` -> `_do_eos()` once; `eos='loop'` seeks to 0 and replays;
   - `STATE_BUFFERING` with play requested -> `on_buffering(True)`, then
     `False`; never `True` while paused;
   - seek: `precise` picks the seek parameters; the snapshot position updates
     immediately;
   - unknown `options` keys log a warning;
   - `unload()` releases the player before the `SurfaceTexture`.

   GL work (`Texture`, `Fbo`) is faked or skipped; it is covered on the device.
   *Check:* passes in desktop CI with no device or JVM.
10. **Device validation.** The spike app (`android-exoplayer-spike/`), switched
    from its own player code to `kivy.uix.video.Video` on the Kivy 3 dev wheel.
    Devices: Pixel 8a (arm64_v8a), `kivyforge_x86_64` (API 35) and
    `kivyforge_api24_x86_64` (the `min_sdk` floor). Cases:
    - H.264/AAC MP4 (the spike clip); HEVC; a portrait clip recorded on a phone
      (rotation 90); a 1920x1080 clip (crop); a 60 fps clip;
    - progressive http(s); HLS with `media3-exoplayer-hls`; a 404 URL; no network;
    - a `content://` URI from the system file picker;
    - a missing file, a corrupt file, an unsupported codec, and an APK built
      without the Media3 dependency, each firing `on_error` once;
    - pause, resume, rotate, seek while paused, volume, `eos` stop and loop;
    - a release build with `minify = true`, with and without the keep rule
      (expect the missing-dependency `on_error` without it);
    - re-check the emulator's black texture (spike open item) on the Kivy 3
      build; if it persists, document emulator video as unsupported rather than
      chase it.

    Record results in a `notes/video-providers/ANDROID-PROVIDER-RESULTS.md`, like
    the spike.
11. **Docs** (next section).
12. **PR** (last section).

## Docs

All edits are in the kivy repo; each says `android` where #9528's text lists
providers that report failures.

1. **`VideoAndroid` class docstring** (the canonical reference, as for
   `VideoMediaFoundation`):
   - built on androidx.media3 ExoPlayer; frames reach OpenGL without a CPU copy
     through a `SurfaceTexture`;
   - formats depend on the device's decoders (MediaCodec), not on Kivy: H.264
     and AAC everywhere, HEVC, VP9 and AV1 on most recent devices;
   - **required dependency**: `androidx.media3:media3-exoplayer` (tested with
     1.11.1, which needs compile SDK 36), plus `media3-exoplayer-hls` /
     `-dash` for streaming; without it every video fires `on_error`;
   - sources: paths, `file://`, `http(s)://` (needs the `INTERNET` permission),
     `content://`;
   - failures fire `on_error` (link `VideoBase.on_error`); no `options` keys yet;
   - audio focus handling (D9); pauses with the app (D8);
   - `.. versionchanged:: 3.0.0` rewritten on Media3 ExoPlayer, replacing
     `MediaPlayer`.
2. **`kivy/core/video/__init__.py`** module docstring: add `android` to the
   3.0.0 `versionchanged` paragraph that lists `avfoundation` and
   `mediafoundation`, noting it needs the Media3 dependency rather than "no
   extra package".
3. **`kivy/uix/video.py`**:
   - the `on_error` paragraph: "other failures are reported by the Media
     Foundation (Windows), AVFoundation (macOS, iOS) and Android providers";
   - `options`: add Android to "Other providers ... currently expose no
     documented `options` keys".
4. **`doc/sources/migration.rst`**, Video section:
   - new entry `*Android video provider rebuilt on Media3 ExoPlayer*`, in the
     same shape as the AVFoundation and Media Foundation entries.
     **Behavior Changes:** Kivy 2.x.x used `android.media.MediaPlayer`; Kivy
     3.x.x uses Media3 ExoPlayer and is the default on Android; the app must add
     the Media3 dependency; HLS and DASH work with the extra modules;
     `content://` URIs play; failures fire `on_error`; video pauses with the
     app. **Migration Impact:** add the dependency (Buildozer and kivyforge
     snippets, or a link to the packaging section), raise the compile SDK, add
     `INTERNET` for network sources, add the keep rule if R8 is enabled;
   - in #9528's on_error entry, add Android to "The Media Foundation (Windows)
     and AVFoundation (macOS, iOS) providers fire `on_error`".
5. **`doc/sources/guide/packaging-android.rst`**: a new "Playing video" section
   with both toolchains, since the guide covers only Buildozer today:
   - Buildozer: `android.gradle_dependencies =
     androidx.media3:media3-exoplayer:1.11.1` (plus `-hls` / `-dash`) and
     `android.api = 36`;
   - kivyforge: `[tool.kivy.android.gradle] dependencies = [...]`,
     `[tool.kivy.android] compile_sdk = 36`, `[tool.kivy.android.permissions]
     uses = ["INTERNET"]`;
   - R8: a keep rule, `-keep class androidx.media3.** { *; }`, or the narrower
     set step 10 proves sufficient, with where each tool takes it
     (`[tool.kivy.android.proguard]` for kivyforge).
   Verify every snippet by building the spike app with it.
6. **`doc/sources/guide/environment.rst`**: `android` is already in the
   `KIVY_VIDEO` values; confirm, no change expected.
7. Build the docs locally (`make html` in `doc/`) and check that the new
   cross-references resolve.

Follow-up outside this repo: once kivyforge implements the native-integration
sidecar, Kivy can declare the Media3 dependency itself and the "add this
dependency" docs shrink to a note. Track that on the roadmap, not here.

## PR

- Branch `android-exoplayer-video` from master after #9528 merges (or stacked on
  `video-on-error-event`, retargeted when it merges). Code commits only:
  `notes/` stays on a separate branch, per [[notes-stay-out-of-upstream-pr]].
- Suggested commits: (1) provider, registry and unit tests; (2) docs; (3) any
  example adjustment from step 8.
- Description: what it replaces and why (MediaPlayer, the dropped
  registration), the threading model in two sentences, the required dependency,
  what was verified on which devices, and what wasn't (emulator picture, if
  still black).
- Test plan in the description: the step 10 matrix as a checklist.

## Risks and open questions

| Risk | Where it gets settled | Fallback |
|---|---|---|
| `getVideoSize()` by polling reports 0x0, as the listener path never fired | Step 3 | Selected video track's `Format` from `getCurrentTracks()` |
| Transform matrix and rotation interplay; pyjnius may not write back into the `float[16]` passed to `getTransformMatrix` | Step 4, portrait clip | Allocate the array as a Java `float[]` and read it back; at worst, rotation from `VideoSize.unappliedRotationDegrees` |
| Posting the poll `Runnable` every tick costs too much (JNI + GIL) | Step 10, frame times on the 8a | Poll at 10 Hz; frames don't need the UI thread |
| GL context loss on resume | Step 7 | Rebuild from scratch on resume, as `load()` does |
| No Kivy 3 Android wheel to test on | Prerequisite 2 | Blocks device validation; unit tests and docs can proceed |
| Buildozer / python-for-android can't build compile SDK 36 yet | Step 11, while verifying snippets | Document the newest Media3 that builds with SDK 35; the provider uses only APIs present since Media3 1.0 |
| Emulator shows a black texture | Step 10 | Document emulator video as unsupported |
| Kivy-side `SoundLoader` audio and ExoPlayer audio focus interact badly | Step 10 | Make `handleAudioFocus` an `options` key |
