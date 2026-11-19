# GStreamer provider spike -- findings

Spike for the Kivy 3.0 GStreamer video provider (rewrite of
`kivy/core/video/video_gstplayer.py` + `kivy/lib/gstplayer/`,
[[kivy3-video-provider-roadmap]]): primary provider on Linux and Raspberry Pi,
optional secondary on Windows. Prototype code is in
`notes/video-providers/gstreamer-spike/`. Notes only, never part of an upstream
PR ([[notes-stay-out-of-upstream-pr]]). Date: 2026-11-18; Pi 5 and WSL runs,
and Kivy 3.0 on the Pi, 2026-11-19.

**Bottom line.**

1. GStreamer now publishes **official Python wheels on PyPI** (since 1.28.0):
   `pip install gstreamer-bundle` / `gstreamer-meta` gives Windows and macOS a
   complete GStreamer, including PyGObject (`gi`), with no installer. There are
   **no Linux wheels**: on Linux and the Pi, GStreamer and `gi` come from the
   distro.
2. On Windows, with those wheels, every part of the design works:
   `playbin3` + `appsink`, polling the bus and the sink from Kivy's
   thread every tick (no GLib main loop), and `on_error` with useful text for
   missing, corrupt and undecodable files, HTTP 404s and HLS servers that die
   mid-stream. HLS and DASH play through `adaptivedemux2`.
3. **GPU texture sharing works**: Kivy's GL context is wrapped as a
   `GstGLContext`, and Kivy draws GStreamer's texture ids directly. A 4K
   frame then costs 0.25 ms per tick and about 15% of one core, against 7.5 ms
   and 40% for NV12 + shader and 16 ms and 125% for RGBA. It took three
   workarounds for PyGObject and WGL (below).
4. **Recommendation: don't build the provider on `gi`.** Kivy's
   manylinux and Raspberry Pi wheels are built with `USE_GSTREAMER=0`
   today, and kivyforge's Linux AppImage runs python-build-standalone,
   which can't import the distro's `gi`. The GL path also needed ctypes
   anyway. Write the provider as a thin Cython layer that `dlopen`s
   `libgstreamer` **at runtime** (stable 1.x C ABI, no build-time
   GStreamer), and take the *runtime* from the distro on Linux/Pi and from
   the official wheels or installer on Windows. `gst_nogi.py` proves the
   loop works without `gi` (23 µs per tick).
5. **Raspberry Pi 5: hardware HEVC goes into Kivy's GL with no copy, but
   only when Kivy's context is EGL.** `v4l2slh265dec` outputs
   `memory:DMABuf` (`DMA_DRM`) straight into `glupload`. 4K HEVC plays at
   **11% CPU** and 0.3 ms per tick; every other path manages 7 fps at
   110–133%. The decoder's native format is tiled (`NV12_128C8`), so the
   NV12 + shader path and GLX both pay a CPU de-tile. The Pi 5 has no H.264
   hardware decoder: software 1080p H.264 costs about 49% of one core.
   **Kivy 3.0 (SDL3), built on the Pi, confirms it:** the `gl` and `sdl3`
   GL backends behave identically, and only the context type matters. EGL
   (native Wayland, or X11 with `SDL_VIDEO_FORCE_EGL=1`) gives 4K HEVC at
   29 fps on 13–16% CPU; GLX gives 6.5 fps. **But out of the box, Kivy 3.0 on
   the Pi desktop gets X11 + GLX** (SDL3 chose Xwayland), so Kivy should
   request EGL on Linux before creating its window.
6. Stock **Pi OS Trixie desktop already ships the GStreamer runtime**
   (base/good/bad/ugly/libav/gl, all Pi-patched 1.26.2) and `python3-gi`.
   Only the `gir1.2-*` typelibs, which `gi` needs, were missing.

## Environments

Windows run:

