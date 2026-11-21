# GStreamer provider: spikes 1 to 3 (core, GL hand-over, controls)

Date: 2026-11-20. Design: `GSTREAMER-PROVIDER-DESIGN.md` (section 13).
Code: Kivy branch `gstreamer-video-provider` (from upstream master
`ab0c7f1f5`), uncommitted when this was written. Harnesses and results:
`gstreamer-spike/core_headless.py`, `provider_check.py`, `pack_hls.py`,
`results/core-*.json`, `results/check-windows-*.json`,
`results/pi5-provider/`.

The spikes were run on the real implementation rather than a throwaway:
`kivy/lib/gstplayer/_gst_loader.h` (runtime loader), `_gstplayer.pyx`
(core), `_runtime.py` and `_policy.py` (pure Python),
`kivy/core/video/video_gstplayer.py` (provider) and
`kivy/core/audio_output/audio_gstplayer.py` (audio port).

## Verdict

| Spike | Question | Answer |
|---|---|---|
| 1 | Does the runtime-loaded C/Cython core work, with `gst_init` on a worker thread? | **Yes**, on Windows (official wheels 1.28.7), Pi OS Trixie (1.26.2) and Ubuntu 26.04 under WSL2 (1.28.2). No PyGObject, no GStreamer at build time, no compiler warnings |
| 2 | Context hand-over with `gst_element_set_context()` + `gst_gl_display_ensure_context()` instead of a bus sync handler? | **Yes**, on WGL (Windows) and EGL (Pi 5, Wayland and X11). DMABuf still reaches `glupload` on the Pi: 4K HEVC at 30 fps, 8% of one core |
| 3 | Paused frames, seeking, looping, stop, reduced flags through the `Video` widget? | **Yes**, every check on every path and platform tried. The reduced flags need one Windows exception (text flag, below) |

## Spike 1: the core

Platforms: Windows 11, RTX 2060, Python 3.12, only the four wheels the
`gstreamer` extra will install (`gstreamer-libs`, `-plugins`,
`-plugins-libs`, `-ext-runtime` 1.28.7; no `gstreamer-meta`, no
`gstreamer-python`); Pi 5, Pi OS Trixie, Python 3.13, distro GStreamer;
WSL2 Ubuntu 26.04, Python 3.14, distro GStreamer (only the extension
compiled, by hand).

| | Windows | Pi 5 | WSL2 |
|---|---|---|---|
| GStreamer found | `gstreamer-libs wheel` | `system` | `system` |
| Import (Kivy + loader) | 0.53 s | | |
| `init_start()` returns in | 0.25–0.32 ms | 0.22 ms | 0.40 ms |
| First-run init (empty registry), on the worker | 3.4–13.2 s | 5.1 s | 1.6 s |
| Warm init | 0.06 s | 0.01 s | 0.01 s |
| Longest gap of a 60 Hz loop during first-run init | 17.6–19.9 ms | 16.7 ms | 17.0 ms |
| Struct self-test (GstMessage head) | pass | pass | pass |
| `gi` imported | no | no | no |

Headless cases (`core_headless.py --all`, nv12 sink, frames pulled but not
uploaded) behave as in the PyGObject spike, with better error text:

