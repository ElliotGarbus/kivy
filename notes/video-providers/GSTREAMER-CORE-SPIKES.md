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

## Still open from these spikes

* Upstream reports: the libsoup abort and the subtitle/`wasapi2sink`
  stall (Windows 1.28.7).
* Windows HLS TS without the restricted set can crash on error until
  upstream fixes the abort: document it with the `gstreamer-restricted`
  extra, or find a workaround.
* Pi `nv12` 4K HEVC at 4 fps versus 7 in the PyGObject spike.
* The unexplained no-output run (Kivy `gl` backend on Wayland) and GL
  context recreation (fullscreen toggle) were not repeated.
