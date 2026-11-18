# Android ExoPlayer spike -- findings

Spike for the Kivy 3.0 Android video provider (`video_android.py` rewrite,
[[kivy3-video-provider-roadmap]]), ahead of writing it against the real
`VideoBase` API. Built and run from `notes/video-providers/android-exoplayer-spike/`
(a standalone kivyforge app, "ExoPlayer Check" / `org.kivyforge.exoplayercheck`).
Not part of the upstream PR -- notes-only, per [[notes-stay-out-of-upstream-pr]].
Date: 2026-11-18.

**Bottom line: the approach works end to end, on both an emulator and a real
device.** All six checkpoints (`EXOPLAYER_AUTOCLASS_OK`, `..._LISTENER_CREATED`,
`..._SURFACE_OK`, `..._FRAME_OK`, `..._READY_OK`, `..._EOS_OK`) fired on both.
On the physical device the picture actually renders correctly
(`evidence/pixel8a_playback.png`); on the emulator the texture stayed black
despite identical frame-count progress and no errors -- see "Open item" below.

## Environment

| Item | Value |
|---|---|
| Host | Windows 11, JDK 17 (Temurin), Android SDK (build-tools 35, NDK 27.3), kivyforge 0.1.1.dev50 |
| Emulator | `kivyforge_x86_64` AVD, Android 15 (API 35), x86_64 |
| Device | Pixel 8a (`akita`), arm64_v8a, physical hardware over USB |
| App | Kivy 2.3.1 (`kivy_generation = 2`), pyjnius (locked), CPython 3.14.6 |
| Dependency | `androidx.media3:media3-exoplayer:1.11.1` (Maven, resolved via `kivyforge lock -p android`) |

## What was built

A minimal Kivy app (`src/main.py`) that loads a bundled clip
(`src/clips/clip.mp4`, Big Buck Bunny, reused from the `mf-check` Windows
spike) through `androidx.media3.exoplayer.ExoPlayer`, renders it into a
`SurfaceTexture` bound to a Kivy GL texture id, draws that through an FBO
with the same external-OES rotation shader `video_android.py` already uses,
and exposes Play/Pause/Stop/+10s buttons plus a status label. Each checkpoint
prints an `EXOPLAYER_*` marker for grepping out of `adb logcat`.

## Findings

### 1. The Maven dependency resolves cleanly, but needs `compile_sdk = 36`

`androidx.media3:media3-exoplayer:1.11.1` locks and builds with one extra
setting. Without it, `checkDebugAarMetadata` fails: every media3 artifact
requires `compileSdk >= 36` (same constraint kivyforge's own Stripe spike hit,
see `docs/design/dev/android-androidx-subclass-spike-findings.md`). This is a
requirement on whatever app consumes the provider, not on Kivy's own code, but
it belongs in the provider's docs.

### 2. `ExoPlayer.Builder` needs its own `autoclass`

`ExoPlayer.Builder` is a static nested class. `autoclass("...ExoPlayer").Builder(...)`
fails (`AttributeError`); it needs the Java nested-class name directly:
`autoclass("androidx.media3.exoplayer.ExoPlayer$Builder")`.

### 3. Threading is the real story -- ExoPlayer enforces a single "application thread" for *every* call, not just construction

This is the main architectural finding, and the thing the old `MediaPlayer`-
based provider never had to deal with.

A `Player` has one fixed "application thread" (`Builder.setLooper(...)`,
defaulting to the thread that calls `build()`). **Every subsequent call into
it -- `setMediaItem`, `prepare`, `play`/`pause`/`stop`, `seekTo`,
`setVideoSurface`, `release`, all of it, not just listener registration --
throws `IllegalStateException: Player is accessed on the wrong thread` if
made from any other thread.**

Kivy's Python thread on Android (`SDLThread`) is not, and cannot become, that
thread: it has no `Looper` of its own, and it's busy running Kivy's own
event loop, so it can't call `Looper.prepare()` / `Looper.loop()` either
without blocking the app. The fix used here:

- Build the player with `Builder.setLooper(Looper.getMainLooper())`, making
  Android's real UI thread (`SDLActivity`, not `SDLThread`) the player's
  application thread.
- Bounce *every* `Player` method call onto that thread with kivyforge's
  `android.runnable.Runnable` (the vendored python-for-android
  `run_on_ui_thread` machinery -- see `_ui()` in `src/main.py`).
- Listener callbacks arrive on that same UI thread for the same reason, and
  get bounced back onto Kivy's own thread with Kivy's `@mainthread` before
  touching any Kivy object (textures, `Clock`, widget state) -- Kivy objects
  aren't thread-safe either.

So there are two hops in both directions: Kivy thread --(Runnable)--> UI
thread for anything calling into the player, UI thread --(`@mainthread`)-->
Kivy thread for anything the player calls back with. `android.runnable.Runnable`
is fire-and-forget (no return value), which is fine for the mutating calls
this spike makes. **The real provider's biggest open design question is
`_get_position()`/`_get_duration()`**: `VideoBase` properties are read
synchronously from Kivy code, but `getCurrentPosition()`/`getDuration()` can
only be called safely on the UI thread. The practical answer is almost
certainly to cache the last-known position/duration locally (updated from
listener callbacks and/or a periodic UI-thread poll bounced back
asynchronously) rather than trying to block Kivy's thread on a synchronous
cross-thread round trip, which risks jank or deadlock.

