# Android video provider (Media3 ExoPlayer) -- device results

Device validation for the `video_android.py` rewrite on branch
`android-exoplayer-video` (stacked on `video-on-error-event`, kivy/kivy#9528),
following step 10 of `ANDROID-PROVIDER-PLAN.md`. Date: 2026-11-18.

**Result: 9 of 9 cases pass on a Pixel 8a and on the x86_64 emulator**, with
the picture upright, the rotated clip turned correctly, and the emulator
rendering video (the spike's black-texture problem is gone).

## Setup

| Item | Value |
|---|---|
| Devices | Pixel 8a (`akita`, Android 16, Mali-G715, arm64_v8a); `kivyforge_x86_64` AVD (Android 15, x86_64) |
| Kivy | `3.0.0.dev202611051750` Android wheels from kivy-mobile-wheels, with the branch's Python overlaid (`make_wheels.py`, 12 files). Sound because no compiled code used on Android changed since that build; only Apple, Windows and `Matrix.rotate/scale` `.pyx` changes are newer. |
| App | `android-provider-check/` (kivyforge, `kivy_generation = 3`, CPython 3.14.6, `compile_sdk = 36`), Media3 `media3-exoplayer` and `-hls` 1.11.1 |
| Media | `make_clips.py`: Big Buck Bunny 640x360 H.264 (360 isn't a multiple of 16, so the decoder crops 368 rows), the same file with a 90 degree track matrix, 64 KiB of random bytes. Network: a public HLS stream, a progressive MP4 and a URL that returns 403. |
| Driver | `run_check.sh SERIAL ABI`: build, install, follow logcat, take the screenshots and do the background/restore the app asks for. |

## Cases

| Case | What it checks | Pixel 8a | Emulator |
|---|---|---|---|
| provider | `android` is the selected provider | pass | pass |
| h264 | load, texture 640x360, playing, seek 50%, pause holds, end -> `state` stop | pass | pass |
| rotated | 90 degree file -> 360x640 texture, picture turned clockwise | pass | pass |
| loop | `eos='loop'` replays, `on_eos` every time | pass | pass |
| errors | corrupt file, unknown `content://`, HTTP 403, missing file: `on_error` exactly once each, `state` stop, not loaded | pass | pass |
| http | progressive MP4 over HTTPS plays | pass | pass |
| hls | 1920x1080 HLS plays | pass | pass |
| thumbnail | 320x180 thumbnail; missing file -> `None` | pass | pass |
| pause_resume | background then foreground mid-playback; still playing | pass | pass |

The `on_error` messages, as an app sees them:

```
ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED: Source error (None of the available extractors (...) could read the stream.)
ERROR_CODE_IO_FILE_NOT_FOUND: Source error (java.io.FileNotFoundException: No content provider: content://org.kivyforge.nosuchprovider/video.mp4)
ERROR_CODE_IO_BAD_HTTP_STATUS: Source error (Response code: 403)
file not found: /data/user/0/.../clips/no-such-clip.mp4        (reported by the Video widget, #9528)
```

Screenshots: `android-provider-check/evidence/`.

## What the device runs found and changed

1. **The picture was upside down.** Applying `SurfaceTexture.getTransformMatrix()`
   as Android documents it (the decoder's matrix here: a vertical flip plus the
   368 -> 360 crop) left the video vertically flipped in Kivy's FBO. The fix is a
   flip of `tex_coord0` in the provider's shader, so the provider still applies
   the decoder's matrix, which is what makes orientation device-independent.
   - A first attempt flipped the FBO rectangle's `tex_coords` and changed
     nothing: a `Rectangle` with no texture of its own takes Kivy's default
     texture's coordinates, which are already `(0, 1, 1, 1, 1, 0, 0, 0)`.
     Reproduced on the desktop with a gradient texture before changing the
     shader.
   - The spike looked upright only because it ignored the matrix.
2. **The emulator renders video now.** The spike's black texture on the
   emulator is gone. The likely cause was the texture name: the spike, like the
   old provider, handed `SurfaceTexture` the id of a Kivy `Texture`, which was
   already bound as `GL_TEXTURE_2D`; a name can't change target, and the
   emulator's GL translator enforces that. The provider uses a bare
   `glGenTextures` name.
3. **`eos` in `options` was reported as unknown.** The `Video` widget passes
   `options={'eos': ...}` through as a keyword, the documented way to loop, and
   the provider warned "ignoring unknown options keys ['eos']". It now excludes
   `VideoBase`'s own keyword arguments. The AVFoundation and Media Foundation
   providers have the same check and the same false warning; not fixed here.

## Harness lessons (test app only)

- Android's stdout redirect logs each `write()` as its own logcat line, and
  `print(a, b)` writes each argument separately: markers must be one string.
- Kivy's `Clock` holds a scheduled bound method weakly; an object that only
  schedules itself is collected at once and never ticks.

## Not covered

- An APK without the Media3 dependency on a device. The missing-dependency
  `on_error` is covered by the unit tests (`autoclass` raising `JavaException`).
- A release build with R8: kivyforge's `[tool.kivy.android.proguard]` is
  specified but not implemented, and its guidance is to leave `minify` off for
  apps that reach Java from Python. The docs say the same and give the keep
  rule for toolchains that do run R8.
- Buildozer / python-for-android builds, HEVC, 60 fps, DASH, `content://` from a
  real provider (only the not-found case), API 24, long playback and memory.
- A GL context loss on resume: neither device lost it, so the surface rebuild
  in `_on_app_resume` ran but its loss path is unexercised.
