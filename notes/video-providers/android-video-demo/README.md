# Android video demo

A Kivy app that plays a dozen video formats through the Media3 ExoPlayer
provider (`kivy.core.video.video_android`, branch `android-exoplayer-video`,
kivy/kivy#9534). Pick a source; Kivy's `VideoPlayer` gives play/pause, stop,
seek and volume (double-tap for full screen), and the panel under it shows the
texture size, state, position/duration and any `on_error` message.

| Group | Sources |
|---|---|
| In the app | H.264 MP4; the same clip tagged as portrait (90 degree rotation) |
| Progressive download | VP9 WebM, AV1 MP4, H.264 in Matroska, MPEG-TS |
| HLS | MPEG-TS segments, fMP4 segments (Apple test streams) |
| DASH | H.264, HEVC, VP9 (Tears of Steel), H.264 1080p60 |

The network sources are the public test media that ExoPlayer's own demo app
lists (`media.exolist.json` in androidx/media), so they need a connection.

## Build and run

```bash
python prepare.py
kivyforge lock -p android
kivyforge run -p android --device
```

`prepare.py` copies the Kivy wheels and the two clips from
`../android-provider-check`; build those there first (its `make_clips.py`, and
`make_wheels.py` against a Kivy checkout on `android-exoplayer-video`).

The app depends on `media3-exoplayer`, `-hls` and `-dash` 1.11.1 (compile SDK
36), declares `INTERNET`, and turns R8 on for release builds with
`-keep class androidx.media3.** { *; }` (kivyforge after 0.1.2).

## Result, 2026-11-19

All 12 sources played on a Pixel 8a (Android 16), debug build: picture
upright, the portrait clip turned, position and duration advancing, no
`on_error`. Texture sizes: 640x360, 360x640, VP9 640x360, AV1 854x480, MKV
1280x720, TS 400x300, both HLS 1920x1080, DASH H.264/HEVC/VP9 1918x856
(adaptive, settled at the top rendition), DASH 60 fps 1920x1080. Screenshots in
`evidence/`.

### Release build with R8

`kivyforge run -p android --release --device` (signed with the debug key; no
release key is configured). `doctor` passes "R8 keep rules", Gradle runs
`:app:minifyReleaseWithR8`, and the generated `proguard-rules.pro` carries the
Media3 rule under `# [tool.kivy.android.proguard].keep`. The release dex still
has `ExoPlayer$Builder`, `HlsMediaSource` and `DashMediaSource`.

All 12 sources played again on the Pixel 8a, with the same texture sizes and
no `on_error` (`evidence/pixel8a_release_r8.png`).

| | Debug | Release (R8) |
|---|---|---|
| APK | 41.8 MB | 28.3 MB |
| Dex | 16.6 MB in 6 files | 4.0 MB in 1 file |

The APK difference is not all R8: the release variant also byte-compiles the
Python and strips its source.
