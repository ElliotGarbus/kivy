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

## Release build with R8 (2026-11-18)

Run once kivyforge could add keep rules (kivyforge #240, on `main` after
v0.1.2; the editable install here was at `main`'s tree). `pyproject.toml` now
sets `[tool.kivy.android.build_settings] minify = true`, and
`VARIANT=release ./run_check.sh SERIAL ABI` builds with
`kivyforge run -p android --release`, which runs `:app:minifyReleaseWithR8` and
signs with the debug key. Pixel 8a, arm64_v8a, same wheels and clips as above.

| Config | Result |
|---|---|
| `minify = true`, no `[tool.kivy.android.proguard]` | `doctor` WARNs ("minify is on and the app ships gradle dependencies, but ... declares no keep rules"). The build succeeds; the dex has no `androidx/media3` classes (`ExoPlayer$Builder`, `MediaItem` absent; `org.jnius` kept by kivyforge's baseline). **7 of 9 fail**: every video fires `on_error` with the missing-dependency message, and nothing crashes. `provider` passes, and `thumbnail` passes because it uses the platform's `MediaMetadataRetriever`, not Media3. |
| `keep = ["-keep class androidx.media3.** { *; }"]` | **9 of 9 pass**, the same `on_error` messages as the debug runs, the picture upright (`evidence/pixel8a_release_r8_h264.png`). The generated `proguard-rules.pro` holds the rule under `# [tool.kivy.android.proguard].keep`; the dex has the Media3 classes again (one `classes.dex`, 3.9 MB against 0.98 MB without the rule). |

So the one keep rule the docs give is enough: pyjnius's proxies for the
provider's `PythonJavaClass` Runnable, and the framework classes it uses
(`SurfaceTexture`, `Surface`, `Handler`, `MediaMetadataRetriever`), need
nothing more.

**The missing-dependency message misleads under R8.** It says "Add the Maven
dependency androidx.media3:media3-exoplayer", but here the dependency is in
the build and R8 removed it. The message should also name the R8 keep rule.

Summaries: `evidence/pixel8a_release_r8_summary.txt`,
`evidence/pixel8a_release_r8_norule_summary.txt`. Not repeated on the
emulator, and `rules_files` wasn't tried (kivyforge's own run covers it).

## Harness lessons (test app only)

- Android's stdout redirect logs each `write()` as its own logcat line, and
  `print(a, b)` writes each argument separately: markers must be one string.
- Kivy's `Clock` holds a scheduled bound method weakly; an object that only
  schedules itself is collected at once and never ticks.

## Not covered

- An APK built without the Media3 dependency. The R8 run without a keep rule
  takes the same path on a device (no Media3 classes in the dex), and the
  missing-dependency `on_error` fired as designed.
- Buildozer / python-for-android builds, HEVC, 60 fps, DASH, `content://` from a
  real provider (only the not-found case), API 24, long playback and memory.
- A GL context loss on resume: neither device lost it, so the surface rebuild
  in `_on_app_resume` ran but its loss path is unexercised.