| Case | Windows | Pi 5 | WSL2 |
|---|---|---|---|
| local H.264 / HEVC 1080p and 2160p, VP9 | play (d3d12 decoders) | play (HEVC 2160p on `v4l2slh265dec`) | play (`nvh264dec` through WSL's GPU) |
| missing file | `Resource not found.` | same | same |
| not a video | `This appears to be a text file` | same | same |
| corrupt | `This file contains no playable streams.` | same | same |
| HTTP 404 | `Not Found (HTTP 404)` (from `gst_message_parse_error_details`) | same | same |
| DNS failure | `Internal data stream error. (streaming stopped, reason error (-5))` (spike 11 still open) | same | same |
| HLS TS (Mux) | `missing GStreamer plugin: MPEG-2 Transport Stream demuxer`, then **abort** (below) | plays | plays |
| HLS fMP4 (Apple) | plays only with the text flag (below) | plays | plays |
| DASH | plays | plays | plays |
| HLS 404 | `Not Found (HTTP 404)` | same | same |
| local HLS, server killed at 4 s | `Couldn't download fragments` 6.2 s later (buffer drains first) | 0.04 s later | 0.05 s later |

Idle tick (bus drain + pull attempt): 8–30 µs. Teardown: 0.2–90 ms.

### Findings that change the design

1. **The wheels need `GIO_EXTRA_MODULES`.** HTTPS failed with "Internal
   data stream error" until it was set: the TLS backend (`gioopenssl.dll`)
   is a GIO module in `gstreamer_plugins_libs/lib/gio/modules`. The
   wheels' environment provides it; the loader now applies it.
2. **Kivy's extras won't run the wheels' `.pth`.** It comes from
   `gstreamer-meta`, which the four-wheel `gstreamer` extra doesn't
   install. So the loader applies `gstreamer_libs.gstreamer_env()` itself:
   `PATH`, `GST_PLUGIN_PATH_1_0`, `GST_PLUGIN_SYSTEM_PATH_1_0`,
   `GST_PLUGIN_SCANNER_1_0`, `GST_REGISTRY_1_0` (never overriding a user
   value: it's a single path), `GIO_EXTRA_MODULES`, `XDG_*`. It registers
   the DLL directories with `os.add_dll_directory` and puts them on
   `PATH`, because GLib loads plugins with the normal search.
3. **The self-test runs after `gst_init`, on the worker**, not at import:
   creating a message before initialization isn't something to rely on.
   Import only opens the libraries and checks the version floor.
4. **`appsink` posts EOS only once its queued sample is taken.** The
   provider pulls every tick while playing, so it is unaffected; tests must
   pull too.

### Windows-only upstream problems (1.28.7 wheels)

None of these happen on Linux with 1.26.2 or 1.28.2.

* **libsoup abort.** An HLS stream whose segments can't be demuxed (TS
  without `gstreamer-plugins-restricted`) reports the missing plugin
  correctly, then `gst_element_set_state(NULL)` aborts the process:
  `libsoup:ERROR:../libsoup/soup-session.c:1529:message_completed:
  assertion failed: (item->context == soup_thread_default_context ())`.
  Reproduced 3/3 with the core, and with the PyGObject spike
  (`gst_headless.py`, `GST_PLUGIN_FEATURE_RANK=tsdemux:NONE`), so it is
  not Kivy's code. Going to READY first, or waiting 1 s, doesn't help. On
  the Pi and WSL the same rank override gives a clean error. Ordinary
  teardowns during HLS/DASH downloads, and errors such as a 404 or a dead
  server, don't trigger it. This needs an upstream report; until it's
  fixed, HLS TS on Windows needs `kivy[gstreamer-restricted]` or it can
  crash on error.
  (A detour: worker-thread init first looked like the cause, because the
  first failing runs all used it. Main-thread init and the PyGObject spike
  fail the same way.)
* **Stall with a subtitle track.** Apple's fMP4 HLS example has a subtitle
  stream. `playbin3` selects it even with the text flag off, and then with
  the default audio sink (`wasapi2sink`) the clock never starts: position
  stays 0 and no frame arrives, with no error. Video-only plays;
  `directsoundsink` or a `fakesink` audio sink plays; the text flag on
  (playsink's `textoverlay`) plays; a `fakesink` as `text-sink` does not;
  a `select-streams` event for audio+video had no visible effect. So on
  Windows the text flag stays on (as in the PyGObject spike, which used
  the defaults); on Linux it stays off, where a subtitle overlay would be
  blended on the CPU and break zero-copy. Subtitles are then burned in on
  Windows when a stream has them, and the wheels' Pango logs
  `pango_font_description_get_family` criticals. Worth an upstream report
  and a better fix (stream selection) later.
* **Two D3D12 VP8 decoders at once** failed once with "No valid frames
  decoded before end of stream" on the second; it passes alone and with
  software decoders. Seen only with the generated VP8 test clips.

## Spike 2: GL context hand-over

The GL path sets the display context and `gst.gl.app_context` on the
`playbin3` with `gst_element_set_context()`; the bin hands them to the
GL elements it gets later and answers their NEED_CONTEXT from them (GstBin
source, 1.28). GStreamer's own context is created with
`gst_gl_display_ensure_context()` (WGL: Kivy's context released around the
call). Nothing runs on GStreamer's threads: there is no bus handler and no
signal.

| Platform | Context | Result |
|---|---|---|
| Windows, desktop GL (RTX 2060) | WGL, `opengl,opengl3` | shared textures; read-back identical to `nv12` |
| Windows, ANGLE | EGL | `auto` logs "Kivy's context is EGL (ANGLE); GStreamer's Windows GL library only shares with WGL" and uses `nv12` |
| Pi 5, Wayland | EGL, desktop GL 3.1 (V3D 7.1) | `glupload` input `video/x-raw(memory:DMABuf)` from `v4l2slh265dec` |
| Pi 5, X11 + `SDL_VIDEO_FORCE_EGL=1` | EGL | same as Wayland |
| Pi 5, X11 | GLX | works; the provider warns that the hardware decoder's frames are copied |

## Spike 3: controls through the `Video` widget

`provider_check.py` drives `kivy.uix.video.Video` with
`options={'render': ...}`, 5 s of playback per case.

* **First frame without playing**: yes, everywhere (`on_load` with
  state `''`), 0.13–0.96 s after load on Windows.
* **Paused seek** updates the picture: yes, 0.03–0.21 s.
* **50 seeks in 50 frames**: 3 to 12 pipeline seeks, always ending exactly
  on the last target (`SeekQueue`: one in flight, the newest queued).
* **Loop** restarts from EOS (flushing seek from EOS) and keeps playing
  (30 frames in the next second); **stop** returns to position 0.
* **Reduced flags** (no `soft-colorbalance`; no `text` on Linux): DMABuf
  still passes on the Pi.

Playback, CPU as % of one core (process time / wall time):

| Platform, clip | `gl` | `nv12` | `rgba` |
|---|---|---|---|
| Windows WGL, 1080p H.264 | 30 fps, 15% | 30 fps, 14% | 30 fps, 25% |
| Windows WGL, 2160p HEVC | 30 fps, 20% | 30 fps, 29% | 30 fps, 89% |
| Windows ANGLE (`auto` = nv12), 1080p / 2160p | | 30 fps, 32% / 67% | |
| Pi 5 EGL, 2160p HEVC (hardware) | **30 fps, 8%** | 4 fps, 92% | |
| Pi 5 EGL, 1080p H.264 (software) | 30 fps, 66% | 30 fps, 43% | |
| Pi 5 EGL, 360p H.264 | 30 fps, 24% | 30 fps, 12% | |
| Pi 5 GLX, 2160p HEVC | 3.7 fps, 88% (warning logged) | 4 fps, 91% | |

The Pi's software-decoded rows repeat the spike's finding that `nv12`
beats `gl` there (spike 4, per-stream selection). The Pi `nv12` 4K HEVC
rate (4 fps) is lower than the PyGObject spike's 7: not investigated; the
`videoflip` now in the CPU sinks is a suspect.

### Bugs found and fixed on the way

* `Clock.unschedule(self._update)` (to drop VideoBase's 30 Hz event) also
  removed the provider's own every-frame event on the same callback. The
  tick is now `_on_tick`.
* The NV12 shader centred chroma on 0.5 instead of 128/255: +1 level on
  R and B. After the fix `nv12` and `gl` read back identically.

## Tests

`kivy/tests/test_gstplayer_policy.py` (34, no GStreamer needed) and
`kivy/tests/test_video_gstplayer.py` (19; clips generated with
`videotestsrc ! vp8enc ! webmmux` through the new `run_pipeline()`):
53 pass on Windows, twice in a row, and on the Pi 5 under both Wayland and
X11 (59 there, with `test_audio`). Existing video, VideoPlayer, Media
Foundation and audio tests pass. The ported audio provider
(`KIVY_AUDIO=gstplayer`): length, play to end, loop, stop.

## Follow-up, same day: the Pi nv12 rate, spikes 10 and 11

### Pi `nv12` 4K HEVC: `videoflip` was the cost

`core_headless.py` with `CORE_FLIP=0|1` on the Pi 5, 8 s each:

| Clip | sink | with `videoflip` | without |
|---|---|---|---|
| 4K HEVC (hardware) | nv12 | 29 frames, 89% CPU | **57 frames**, 87% |
| 4K HEVC | rgba | 28 frames | 29 frames |
| 4K H.264 (software) | nv12 | 235 frames, 114% | 236 frames, **94%** |

`videoflip video-direction=auto` costs a full frame pass even with no
orientation tag. It is now off by default (`GstPlayer(flip=False)`), which
restores the PyGObject spike's 7 fps. Rotation (D12) moves to Kivy: read the
`image-orientation` tag and rotate when drawing (the NV12 path already draws
through an Fbo); spike 9.

### Spike 10: thumbnails

`thumb_check.py`, Windows 11, official wheels (`results/thumbs-windows.json`;
Pi in `results/thumbs-pi5.json`):

| Case | Time | Result |
|---|---|---|
| 1080p H.264 at 0 / 10 / 19.9 s | 1.31 (first call) / 0.14 / 0.17 s | 1920x1080 |
| 1080p, 60 s (past the end) | 0.17 s | last frame (after a fix: it returned None) |
| 2160p HEVC at 5 s | 0.18 s | 3840x2160 |
| 360p at 5 s, `size=(160, 90)` | 0.19 s | 160x90 |
| VP9 at 2 s | 0.44 s | 1920x1080 |
| local HLS (CMAF) at 5 s | 0.53 s | 1920x1080 |
| progressive HTTP at 5 s | 10.1 s (the bound) | None: Python's test server has no Range support and the MP4's index is at the end, so this says nothing about the provider |
| missing file / not a video / corrupt | 0.002 / 0.06 / 0.01 s | None |
| connection refused | 2.0 s | None |

Pi 5: 0.11–0.82 s for the same local and HLS cases, failures in 1–7 ms.
Never raised and never exceeded the 10 s bound. Done, apart from a
Range-capable server for the HTTP case. After these changes the tests pass
on the Pi again (60, Wayland and X11).

### Spike 11: DNS, refused connection, TLS

The bus has nothing better: with libsoup 3, `souphttpsrc`
(`_session_send_cb`, 1.28 source) gets the failure as a `GError` and only
logs it at DEBUG ("Sending message failed: ..."), then returns
`GST_FLOW_ERROR`, so `basesrc` posts "Internal data stream error". The
libsoup 2 path did post "Could not resolve server name." and friends. That
is an upstream gap worth a patch (post the `GError` message with
`GST_ELEMENT_ERROR`).

Kivy now maps the generic error from a `souphttpsrc` with no HTTP status to
"Could not connect to the server (the host name could not be resolved, the
connection was refused, or the secure connection failed)". Checked on
Windows with a DNS failure, a refused port, `expired.badssl.com` and
`self-signed.badssl.com` (so certificate checks work with the wheels' TLS
module). HTTP errors keep their status: `Not Found (HTTP 404)`.

## Spike 9: rotation, pixel aspect ratio, colour (2026-11-20)

With `videoflip` gone from the sinks, rotation is done by Kivy. The core
reports the video sink's `image-orientation` tag (base sinks post the tags
they receive as TAG messages; only those from Kivy's appsink are used). The
provider maps the tag to a corner permutation (`_policy.py`) and draws the
frame turned: in the NV12 path inside the Fbo it already uses, in the GL and
RGBA paths with one extra Fbo pass, only for rotated or non-square-pixel
streams. Non-square pixels widen (or heighten) the picture so no detail is
lost. Thumbnails keep `videoflip` (one frame).

`orient_check.py` generates SMPTE-bar clips with GStreamer: one MP4 per tag
value (`taginject` before `mp4mux`, read back by `qtdemux`), WebM with 2:1
and 1:2 pixels, and WebM tagged BT.601, BT.709 and full-range BT.601
(generated from RGB, so `videoconvert` does the YUV conversion). Each path's
first frame is read back on a 16x16 grid and compared with GStreamer's own
rendering (`videoflip` auto + `videoconvert`, aspect applied) and, for the
colour clips, with the source written as a lossless PNG.

| | Windows (RTX 2060, 1.28.7) | Pi 5 (EGL, 1.26.2) |
|---|---|---|
| 8 orientation tags x gl / nv12 / rgba: size | all as expected (W x H or H x W) | same |
| ... picture vs `videoflip` | 1.43 levels (gl, nv12), 0.0 (rgba) | 1.52 / 0.01 |
| 2:1 and 1:2 pixels: size | 640x180, 320x360 | same |
| BT.601 vs source | gl/nv12 2.58, GStreamer 3.86 | |
| BT.709 vs source | gl/nv12 2.74, GStreamer 3.91 | |
| full-range BT.601 vs source | gl/nv12 2.28, GStreamer 2.44 | |

(The ~1.5-level difference between the GPU paths and `rgba` is rounding in
the conversion. The Pi's colour row used the first clip generator, which
labelled `videotestsrc`'s limited-range YUV as full range; its BT.601/709
numbers matched Windows'.)

Bug found on the way: the two transposes were swapped. GStreamer's
`flip-rotate-N` is "mirror, then turn N degrees clockwise"; the first
mapping turned first. Both now match `videoflip`; a unit test checks the
composition.

Not covered: 10-bit HEVC (no 10-bit encoder in the test setup) and BT.2020.

## Spike 8: packaging with kivyforge (2026-11-20)

App: `gstreamer-pack/` (`src/main.py` plays a bundled clip, Apple's fMP4 HLS
over HTTPS, DASH over HTTPS and an HTTPS 404, writes `packcheck.json`, quits).
Dependencies: `kivy[gstreamer]==3.0.0.dev0`, with a cp312 wheel built from
the branch in `wheels/`. kivyforge 0.1.1.dev50 (`lock`, `package -p windows`),
bundled CPython 3.12.10.

### Windows

* **It works as a bundle.** The lock resolved the four official wheels from
  the extra (17 packages); `package` took 64 s. The bundled app found
  GStreamer in its own `site-packages` (`gstreamer-libs wheel`), applied the
  wheels' environment itself (no `.pth` from `gstreamer-meta`), played the
  local clip, HLS fMP4 and DASH on the `gl` path, and reported
  `Not Found (HTTP 404)`: HTTPS works from the bundle (`GIO_EXTRA_MODULES`).
* **Size:** the bundle is 500 MB; GStreamer is 258 MB of it
  (`gstreamer_libs` 92, `gstreamer_plugins` 111, `gstreamer_plugins_libs` 53,
  `gstreamer_ext_runtime` 2.3).
* **First launch.** GStreamer's init took 12.6 s on the first launch and
  9.8–10.8 s on fresh copies of the bundle in new folders; 0.07 s on later
  launches. With the bundle's files already read once, a full rescan with a
  deleted registry takes only 3.4–3.7 s, so most of the first launch is the
  first read of ~250 newly written plugin DLLs (on-access scanning,
  presumably), not the scan itself. The UI isn't blocked (worker thread), but
  video only starts then. Two mitigations:
  * the provider now starts initialization when it is imported (the app
    selecting its video provider), not on the first `load()`. Measured on
    a fresh copy: init still 11.1 s, so this only hides the app's own
    start-up (Kivy import 1.4 s);
  * an installer could run the scan once at install time (the registry is
    per installation now);
  * a playback allowlist of 28 plugins (`prune_estimate.py`'s list) is
    11.7 MB instead of 166 MB of plugins and scans in 1.2–1.7 s instead of
    3.4–3.7 s. Pruning is kivyforge work.
* **Registry location.** Outside a venv the wheels put the registry in
  `%TEMP%\gstreamer-1.0\wheel-registry-3.12-win-amd64.bin`, one file shared by
  every app on that Python version. The loader now uses one file per
  GStreamer installation, `~\.kivy\gstreamer\registry-win-amd64-<hash>.bin`
  (a user's or `gstreamer-meta`'s `GST_REGISTRY_1_0` still wins). Seen in the
  bundle: `registry-win-amd64-d30fdcc8301a.bin`.
* **Licences.** The wheels' `License` fields are AND-lists of every bundled
  component's licence (17 SPDX ids for the libs and plugins wheels, including
  `GPL-2.0-or-later` and `LGPL-3.0-only`), without saying which component has
  which; `gstreamer-ext-runtime` is the MSVC runtime
  (`LicenseRef-Proprietary`). None of the four wheels contains a single
  licence text. So an app bundling them has nothing to copy its notices from:
  kivyforge would need the texts from upstream (cerbero's recipes), and which
  component is GPL needs asking upstream.
* Not done: the official installer (not on this machine), and pruning itself.

### Linux (Pi 5)

kivyforge's Linux AppImage runs python-build-standalone, so the question was
whether PBS can load the distro's GStreamer without library clashes.

* The aarch64 `dice-roller` AppImage kivyforge built earlier carries 53
  shared libraries and **none** of GLib, GIO, OpenSSL or GStreamer, so the
  system GStreamer would load its own GLib with nothing to clash with.
* PBS CPython 3.13.14 (the build kivyforge caches) with the branch's Kivy
  wheel: the provider loaded the system GStreamer 1.26.2 and played HTTPS
  DASH (165 frames in 6 s). TLS went through the system's
  `libgiognutls.so`; PBS's own OpenSSL 3.5.7 is static inside its `_ssl`
  module and coexisted with the system `libssl.so.3` that was also loaded.
* Windowed under PBS this ad-hoc setup failed before GStreamer mattered:
  SDL3 (linked from outside the wheel) didn't start, Kivy fell back to its
  `x11` window, which died with an X `BadAccess`. A real AppImage needs a
  manylinux aarch64 Kivy wheel from the branch (with SDL3 inside), which CI
  will produce; that run is still to do, and it is the community test app's
  base.

### Also seen

The `Video` widget (not the provider) logs `Image: Not found <url>` for every
stream: with no `preview`, `Video.texture_update()` passes `source` to
`set_texture_from_resource()`. Upstream widget issue for all providers.

## Spike 4: render path per stream (2026-11-20)

No sink that switches branches on the caps event: that needs a pad probe or
signal callback on a streaming thread, which the design avoids. Instead
`auto` re-plans once: on Linux it starts on the GL path; at the first
preroll (`ASYNC_DONE`) the provider reads the decoder's output caps
(`describe()`), and if they are plain `video/x-raw` (system memory, a
software decoder) it tears the pipeline down and rebuilds it on the nv12
path. DMABuf, GL or other GPU memory stays on GL. A seek requested before the
first preroll waits for the new pipeline. Windows doesn't re-plan: there
`gl` was as cheap as or cheaper than `nv12` for system-memory frames
(1080p 15% vs 14%, 4K HEVC 20% vs 29%).

Pi 5, Wayland/EGL, `provider_check.py` with `auto`, `gl` and `nv12`
(`results/pi5-spike4/`), 6 s of playback, CPU in % of one core:

| Clip (decoder) | `auto` became | `auto` | `gl` | `nv12` | load to first frame (auto / gl / nv12) |
|---|---|---|---|---|---|
| 4K HEVC (`v4l2slh265dec`, DMABuf) | gl | 30 fps, 8.0% | 30 fps, 7.5% | 7.3 fps, 89.5% | 0.29 / 0.18 / 0.38 s |
| 1080p H.264 (`avdec_h264`) | nv12 | 29.8 fps, 40.3% | 30 fps, 65.8% | 29.8 fps, 38.0% | 0.21 / 0.13 / 0.08 s |
| 360p H.264 | nv12 | 12.1% | 24.6% | 12.3% | 0.13 / 0.10 / 0.05 s |
| 1080p VP9 (`vp9dec`) | nv12 | 25.2 fps, 134.5% | 21.3 fps, 172.2% | 25.1 fps, 134.2% | 0.28 / 0.17 / 0.09 s |

`auto` now gets both best cases the spike asked for (HEVC at 8%, H.264 at
40%); the price is a second preroll for software-decoded streams, 0.1–0.2 s
more before the first frame. Paused seeks and looping pass on every row.
Adopted into D6. The rule is measured on the Pi only; x86 Linux (the Intel
laptop, spike 6) should confirm it.

## Spike 5: GLES context, KMSDRM (2026-11-20)

**GLES.** On Linux, Kivy 3.0's SDL3 window only asks for a GLES context
with `KIVY_GL_BACKEND=angle_sdl3`. On the Pi 5 (Wayland, and X11 with EGL)
that window fails to open, with no error logged (kivy/kivy#9539 adds the
`SDL_Init` check that would say why), Kivy falls back to its legacy `x11`
window, and that dies with an X `BadAccess` in `X_GLXMakeContextCurrent`.
So no Kivy 3 configuration on Linux produces a GLES context today; every Pi
run so far wrapped desktop GL 3.1 (Mesa V3D) as `opengl,opengl3`. The
provider's GLES branch (`GLShare(gles=True)`, API `gles2`) stays untested
until Kivy can create one. A Kivy limitation, not the provider's.

**KMSDRM: works, same as Wayland.** Run with the desktop stopped
(`kms_all.sh`, started with `sudo systemd-run` so it outlived the session;
the check itself ran as the normal user, which got the display through the
`video` group). SDL3's `kmsdrm` backend gave Kivy an EGL context with desktop
GL 3.1 (V3D 7.1), and the provider shared it (`results/pi5-spike5/kmsdrm.json`):

| Clip | `auto` became | `auto` | `gl` | `nv12` |
|---|---|---|---|---|
| 4K HEVC (`v4l2slh265dec`) | gl, DMABuf into `glupload` | 30.1 fps, 8.1% | 30.1 fps, 7.3% | 7.3 fps, 89.3% |
| 1080p H.264 (`avdec_h264`) | nv12 | 30.0 fps, 41.3% | 30.1 fps, 66.6% | 30.0 fps, 39.5% |

First frame without playing, paused seeks, the 50-seek burst (3–5 pipeline
seeks), looping and stop all pass; colours read back the same as under
Wayland. So the zero-copy path doesn't depend on a compositor: a kiosk-style
Pi app without a desktop gets it too.

Spike 5 is done apart from GLES, which waits for Kivy (above).

## Spike 6: x86 Linux with Intel graphics (2026-11-21)

Dell XPS 9320 (Core i7-1260P, Iris Xe, Alder Lake GT2), Ubuntu 26.04.1
desktop booted live from USB (GNOME, Wayland), GStreamer 1.28.2 from the
distro plus `gstreamer1.0-plugins-bad`, `-libav`, `-gl` and
`intel-media-va-driver` (iHD 26.1.2: H.264, HEVC and VP9 decode; no AV1
`vaav1dec` with that driver build). Kivy 3 built on the laptop from the
branch. Results in `results/intel-xps9320/`.

**Headless:** every case passes: VA decoders picked automatically
(`vah264dec`, `vah265dec`, `vavp9dec`), HLS TS/fMP4, DASH, HTTP 404, the
new "Could not connect to the server" text for DNS, the dead HLS server
(0.05 s), first-run init 0.3 s.

**Zero-copy works on Intel.** With an EGL context (Wayland, and X11 with
`SDL_VIDEO_FORCE_EGL=1`), every hardware-decoded stream reaches `glupload`
as DMABuf. CPU in % of one core, 6 s of playback:

| Clip (decoder) | Wayland `gl` | Wayland `nv12` | X11+EGL `gl` | X11+GLX `gl` |
|---|---|---|---|---|
| 4K H.264 (`vah264dec`) | 30 fps, 7.2% | 20.4% | 5.8% | 18.9% |
| 4K HEVC (`vah265dec`) | 30 fps, 6.3% | 21.0% | 5.7% | 19.6% |
| 1080p H.264 | 5.5% | 9.4% | 5.1% | 9.9% |
| 1080p VP9 (`vavp9dec`) | 4.7% | 20.7% | 4.1% | 15.5% |
| 360p H.264 | 3.8% | 3.5% | 3.5% | 4.2% |

GLX can't import DMABuf, so there the decoder's frames are copied to
system memory and cost 3 to 4 times more; the provider's GLX warning fires.
All rows: first frame without playing, paused seeks, loop, stop pass. The
first video of each run read higher under `auto` (12.6–14.9% for 4K H.264
against 5.8–7.2% under `gl`, same path) and the same clip on its own run
didn't: warm-up, not the path.

**Colour on Iris Xe:** BT.601, BT.709 and full range within 2.3–2.7 levels
of the source in `gl` and `nv12`, as on the RTX 2060 and the Pi.

**A fix from this run.** Under GLX, `auto` saw system-memory frames and
rebuilt on `nv12`, logging "vah264dec decodes in software", which was
wrong (hardware decode, copied out) and no cheaper (4K H.264 19.1% vs
18.9%; VP9 20.8% vs 15.5%). The re-plan now also needs a software decoder
(decoder name not starting with a hardware prefix); rechecked: GLX stays on
`gl`, Wayland unchanged.

**Practical notes for the test setup:** the live session suspended after
idling and dropped Wi-Fi; masking the sleep targets and turning off Wi-Fi
power saving fixed it. GNOME's Xwayland needs
`XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.*` for apps started over
SSH.

Not covered: AV1 (no VA AV1 decoder in this driver build), AMD and NVIDIA
(spike 6's other half), Intel software decoding.

## Spike 7: GStreamer 1.22 and 1.24 (2026-11-21)

Podman containers in WSL2: `debian:12` (GStreamer 1.22.0, Python 3.11.2) and
`ubuntu:24.04` (GStreamer 1.24.2, Python 3.12.3), each with the distro's
`gstreamer1.0-plugins-base/-good/-bad/-libav/-gl`. Only the `_gstplayer`
extension was compiled (Kivy's graphics weren't), so no window tests: the
headless cases, the window-free tests (`test_gstplayer_policy.py` and
`GstPlayerCoreTestCase`, with `kivy.tests` stubbed) and the loader's view of
the GL entry points. Results in `results/spike7/`.

| | Debian 12, 1.22.0 | Ubuntu 24.04, 1.24.2 |
|---|---|---|
| loads, version check, message-layout self-test | pass | pass |
| `has_gl()` (GL library and 1.24's `gst_gl_display_ensure_context`) | False: `auto` uses nv12 | True |
| first-run init (fresh registry) | 0.35 s | 0.32 s |
| headless cases | all as on 1.26/1.28 | all, except HLS fMP4 (below) |
| window-free tests | 82 passed | 82 passed |

Every case matched the newer versions: local H.264/HEVC/VP9, missing,
non-video and corrupt files, `Not Found (HTTP 404)`, the connection-failure
text, HLS TS, DASH, and the dead HLS server (error 0.04–0.05 s later).

**So the floors stand: 1.22 on Linux and the Pi (CPU path), 1.24 for the GL
path.** The provisional decision (D15) is now final; Debian 12 and Pi OS
Bookworm keep a working provider without the zero-copy path.

Two things seen only in containers without sound:

* **Ubuntu 24.04: `openalsink` aborts the process.** `autoaudiosink` chose
  OpenAL Soft, which aborted (exit 134) when it couldn't reach PipeWire;
  `gst-launch-1.0 playbin3` hung the same way, so it isn't Kivy's code. With
  `GST_PLUGIN_FEATURE_RANK=openalsink:NONE` everything passed. A desktop has
  PipeWire, so this matters for headless machines (kiosks, CI). Kivy's CI
  test clips have no audio track, so no audio sink is created there.
* **Ubuntu 24.04: Apple's fMP4 HLS stream showed no frames, no error.** Same
  symptom as the Windows 1.28.7 subtitle/`wasapi2sink` stall, but with no
  working audio output here this run can't tell which; Debian 12 played it.
  Needs a run on a real 24.04 desktop.

### Windows: the official 1.24.13 installer

`gstreamer-1.0-msvc-x86_64-1.24.13.msi` (102 MB, checksum verified),
installed with `ADDLOCAL=ALL` (the complete set, restricted plugins
included). It installed to `D:\gstreamer\1.0\msvc_x86_64\` (the drive with
the most space, not `C:`) and set `GSTREAMER_1_0_ROOT_MSVC_X86_64` for the
machine; PATH is unchanged. The run used a clean Python 3.12 venv with
Kivy's SDL3/GLEW/ANGLE deps and no GStreamer package, so
`find_runtime()` reported `GSTREAMER_1_0_ROOT_MSVC_X86_64=...` as the source.
Results in `results/spike7/*-windows-installer-1.24.13*.json`.

* **Headless:** every case passes, matching the 1.28.7 wheels: D3D11
  decoding of H.264/HEVC up to 4K, VP9 in software, the error texts,
  HTTP 404, HLS TS (`tsdemux` is in the complete install), DASH, the dead HLS
  server (6.2 s to the error, as on 1.28.7: Windows behaviour, not version).
  First-run init with a fresh registry 3.8 s, UI loop's longest gap 17.4 ms.
* **Apple's fMP4 HLS stream plays on 1.24.13** (693 frames, text flag on),
  where 1.28.7 stalls. So the subtitle/`wasapi2sink` stall is a regression
  after 1.24: worth putting in the upstream report.
* **Tests:** `test_gstplayer_policy.py` and `test_video_gstplayer.py`, 95
  passed (windowed tests included).
* **Windowed check** (`provider_check.py`, glew): `gl`, `nv12` and `rgba` all
  work on the three clips: first frame without play, paused seek, the 50-seek
  burst (4–10 pipeline seeks), 30 fps, loop, stop. Pictures match 1.28.7
  within 2 levels.

**CPU is higher than on 1.28.7 because of the decoder, not Kivy.** 1.24 ranks
the `d3d11*dec` decoders first; 1.28 picks `d3d12*dec`. Both download frames
to system memory. Forcing D3D12 on 1.24.13
(`GST_PLUGIN_FEATURE_RANK=d3d12h265dec:MAX,d3d12h264dec:MAX`) brings it back
in line (percent of one core, 30 fps throughout):

| | 1.28.7 wheels (D3D12) | 1.24.13 (D3D11, default) | 1.24.13, D3D12 forced |
|---|---|---|---|
| 4K HEVC, `gl` | 20% | 55–58% | 18% |
| 4K HEVC, `nv12` | 29% | 64–68% | 15% |
| 1080p H.264, `gl` | 15% | 25–26% | 14% |
| 1080p H.264, `nv12` | 14% | 24–31% | 14% |

A cost of the old version, not a failure: the floor stands. Kivy could raise
the D3D12 ranks itself on 1.24 and 1.25, but D3D12 in 1.24 is less
exercised, so it isn't done; users of the installer can set the variable.

**Bug found and fixed: libsoup 2's transport codes shown as HTTP.** The 1.24
installer ships libsoup 2.4, which reports transport failures as status
codes below 100 in the same field (2: name not resolved, 4: can't connect).
`error_text` appended them: `Could not resolve server name. (HTTP 2)`,
`Couldn't download fragments (HTTP 4)`. Now only statuses of 100 and above
are appended (libsoup 2's message already names the failure), with a test.

**Problem found: Kivy 2's `kivy_deps.gstreamer` leaks into an installer
runtime.** A venv upgraded from Kivy 2 still has `kivy_deps.gstreamer`
(0.3.4, GStreamer 1.18.6). Kivy 3's `__init__` still imports every
`kivy_deps` package, and that one prepends its `bin` to PATH and sets
`GST_PLUGIN_PATH` (its 216 plugins live in `bin`) and `GST_REGISTRY`.
GStreamer reads those when the `_1_0` variants aren't set, and searches
`GST_PLUGIN_PATH` first, so with the 1.24.13 installer
`gst-inspect-1.0 coreelements` reported
`.venv\share\gstreamer\bin\gstcoreelements.dll`, version 1.18.6: 1.18
plugins in a 1.24 process. Playback still worked here, by luck. The wheels
are immune (they set `GST_PLUGIN_PATH_1_0`, `GST_REGISTRY_1_0`, which take
precedence); `KIVY_GSTREAMER_ROOT`, the installer and PATH runtimes aren't.
**Fixed:** `kivy/__init__.py` no longer imports `gstreamer` or
`gstreamer_dev` from `kivy_deps` (or the older `kivy.deps`); it logs
`Not importing "kivy_deps.gstreamer": Kivy 3 no longer uses it. Uninstall
it`. The 0.3.3 PATH patch for those packages (`_patch_mod_deps_win`) is
gone with them. In the upgraded venv `GST_PLUGIN_PATH` and `GST_REGISTRY`
stay unset and the old `bin` is off PATH; 96 tests pass in both venvs (a
new one imports Kivy over a fake `kivy_deps` and checks the GStreamer
packages are skipped and the others still load). The migration guide
gives the uninstall command.

First-run init (fresh registry) was 11 s on the first runs of the day and
3.6 s on repeats in both venvs: a cold disk cache, not the old plugins.

## Still open from these spikes


* Upstream reports: the libsoup abort and the subtitle/`wasapi2sink`
  stall (Windows 1.28.7).
* Windows HLS TS without the restricted set can crash on error until
  upstream fixes the abort: document it with the `gstreamer-restricted`
  extra, or find a workaround.
* Pi `nv12` 4K HEVC at 4 fps versus 7 in the PyGObject spike.
* The unexplained no-output run (Kivy `gl` backend on Wayland) and GL
  context recreation (fullscreen toggle) were not repeated.