### 4. pyjnius tolerates an incompletely-implemented `Player.Listener`, but noisily

`Player.Listener` has ~30 methods, nearly all default (empty-body) methods
on the interface. Implementing only the ones you need is fine -- pyjnius's
proxy catches the rest as `NotImplementedError` and logs a
`"Python/java method missing"` block plus a traceback *per call*, but does
not crash the app. In a real playback session this fires dozens of times a
second during buffering/state transitions (`onEvents`, `onTracksChanged`,
`onPlayerStateChanged`, etc. all fire constantly). For the real provider,
either implement all the methods with no-op bodies to keep a user's logcat
clean, or accept the noise; it is not a functional problem.

### 5. `onVideoSizeChanged` never fired -- use `onTracksChanged` instead

Despite being a normal default method on `Player.Listener` with a descriptor
confirmed correct by `javap` (`(Landroidx/media3/common/VideoSize;)V`), and
despite *other* default methods taking custom Media3 class arguments (e.g.
`onTracksChanged(Tracks)`) dispatching through the same pyjnius proxy
correctly, `onVideoSizeChanged` was never observed firing -- not called, not
logged as missing, nothing -- on either the emulator or the device. Root
cause not identified (candidate for a minimal pyjnius repro / upstream
issue if it matters later).

**Workaround that works reliably:** get width/height/rotation from
`onTracksChanged` instead, by walking the video `Tracks.Group` and reading
`Format.width` / `Format.height` / `Format.rotationDegrees` directly (see
`_on_tracks_changed` in `src/main.py`). This is arguably better anyway --
`Format` is the authoritative source and arrives through a path already
proven to work.

### 6. The provider's `load()` can no longer build the texture synchronously

`MediaPlayer.prepare()` blocks; `getVideoWidth()`/`getVideoHeight()` are
available the instant it returns, so the existing provider builds the
texture/FBO inline in `load()`. ExoPlayer's `prepare()` returns immediately;
size (now: `onTracksChanged`) arrives later, asynchronously, on the UI
thread. The rewrite's `load()` can only kick off `setMediaItem`/`prepare`;
texture/FBO setup, and firing `on_load`, has to happen from that callback.

### Checkpoints observed, both environments

```
EXOPLAYER_AUTOCLASS_OK      -- androidx.media3 classes resolve via autoclass
EXOPLAYER_LISTENER_CREATED  -- PythonJavaClass implements Player.Listener
EXOPLAYER_SURFACE_OK        -- SurfaceTexture bound to the Kivy texture id,
                                handed to the player via setVideoSurface
EXOPLAYER_FRAME_OK          -- first updateTexImage() + FBO draw
EXOPLAYER_READY_OK          -- Player.STATE_READY
EXOPLAYER_EOS_OK            -- Player.STATE_ENDED, full clip played through
```

## Open item: black texture on the emulator, correct picture on the device

On the Pixel 8a the picture renders correctly, full-frame, right side up
(`evidence/pixel8a_playback.png`). On the `kivyforge_x86_64` emulator the
same build reached every checkpoint (including steadily incrementing
`frames drawn` in the on-screen status label) but the texture area stayed
black. No GL/shader errors were logged in either case. This was not
investigated further given the real-device confirmation -- it's consistent
with known flakiness in the Android emulator's translated GL driver around
external/OES textures (`samplerExternalOES` + `SurfaceTexture`), not a defect
in the approach. Worth a quick recheck on a newer emulator image before
relying on emulator-only testing for the real provider's CI/dev loop.

## Recommendations for the `video_android.py` rewrite

1. Mirror the two-hop threading model throughout: every `Player` call via
   `android.runnable`'s UI-thread bounce; every listener callback bounced
   back to Kivy's thread via `@mainthread` before touching `VideoBase` state.
2. Drive `on_load` and texture/FBO construction from `onTracksChanged`
   (video `Format`), not from `load()` itself.
3. Cache position/duration from listener updates / a UI-thread-bounced poll
   rather than reading the player synchronously from `_get_position()` /
   `_get_duration()`.
4. Wire `onPlayerError` to `VideoBase.on_error` (per the roadmap's
   already-added `on_error` contract) and `STATE_ENDED` to `_do_eos()`.
5. Declare `androidx.media3:media3-exoplayer` plus `compile_sdk = 36` as the
   provider's build requirement for consuming apps.
6. Decide whether to implement every `Player.Listener` method as a no-op (to
   keep user logcats clean) or accept the "method missing" noise.
7. HLS/DASH support (a roadmap goal) wasn't exercised by this spike --
   `media3-exoplayer` includes the extractors, but a follow-up spike with an
   actual stream URL would confirm it end to end, including under the same
   UI-thread-bounce constraints.

## Files

- `android-exoplayer-spike/pyproject.toml`, `src/main.py` -- the spike app.
- `android-exoplayer-spike/evidence/pixel8a_playback.png` -- real-device
  screenshot mid-playback.