| Item | Value |
|---|---|
| Host | Windows 11 Home 10.0.26200, NVIDIA RTX 2060 (driver 610.60, GL 4.6) |
| Python | CPython 3.12.10 (64-bit), isolated venv in the session scratchpad |
| GStreamer | **1.28.7** from PyPI: `gstreamer-meta==1.28.7` (+ `gstreamer-plugins-restricted` for one test), every `gstreamer_*` package pinned to 1.28.7 |
| `gi` | PyGObject **3.50.2** + pycairo 1.27.0, *vendored inside* `gstreamer_python` (not the PyPI PyGObject 3.58.1) |
| Kivy | 3.0.0.dev0 wheel from `mf-check/wheels` (predates kivy/kivy#9528, so the spike polyfills `on_error` / `_do_error`) |
| GL backends | `glew` (Kivy's Windows default, desktop GL over WGL) and `angle_sdl3` |

Linux runs (`run_linux.sh`, Kivy **2.3.2** from PyPI with SDL2, because there is
no Kivy 3.0 Linux build here; the spike polyfills `options`, `on_buffering`
and `nofree`):

| | Raspberry Pi 5 | WSL2 (Windows host above) |
|---|---|---|
| OS | Raspberry Pi OS Trixie (Debian 13), kernel 6.18.60+rpt, labwc Wayland desktop | Ubuntu 26.04 LTS, WSLg |
| Python / `gi` | 3.13.5 / python3-gi 3.50.0 | 3.14.4 / python3-gi 3.56.2 |
| GStreamer | 1.26.2 (Pi `+rpt` builds) | 1.28.2 |
| Kivy GL | Mesa 26.2.7, V3D 7.1 (GL 3.1), via Xwayland: GLX by default, EGL with `SDL_VIDEO_X11_FORCE_EGL=1` | **llvmpipe** (software GL), so WSL render costs are meaningless |
| Video decode | `v4l2slh265dec` (HEVC); `avdec_h264` (H.264, software) | NVIDIA `nvh264dec` / `nvh265dec` via WSL CUDA |

## 1. Official bindings and downloads

### What gstreamer.freedesktop.org ships

Source: [download page](https://gstreamer.freedesktop.org/download/), with
sizes from `Content-Length` on
[data/pkg](https://gstreamer.freedesktop.org/data/pkg/).

* **Windows**: Inno Setup installers for MSVC x86_64 / x86 / arm64 and MinGW
  x86_64 / x86. Since 1.28 they install per-user
  (`%LOCALAPPDATA%\Programs\gstreamer\1.0\<abi>_<arch>`) or system-wide, with
  `/TYPE=runtime|devel|debug` and `/VERYSILENT`. One installer covers all
  install types. `gstreamer-1.0-msvc-x86_64-1.28.8.exe` is **543 MB**;
  arm64 is 316 MB. The installers include the Python bindings: the
  maintainers committed to shipping `gi` `.pyd`s for Python 3.9–3.14t from
  1.28.2 on
  ([discourse](https://discourse.gstreamer.org/t/inquiry-regarding-python-binding-versions-in-windows-msvc-binaries-and-custom-compilation-guidance/5736)).
* **macOS**: universal `.pkg` installers: runtime 157 MB, development 809 MB.
  Not needed for Kivy, where AVFoundation is the provider.
* **Python wheels on PyPI, "starting with 1.28.0"** (download page). Published
  by "GStreamer Project":

| Package | What it is | Windows amd64 wheel (1.28.7/.8) |
|---|---|---|
| `gstreamer-bundle` | meta: everything, including GPL and restricted | 0 |
| `gstreamer-meta` | meta: libs + plugins + python; extras `gpl`, `restricted`, `gpl-restricted`, `cli`, `gtk` | 0 |
| `gstreamer-libs` | core + base/GL/... libraries, GLib, ... | 33.4 MB |
| `gstreamer-plugins` | LGPL plugin set, **including gst-libav (FFmpeg), d3d11/d3d12, opengl, adaptivedemux2, soup, dav1d, vpx** | 46.7 MB |
| `gstreamer-plugins-libs` | third-party libs for the plugins (avcodec, dav1d, openh264, ...) | 21.2 MB |
| `gstreamer-ext-runtime` | Windows redist runtime | 1.0 MB |
| `gstreamer-python` | **PyGObject + pycairo + Gst overrides + typelibs**, per CPython version | 1.0–2.7 MB |
| `gstreamer-plugins-restricted` | patent-encumbered: **mpegtsdemux**, asf, realmedia, rtmp, dvdsub, voaacenc, vvdec | 1.5 MB |
| `gstreamer-plugins-gpl` | GPL: a52dec, dtsdec, dvdread | 0.4 MB |
| `gstreamer-plugins-gpl-restricted` | x264, x265 | 3.4 MB |

  Platforms: **win32, win_amd64, macosx universal2 only**, with `cp39-abi3`
  plus free-threaded `cp313t`/`cp314t` builds of the native packages.
  `gstreamer-python` is per CPython version (3.9–3.14, 3.13t, 3.14t). There are
  **no Linux (manylinux) wheels** and none for arm64 Windows.
  `gstreamer-meta==1.28.7` downloads **118 MB** and installs **284 MB**
  (libs 94, plugins 111, plugin libs 54, python 3.8, runtime 2.4).

  Release-lag hazards seen today: 1.28.9 (2026-11-09) is published for
  **macOS only**, and 1.28.8 is missing the `cp39-abi3` builds of
  `gstreamer-plugins`, `-plugins-libs`, `-restricted` and `-gpl` on Windows.
  The `~=1.28.x` pins inside the meta-packages let pip assemble a **mixed
  1.28.7 / 1.28.8 install**, which happened on the first try. Every
  `gstreamer_*` package must be pinned to one exact version (see
  `gst-constraints.txt` in "Reproducing").

### How the wheels work

* `gstreamer_meta.pth` runs `gstreamer_libs.setup_python_environment()` at
  interpreter start. That prepends the wheels' `bin/` to `PATH` and sets
  `GST_PLUGIN_PATH_1_0`, `GST_PLUGIN_SYSTEM_PATH_1_0`, `GI_TYPELIB_PATH`,
  `GST_PLUGIN_SCANNER_1_0`, `GST_REGISTRY_1_0` and `PYGI_DLL_DIRS`, and puts
  the vendored `gi` on `sys.path`. It even works under `python -I`.
* **It does not call `os.add_dll_directory`.** `gi` copes through
  `PYGI_DLL_DIRS`. Anything else (ctypes, a Cython module) gets
  `FileNotFoundError: gstreamer-1.0-0.dll` until it registers the dirs from
  `gstreamer_libs.gstreamer_env()[1]` itself. `gst_nogi.py` does this.
* Registry: inside a venv, `<venv>/Temp/gstreamer-1.0/registry-win-amd64.bin`.
  Outside one (a kivyforge bundle counts as outside),
  `%TEMP%\gstreamer-1.0\wheel-registry-<pyver>-<platform>.bin`, **shared by
  every app on that Python version**. The first `Gst.init()` took
  **7.3 s** (plugin scan); later ones took 0.06 s.
* Every run logs `Failed to load module ... giolibproxy.dll` (a missing
  dependency in the wheel). It's harmless, but system proxy settings probably
  aren't honoured.

### PyGObject (`gi`) from PyPI vs distro

* PyPI `PyGObject` (3.58.1) is **sdist only** on every platform. `pip install
  PyGObject` compiles: it needs a C compiler, `pkg-config`,
  girepository-2.0 headers (GLib ≥ 2.80) and cairo headers for the pycairo
  dependency. Tried in WSL Ubuntu 26.04 without the dev packages: it fails
  at once with `Dependency "cairo" not found`. On Windows there is no
  supported pip path other than GStreamer's own `gstreamer-python` wheel.
* Distro `gi` (`python3-gi`, Ubuntu 26.04: 3.56.2) only works with the
  **distro's Python**: a venv with `--system-site-packages`, or the system
  interpreter. It can't be used from python-build-standalone, which is what
  kivyforge bundles on Linux.

### Linux and Raspberry Pi packages

| Need | Debian / Ubuntu / Raspberry Pi OS | Fedora |
|---|---|---|
| core + base + GL elements | `libgstreamer1.0-0`, `gstreamer1.0-plugins-base`, `gstreamer1.0-gl` | `gstreamer1`, `gstreamer1-plugins-base` |
| containers, V4L2 stateful decoders (`v4l2h264dec`), souphttpsrc | `gstreamer1.0-plugins-good` | `gstreamer1-plugins-good` |
| adaptivedemux2 (HLS/DASH), v4l2codecs stateless (`v4l2sl*dec`), VA-API `va` | `gstreamer1.0-plugins-bad` | `gstreamer1-plugins-bad-free` |
| H.264/HEVC/AAC software decode | `gstreamer1.0-libav` | needs RPM Fusion (`*-freeworld`); Fedora's own `openh264` repo is limited ([Fedora docs](https://docs.fedoraproject.org/ca/quick-docs/installing-plugins-for-playing-movies-and-music/)) |
| `gi` + typelibs (only if `gi` were used) | `python3-gi`, `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0` | `python3-gobject`, `gstreamer1` (typelibs included) |

* Raspberry Pi OS Trixie carries its **own GStreamer build**,
  `1.26.2-3+rpt3+deb13u1` (Debian Trixie has 1.26.1)
  ([aptpeek](https://aptpeek.dev/raspberrypi/trixie/main/amd64/gstreamer1.0-plugins-bad/1.26.2-3+rpt3+deb13u1)).
  Bookworm shipped 1.22.
* Pi hardware decode: the **Pi 4** has a stateful H.264 decoder
  (`v4l2h264dec`) and a stateless HEVC block. The **Pi 5 has HEVC only**
  (`v4l2slh265dec`), so H.264 is software (`avdec_h264`)
  ([gist](https://gist.github.com/schickling/089f4faf412b5267508f758408f0645f),
  [kernel patch](https://lkml.indiana.edu/2602.3/07802.html)). Known
  regressions to check on hardware: `v4l2slh265dec` broken on Trixie for the
  Pi 4 ([raspberrypi/linux#7137](https://github.com/raspberrypi/linux/issues/7137)),
  and the Pi 5 decoder's DMABuf NV12 modifier rejected by `glupload`, with a
  fix pushed only to the Pi OS GStreamer build
  ([discourse](https://discourse.gstreamer.org/t/qml6glsink-not-compatible-with-dmabuf-from-pi-5-hw-decoder/5757)).
* That DMABuf → `glupload` (EGLImage) path is the real zero-copy story on
  Linux and the Pi: the decoder's buffer becomes a GL texture with no copy.
  **Confirmed on the Pi 5 (section 4b)**, including the `DMA_DRM` /
  `NV12_128C8` modifier issue above: it is fixed in the current Pi OS build.
  On Windows, `glupload` still uploads from system memory.
* What apt actually installed on a stock Pi OS Trixie desktop for the spike:
  only `gir1.2-gstreamer-1.0`, `gir1.2-gst-plugins-base-1.0`,
  `gir1.2-gst-plugins-bad-1.0` and `gstreamer1.0-tools`. Everything the
  recommended gi-free provider needs was preinstalled.
* `pip install PyGObject` isn't needed on any of these: distro `python3-gi`
  works in a `python3 -m venv --system-site-packages` venv (Pi and WSL).

### Per platform: bundle vs require, size, licensing, kivyforge

| | Windows (secondary) | Linux desktop (primary) | Raspberry Pi OS (primary) |
|---|---|---|---|
| Where GStreamer comes from | Official wheels (`gstreamer-libs` + `gstreamer-plugins` [+ `-restricted`]) **bundled by kivyforge**; or the user runs the 543 MB installer | **Distro packages, required from the user** (bundling skips the host's VA-API/V4L2 stack) | **Pi OS packages, required** (Pi-patched build, V4L2 decoders tied to the kernel) |
| Download / disk | 118 MB download, 278 MB installed; **63.5 MB** pruned to a 28-plugin playback allowlist (43.5 MB without FFmpeg), from `prune_estimate.py` | often already present on desktop distros (media players pull it in); otherwise `apt`/`dnf`. Not measured. Ubuntu 26.04 WSL started with only core `libgstreamer` | **nothing extra** on Pi OS Trixie desktop (Lite images not checked) |
| `gi` needed? | no (recommended design) | no | no |
| Codec licensing | GStreamer is LGPL-2.1+: bundling the DLLs is fine (replaceable shared libs, ship notices and the source offer). The base `gstreamer-plugins` wheel **already contains gst-libav (FFmpeg H.264/HEVC/AAC decoders)**, which carries patent exposure in some jurisdictions. The D3D11/D3D12 hardware decoders use the GPU vendor's licensed decoders. `-restricted` (needed for **MPEG-TS HLS**) and `-gpl*` should be opt-in | the distro and the user decide (Ubuntu: `gstreamer1.0-libav`; Fedora: RPM Fusion) | Pi OS ships libav; hardware decode is under the Pi's licence |
| kivyforge would need | see the list below | a declared system dependency + doctor check; nothing bundled | same as Linux; also `aarch64` |

**kivyforge to-do** (source: `kivyforge/platforms/windows/wheels_stage.py`,
`platforms/linux/*`):

* *Windows*:
  1. Install the `gstreamer_*` wheels in full. Their files live under
     `bin/`, `lib/gstreamer-1.0/`, `lib/gio/modules/` (TLS for
     `souphttpsrc`) and `libexec/gstreamer-1.0/gst-plugin-scanner.exe`. The
     PBS prefix keeps `site-packages` non-isolated, so the `.pth` still runs.
  2. Optional **plugin allowlist pruning** (278 → ~64 MB).
  3. Have the launcher set `GST_REGISTRY_1_0` to a **per-app** path, so apps
     don't fight over one shared `%TEMP%` registry. The first launch still
     costs a several-second plugin scan, unless the registry is generated at
     build time (but it records absolute paths and mtimes).
  4. Lock every `gstreamer_*` to one exact version, and fail cleanly on
     partial releases like 1.28.8 and 1.28.9.
  5. Collect LGPL notices.
* *Linux / Pi (AppImage)*: don't bundle GStreamer. Declare it as a host
  requirement: a doctor/launch check for `libgstreamer-1.0.so.0` and the
  needed plugins, with an install hint per distro. Make sure the AppImage
  doesn't carry its own GLib/GObject, which would clash with the
  system GStreamer's GLib.

## 2. The current provider (`video_gstplayer.py` + `_gstplayer.pyx`)

* Pipeline: `pipeline(playbin)` with `video-sink = appsink`
  (`caps=video/x-raw,format=RGB`, `max-buffers=5`, `drop`, `sync`), or
  `fakesink` for audio-only. Legacy `playbin`, not `playbin3`.
* Frames: the appsink `new-sample` signal (`emit-signals=TRUE`) runs a
  **C callback on GStreamer's streaming thread**. It maps the buffer, copies
  it into a Python `bytes` (`data[:datasize]`; plus a second `memcpy` when
  RGB rows are padded) **with the GIL held**, and stores it under a
  `threading.Lock`. `_update()` then `blit_buffer`s it as `rgb`. That's two to
  three CPU copies and a CPU NV12→RGB conversion per frame, plus GIL
  contention on the streaming thread.
* Bus: `gst_bus_enable_sync_message_emission` + a `sync-message`
  handler, so messages also run **on streaming threads with the GIL**
  (the source has comments about deadlocks with PulseAudio as a result).
  EOS goes through `Clock.schedule_once(_do_eos)`.
* **Why failures are never reported:** ERROR / WARNING / INFO go to
  `_on_gstplayer_message` → `Logger.error(...)` and stop there. Nothing
  reaches `VideoBase` and playback state isn't touched, so a failed video
  just stays blank. `load()` can also raise `GstPlayerException`, which is
  the wrong direction for the new contract.
* Other problems: `_get_duration()` can **block for up to 2 s** and changes
  pipeline state (PAUSED preroll, then back to READY) just to answer a
  property read. `seek()` blocks on `get_state`. `stop()` goes NULL→READY,
  dropping the texture.
* Distribution today: **Kivy's manylinux and Pi (armv7l) wheels build with
  `USE_GSTREAMER=0`** (`.github/workflows/manylinux_wheels.yml`,
  `.ci/Dockerfile.armv7l`), so pip-installed Kivy has no gstplayer on its
  primary target platforms. Windows wheels link `kivy_deps.gstreamer`, which
  is **GStreamer 1.18.6** (2021), 437 MB unpacked (116 MB of that is `.pdb`),
  with no `adaptivedemux2` and no D3D12.

## 3. The `on_error` contract (kivy/kivy#9528)

`VideoBase._do_error(error)`: `stop()`, clear `buffering` (dispatching
`on_buffering(False)`), then dispatch `on_error(error)`. Nothing is
raised. `video_mediafoundation.pyx` sets `_error_reported = False` in
`load()` and **never reports from `load()`**, because the `Video` widget
hasn't bound `on_error` yet. `_update()` checks `hasError()` first,
reports **once** (logs, then `_do_error`), and returns early on every later
tick. The spike's `GstSpikeVideo` follows the same pattern:

* `load()` builds the pipeline and sets PAUSED (preroll). Any exception or
  synchronous failure is stored, never raised.
* `_update()` drains the bus. ERRORs are **collected for the whole drain and
  reported once**, preferring a specific error over
  `GST_CORE_ERROR_STATE_CHANGE` (see the next section for why).

## 4. Prototype results (Windows)

(Linux and Raspberry Pi results are in section 4b.)

### playbin3 vs playbin

| | playbin3 | playbin |
|---|---|---|
| Local MP4 1080p | first frame 0.16 s; `urisourcebin` + `d3d12h264dec` | 0.88 s first in process (warm-up) |
| HLS fMP4 (Apple bipbop) | plays (`hlsdemux2`), first frame 2.0 s | **0 frames in 8 s, no error**: both `hlsdemux` and `hlsdemux2` were instantiated |
| HLS TS (mux.dev) + restricted | first frame 0.36 s, 46% CPU | 1.09 s, 59% CPU |
| DASH (Akamai BBB) | plays (`dashdemux2`), 0.58 s | plays (`dashdemux` + `dashdemux2`) |

**Use `playbin3`.** The new adaptive demuxers are designed for it, and with
legacy `playbin` HLS fMP4 failed silently.

### Polling from Kivy's thread: works, no GLib main loop

Every tick: `while (msg := bus.pop()): handle(msg)`, then
`appsink.try_pull_sample(0)` (`emit-signals=False`, `max-buffers=1`,
`drop=True`, `sync=True`). No Python runs on GStreamer threads, except the
GL `NEED_CONTEXT` sync handler in GL mode. ERROR, EOS, STATE_CHANGED,
BUFFERING, STREAM_COLLECTION, DURATION_CHANGED and missing-plugin ELEMENT
messages all arrive in order. A tick costs 0.02–0.05 ms when idle. Nothing
needed a running GLib main context: `souphttpsrc` and `adaptivedemux2` run
their own threads and contexts.

BUFFERING messages come from the adaptive demuxers (percent). The spike
applies the standard protocol: while `state == 'playing'`, a percent below
100 sets the pipeline to PAUSED and `buffering = True`, and reaching 100
resumes it. That maps directly onto `on_buffering`.

### Error text (`results/headless-windows-base.json`)

| Case | Text reported | Notes |
|---|---|---|
| missing file | `Resource not found.` | the debug string has the path. **Race:** playbin3's generic `state change failed and some element failed to post a proper error message` can be the first ERROR popped (seen in `gst_nogi.py`), hence "drain, then prefer non-`STATE_CHANGE`" |
| text file named .mp4 | `This appears to be a text file` | |
| truncated/garbage MP4 | `This file contains no playable streams.` | |
| HTTP 404 (file or HLS playlist) | `Not Found` | debug: `Not Found (404), URL: ...` |
| DNS failure | `Internal data stream error.` | **useless alone**; the spike appends the debug's last line → `(streaming stopped, reason error (-5))`. Still not "could not resolve host": worth an upstream look |
| HLS with TS segments, no `restricted` | `Your GStreamer installation is missing a plug-in.` | plus a `missing-plugin` ELEMENT naming "MPEG-2 Transport Stream demuxer". The spike reports `missing GStreamer plugin: <description>` using `GstPbutils` |
| HLS server killed mid-stream | `Couldn't download fragments` from `hlsdemux2` | arrived **6.2 s** after the kill (retries), playback having continued from the buffer; `results/headless-windows-server-dies.json`, using `slow_http.py` so the VOD can't be prefetched |

### Rendering paths (Kivy app, `results/kivy-windows-glew.json`)

H.264 decoded by `d3d12h264dec` in every case. CPU is process time over
wall time (100% = one core); "update" is `_update()` time per frame. These
are steady-state numbers: the first clip in a process pays a one-time
warm-up (an earlier run that started with 1080p RGBA showed 100% CPU and
26 fps for it).

| Clip | Path | fps | CPU | update p50 / p95 |
|---|---|---|---|---|
| 1080p30 | `rgba` (videoconvert on CPU, `blit_buffer`) | 29.4 | 30% | 3.6 / 4.4 ms |
| 1080p30 | `nv12` (2 plane textures + YUV shader → Fbo) | 29.5 | 21% | 2.0 / 2.8 ms |
| 1080p30 | `gl` (shared GstGL texture) | 29.4 | 15% | **0.26 / 0.43 ms** |
| 2160p30 | `rgba` | 29.2 | **125%** | 15.6 / 18.1 ms |
| 2160p30 | `nv12` | 29.3 | 40% | 7.5 / 8.8 ms |
| 2160p30 | `gl` | 29.2 | **15%** | **0.25 / 0.45 ms** |
| BBB 360p | rgba / nv12 / gl | 26.6* / 29.5 / 29.3 | 78%* / 10% / 12% | 0.40 / 0.42 / 0.24 ms |

\* first clip in that process (warm-up).

Colour and orientation are correct in all three modes:
`results/shot-glew-big_buck_bunny_360_10s-{rgba,nv12,gl}0001.png` (the NV12
shader uses BT.601 below 720p and BT.709 above). Under `angle_sdl3`, `rgba`
and `nv12` behave the same, and `gl` fails cleanly through `on_error`:
`An opengl3 context created but the required ES2 compatibility was not
found` (`results/kivy-windows-angle.json`).

### GPU sharing: what it took

Kivy owns the GL context (SDL3, WGL with the `glew` backend). The official
Windows GstGL build is **WGL-only** (no EGL), so it can share with Kivy's
desktop GL context but not with ANGLE's. The working recipe (`main.py`
`_make_gl_sink` / `_frame_gl`):

1. `GstGL.GLContext.new_wrapped(display, wglGetCurrentContext(), WGL,
   OPENGL|OPENGL3)`, then `activate(True)` and `fill_info()` (which returns a
   bare bool in these bindings).
2. **Create GStreamer's shared context ourselves, with Kivy's context
   released:** `wglMakeCurrent(NULL, NULL)`, then
   `display.create_context(wrapped)`, then `wglMakeCurrent(hdc, hglrc)`,
   then `display.add_context(ctx)`. Without this, every
   `wglCreateContextAttribsARB` attempt (GL 4.5 down to 3.2) fails, because the
   share context is current on another thread. As far as I recall, Qt's
   qmlglsink releases its context the same way on WGL (not re-checked).
3. Answer `NEED_CONTEXT` (`gst.gl.GLDisplay`, `gst.gl.app_context`) from a bus
   sync handler. **The value must be a typed `GObject.Value(GstGL.GLContext,
   ctx)`**: `Structure.set_value('context', ctx)` stores a plain GObject,
   which GstGL rejects *silently* and then creates an **unshared** context.
   Kivy then draws its own unrelated texture ids (in this run, ids 6, 7 and 8
   were Kivy label textures).
4. Video sink: `glupload ! glcolorconvert ! appsink
   caps=video/x-raw(memory:GLMemory),format=RGBA,texture-target=2D`.
5. Per frame: `gst_gl_sync_meta_wait(meta, wrapped)` (glWaitSync in Kivy's
   context), map the memory with `GST_MAP_GL` to read the texture id, wrap it
   in `Texture(texid=..., nofree=True)` (one per pooled id), and hold the
   sample until the next frame.
6. **Two PyGObject gaps forced ctypes:**
   * gst-python's `Gst.Memory.map()` override **drops non-core flags**, so
     `MAP_GL` silently became a READ map: a full `glReadPixels` download
     returning pixels (`0xff000000`) instead of a texture id.
   * `Buffer.get_meta()` returns a base `Gst.Meta` that
     `GLSyncMeta.wait` refuses (`Expected GstGL.GLSyncMeta`).

   `_GstC` in `main.py` calls `gst_memory_map` and `gst_gl_sync_meta_wait`
   through ctypes, using raw pointers dug out of PyGObject's wrappers. In
   Cython against the C API, none of this is an issue.

On Windows, `gl` mode still isn't decoder-to-screen zero-copy:
`d3d12h264dec` downloads to system memory, then `glupload` re-uploads on
GstGL's thread. The win is that conversion happens on the GPU and Kivy's
thread does no copy or upload. On Linux and the Pi, DMABuf from V4L2 or VA
decoders into `glupload` (EGLImage) is true zero-copy (section 4b).

### Without `gi` (`gst_nogi.py`)

The same loop (playbin3, NV12 appsink, `gst_bus_pop`,
`gst_app_sink_try_pull_sample(sink, 0)`, `gst_buffer_map`) runs through
ctypes on `libgstreamer-1.0`, with no `gi` imported. Local 1080p: 23 µs per
tick, correct position and duration. HLS fMP4 plays. A missing file reports
`Resource not found.` once the drain-then-prefer rule is in place. It needed
about 20 function prototypes and three stable structs (`GstMapInfo`,
`GstMiniObject` / `GstMessage` header, `GError`).

## 4b. Linux and Raspberry Pi results

Results: `gstreamer-spike/results/pi5/` and `results/wsl/` (`headless-*.json`,
`kivy-*.json`, `run-*.log`, screenshots).

### Raspberry Pi 5: rendering paths

CPU is process time over wall time (100% = one core; the Pi 5 has four).
"GLX" is Kivy 2.3.2's default under the Xwayland desktop. "EGL" is the same
with `SDL_VIDEO_X11_FORCE_EGL=1`.

| Clip | Decoder (output) | rgba | nv12 | gl, GLX | **gl, EGL** |
|---|---|---|---|---|---|
| HEVC 1080p30 | `v4l2slh265dec` (`NV12_128C8`, or DMABuf `DMA_DRM` under EGL) | 140%, 25 fps | 106%, 26 fps | 100%, 26 fps | **11%, 26 fps**, 0.32 ms/tick |
| HEVC 2160p30 | `v4l2slh265dec` | 133%, **7 fps** | 111%, **7.5 fps** | 101%, **6.7 fps** | **11%, 26 fps**, 0.30 ms/tick |
| H.264 1080p30 | `avdec_h264` (software, I420) | 73% | **43%** | 65% (GLX) / 68% (EGL) | same as GLX |
| H.264 360p (BBB) | `avdec_h264` | 20% | **16%** | 27% | 28% |

* **DMABuf import needs EGL.** Under GLX the decoder output is negotiated
  down to system memory, and `videoconvert` de-tiles `NV12_128C8` on the
  CPU (Mesa also logs `Draw call returned Operation already in progress.
  Expect corruption.`). Under EGL, `glupload`'s sink caps were
  `video/x-raw(memory:DMABuf), format=DMA_DRM`, and the de-tile happens on
  the GPU. Screenshots (`shot-pi5-egl-hevc_*-gl0001.png`) show correct
  colour bars and orientation.
* **The NV12 + shader path doesn't help with the Pi's hardware decoder**
  (its output is tiled). It is the cheapest path only for *software*-decoded
  video (plain I420/NV12), where `glupload` adds an upload on GstGL's thread
  that NV12-to-textures doesn't.
* **EGL doesn't need Kivy's context released** while GstGL creates its
  shared context (`kivy-pi5-norelease.json`: works, 12%). The release dance
  is a WGL requirement only.
* PyGObject can't wrap Kivy's X11 `Display*`
  (`GLDisplayX11.new_with_display` isn't introspectable), so GLX mode used a
  second X connection by name. Another reason for the C-level provider.
* Every path tops out around 26 fps on a 30 fps clip, including 360p, so
  the limit is presentation pacing (Kivy 2.3 / SDL2 under Xwayland), not
  decode. Open question 7.
* gst-python 1.26 (Pi): `Context.writable_structure()` is not a context
  manager (1.28 made it one). In GL mode sharing still worked, because the
  pre-created shared context was registered with `GstGLDisplay.add_context()`
  and the elements found it there.

### Raspberry Pi 5 with Kivy 3.0 (SDL3): `gl` vs `sdl3` backends

Kivy 3.0.0.dev0 built on the Pi from `video-on-error-event` with
`build_kivy3_linux.sh` (Kivy's own `tools/build_linux_dependencies.sh`:
SDL3 3.4.0 with Wayland, X11 and KMSDRM; about 10 minutes on the Pi 5), then
`run_kivy3_matrix.sh`. Results: `results/pi5-kivy3/`. Each run records the
backend, SDL driver and context actually used (`meta` in the JSON).

| `KIVY_GL_BACKEND` | SDL video | Context | H.264 1080p nv12 / gl | HEVC 1080p gl | HEVC 2160p gl | `glupload` input |
|---|---|---|---|---|---|---|
| `gl` | wayland | EGL | 48% / 74% | 15%, 29 fps | **16%, 29 fps** | DMABuf |
| `gl` | x11, forced EGL | EGL | 48% / 76% | 13%, 29 fps | **14%, 29 fps** | DMABuf |
| `gl` | x11 | GLX | 48% / 73% | 109%, 29 fps | 103%, **6.7 fps** | system memory |
| `sdl3` | wayland | EGL | 48% / 75% | 13%, 29 fps | **16%, 29 fps** | DMABuf |
| `sdl3` | x11, forced EGL | EGL | 48% / 75% | 13%, 29 fps | **16%, 29 fps** | DMABuf |
| `sdl3` | x11 | GLX | 48% / 71% | 106%, 29 fps | 104%, **6.5 fps** | system memory |
| **default (no settings)** | **x11** | **GLX** | | | 102%, **6.4 fps** | system memory |

rgba and nv12 are the same in every combination (4K HEVC: 7 fps at 113–135%),
because the hardware decoder's tiled output is de-tiled on the CPU on those
paths.

* **The GL backend doesn't matter.** `gl` (links `libGL` directly) and
  `sdl3` (`SDL_GL_GetProcAddress`) gave the same numbers. The **context type**
  decides: EGL imports DMABuf, GLX can't.
* **Kivy 3.0's default on the Pi desktop is the bad case:** with nothing set,
  it got the `sdl3` backend, SDL's **x11** driver (Xwayland) and **GLX**, even
  though a Wayland session (labwc) was running. Kivy sets no video-driver or
  EGL hint (`kivy/core/window/_window_sdl3.pyx`), so this is SDL3's own
  driver preference; why SDL3 prefers X11 under labwc wasn't investigated.
  Fix candidates (Kivy window setup, before the window and context exist, so
  not something the video provider can do): set `SDL_HINT_VIDEO_FORCE_EGL`
  on Linux, and/or prefer `wayland` when `WAYLAND_DISPLAY` is set.
* **SDL3 removes the ~26 fps ceiling** seen with Kivy 2.3/SDL2: every path
  now holds 29–29.5 fps on the 30 fps clips.
* GL mode under native Wayland matched X11+EGL. Screenshots
  (`shot-pi5-k3-*-gl0001.png`) are correct.
* For software-decoded H.264, NV12 + shader stays cheaper than GL mode
  (48% vs 71–76%), the same as with Kivy 2.3.
* One flake: in the first matrix pass the `gl` + Wayland run produced no
  output and no JSON; a re-run of the same command passed. Not reproduced.
* Without GStreamer dev headers on the Pi, this Kivy build has no
  `gstplayer` at all (its video provider fell back to `null`), which is the
  situation the gi-free, runtime-loaded design avoids.

### Raspberry Pi 5 and WSL: pipeline, errors, streaming

Same behaviour as Windows (`headless-pi5.json`, `headless-wsl.json`):

* `playbin3` autoplugs `v4l2slh265dec` for HEVC on the Pi and the NVIDIA
  CUDA decoders on WSL, with no configuration.
* Error text is **identical** on all three platforms: `Resource not found.`,
  `This appears to be a text file`, `This file contains no playable
  streams.`, `Not Found`, and the unhelpful `Internal data stream error.`
  for DNS failure.
* HLS (TS and fMP4) and DASH play through `hlsdemux2` / `dashdemux2` with
  BUFFERING messages. TS demux is in the default Debian/Ubuntu/Pi packages
  (no separate "restricted" step as on Windows).
* Server dies mid-stream: `Couldn't download fragments` from `hlsdemux2`.
  On Linux it arrived **0.05 s** after the kill, because a fragment download
  was in flight. On Windows it took 6.2 s, after the buffer drained and the
  retries ran out. The provider has to handle both.
* `gst_nogi.py` (ctypes, no `gi`): 22–36 µs per tick on both, H.264 and HEVC.
* Software decode costs on the Pi 5: H.264 1080p30 49%, H.264 2160p30 195%,
  VP9 1080p30 157% (`vp9dec`).

### WSL2

WSL is useful for checking Linux packaging and behaviour, not rendering
performance:
* Kivy's GL context is llvmpipe, so nv12/gl modes show 170–500% CPU. That
  cost is software rasterisation, not the provider.
* No `libmtdev.so.1`, so Kivy logs an `OSError` from its input provider at
  startup (harmless).
* Everything else matched: decoders, errors, HLS/DASH, the gi-free loop.

## 5. Recommended design

**Bindings:** Kivy's provider does **not** depend on PyGObject. Write it in
Cython against a small vendored declaration set for the stable GStreamer 1.x
ABI (core, app, video, gl, pbutils; roughly 60 functions), resolved with
`dlopen`/`LoadLibrary` **at import time**. Reasons:

* The same compiled module ships in **every** Kivy wheel (manylinux, Pi,
  Windows, macOS) with no GStreamer at build time, ending
  `USE_GSTREAMER=0` and the `kivy_deps.gstreamer` 1.18 dependency.
* It works under kivyforge's python-build-standalone on Linux and the Pi,
  where distro `gi` can't be imported.
* The GL path needs C-level access anyway (map flags, metas, raw pointers).
* If GStreamer isn't found, the import fails and `core_select_lib` falls
  through to the next provider, as it does today.
* With no build-time GStreamer, the provider can't use the C-level
  `GstGLDisplayX11` / `GstGLDisplayEGL` constructors through headers either;
  they are plain exported functions, so the same `dlsym` table covers them.
* Runtime lookup order: Windows: `gstreamer_libs` wheel dirs
  (`gstreamer_env()[1]` → `os.add_dll_directory`), then
  `GSTREAMER_1_0_ROOT_MSVC_X86_64` (installer), then `PATH`. Linux:
  `libgstreamer-1.0.so.0` from the system. Minimum version:
  **1.22** (Pi OS Bookworm), since `playbin3` and `adaptivedemux2` need it.
* Offer `kivy[gstreamer]` on Windows as an extra that pulls
  `gstreamer-libs` + `gstreamer-plugins` (exact-pinned), not `gstreamer-python`.

**Pipeline:** `playbin3`, `uri`, `video-sink` = appsink (GL bin or NV12
caps), audio left to `autoaudiosink`, `volume` on playbin3. `load()`
sets PAUSED (preroll gives the first frame, which fires `on_load` from
`_update`). `play()` sets PLAYING. `pause()` sets PAUSED. `stop()` sets
PAUSED plus a flushing seek to 0, which keeps the texture. Duration is
cached from `query_duration` and reset on DURATION_CHANGED; it never
changes state to answer a property read. `seek` uses
`FLUSH | ACCURATE` (or `KEY_UNIT` when `precise=False`).

**Threading:** everything on Kivy's thread from the provider's `Clock`
interval. Drain the bus, then `try_pull_sample(0)`. The only cross-thread
callback is the GL `NEED_CONTEXT` sync handler, which in Cython is C and
never takes the GIL.

**Rendering, in order of preference:**

1. `gl`: shared GstGL context, `glupload ! glcolorconvert ! appsink(GLMemory)`,
   texture id wrapped with `nofree=True`. Use it when Kivy's context is
   desktop GL/WGL (Windows `glew`) or **EGL on Linux**. On Linux this is
   the DMABuf zero-copy path, the only usable path for 4K HEVC on the Pi 5.
   GLX works but can't import DMABuf, so on Linux Kivy's context must be
   EGL. Kivy 3.0 doesn't get that by default (section 4b: X11 + GLX on the
   Pi desktop), so **Kivy's SDL3 window setup should set
   `SDL_HINT_VIDEO_FORCE_EGL` on Linux** (and/or prefer `wayland`). That's a
   change outside the provider. The provider itself should log a warning when
   it finds a GLX context and DMABuf decoders, because the user is then on
   the slow path. The cgl backend (`gl` or `sdl3`) makes no difference.
2. `nv12`: two plane textures + YUV shader → Fbo (the same idea as
   `video_android.py`'s OES shader). Use it when GL sharing isn't possible
   (ANGLE, a failed context share). It is also the cheaper choice for
   *software*-decoded video on the Pi (43% vs 65% at 1080p H.264). Choose it
   per stream by the decoder's output: plain system-memory NV12/I420 →
   `nv12`; DMABuf or GPU memory → `gl`. It costs about a third of RGBA's CPU
   at 4K, and needs `ask_update()` on the Fbo every frame, or `Fbo.draw()`
   is a no-op.
3. `rgba`: last resort only. Note that Kivy's `blit_buffer(rowlength=)` is in
   **bytes**, not pixels.

Choose the path automatically at `load()`, with a `options={'render': ...}`
override, and log the path once, as `video_mediafoundation` logs its CPU
fallback.

**Errors:** after the full drain, report once: skip
`GST_CORE_ERROR_STATE_CHANGE` when a more specific error exists. Turn
missing-plugin ELEMENT messages into `missing GStreamer plugin: <description>`
(`gst_missing_plugin_message_get_description`). For generic
`GST_STREAM_ERROR_FAILED`, append the debug string's last line. Never raise
from `load()`. A synchronous `set_state` FAILURE is not itself an error
to report: the real ERROR is already on the bus.

## Open questions

1. **Why SDL3 picks X11 over Wayland on the Pi's labwc desktop**, and whether
   forcing EGL by default on Linux has side effects (NVIDIA proprietary
   drivers on X11, older Mesa). Kivy 3.0 on SDL3 itself is now measured on
   the Pi (section 4b).
2. Pi 4 (stateful `v4l2h264dec` for H.264, plus the reported Trixie
   `v4l2slh265dec` regression) and Intel/AMD VA-API on real x86 Linux weren't
   available. WSL's llvmpipe can't stand in for them.
3. The Windows audio sink (`wasapi2sink`) and A/V sync weren't checked by ear.
4. The pruned 64 MB set is a static import closure. It hasn't been run, and
   the gio TLS module (`lib/gio/modules`) and `gst-plugin-scanner.exe` are
   loaded dynamically, so they aren't in it.
5. The DNS-failure text (`Internal data stream error`): is there a better
   source (an earlier WARNING, `souphttpsrc` debug), or is this worth an
   upstream issue?
6. Should `kivy[gstreamer]` on Windows include `-restricted` (MPEG-TS HLS is
   common) or leave it to the app?
7. ~~The ~26 fps ceiling on the Pi 5~~: answered. It was Kivy 2.3/SDL2;
   Kivy 3.0/SDL3 holds 29–29.5 fps on both X11 and Wayland. KMSDRM is still
   unmeasured.
8. Pi OS Lite (no desktop): which GStreamer packages it lacks, and whether
   SDL3's KMSDRM EGL context shares with GstGL the same way.

## Files

All under `notes/video-providers/gstreamer-spike/`:

* `main.py`: the Kivy app with `GstSpikeVideo(VideoBase)` (gi-based
  prototype provider; rgba/nv12/gl paths; `on_error` polyfill) and a UI.
  `--auto results/x.json` benchmarks and takes screenshots. Env:
  `GSTSPIKE_MODES`, `GSTSPIKE_CLIPS`, `GSTSPIKE_GLDEBUG=1` (reads GL
  textures back in Kivy's context).
* `gst_headless.py`: windowless probe. Cases: local, error, HTTP, HLS,
  DASH, server-dies. `--bin playbin|playbin3|both`.
* `gst_nogi.py`: the same loop through ctypes, no `gi`.
* `slow_http.py`: rate-limited static server for the server-dies case.
* `make_clips.py`: generates the test clips with GStreamer itself (the clips
  aren't committed; `big_buck_bunny_360_10s.mp4` is copied from `mf-check`).
* `prune_estimate.py`: PE import walk for the pruned Windows bundle size.
* `run_linux.sh TAG`: one-shot Linux/Pi run (element and decoder probe, venv
  with `--system-site-packages`, headless, gi-free, and Kivy in GLX, EGL and
  no-release passes).
* `gl_check.sh`: a quick GLX vs EGL vs Wayland gl-mode check for one clip.
* `build_kivy3_linux.sh SRC VENV`: builds Kivy 3.0 from a source tree against
  Kivy's own SDL3 dependency build, on Debian/Ubuntu/Pi OS.
* `run_kivy3_matrix.sh TAG SRC VENV`: `KIVY_GL_BACKEND` `gl`/`sdl3` × native
  Wayland / X11 GLX / X11 forced-EGL.
* `results/`: Windows JSON and screenshots; `results/pi5/`, `results/wsl/`
  for the Kivy 2.3.2 Linux runs; `results/pi5-kivy3/` for Kivy 3.0 on the Pi.

## Reproducing

Linux / Pi (distro packages from section 1, then):

```
./run_linux.sh pi5        # XDG_RUNTIME_DIR, WAYLAND_DISPLAY, DISPLAY of the desktop session when over SSH
```

Windows:

```
py -3.12 -m venv gstvenv
gstvenv\Scripts\python -m pip install --only-binary=:all: -c gst-constraints.txt gstreamer-meta==1.28.7
gstvenv\Scripts\python -m pip install <kivy 3.0 wheel>
gstvenv\Scripts\python make_clips.py
gstvenv\Scripts\python gst_headless.py --all --json results\headless.json
gstvenv\Scripts\python main.py --auto results\kivy.json
```

`gstreamer-spike/gst-constraints.txt` pins every `gstreamer_*` package (bundle, cli,
ext-runtime, gtk, libs, plugins, plugins-gpl, plugins-gpl-restricted,
plugins-libs, plugins-restricted, python) to `==1.28.7`.
