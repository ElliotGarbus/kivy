# GStreamer video provider for Kivy 3.0 -- design

Design for the modernized GStreamer provider: a rewrite of
`kivy/core/video/video_gstplayer.py` and `kivy/lib/gstplayer/`, running on
Windows, Linux and Raspberry Pi. It follows the spike in
`GSTREAMER-SPIKE.md` (measurements on Windows, a Raspberry Pi 5 and WSL2) and
a review of the GStreamer 1.28 source and documentation. Notes only, never
part of an upstream PR ([[notes-stay-out-of-upstream-pr]]). Date: 2026-11-19.

Every claim below is marked by how well it is supported:

* **Proven**: measured in the spike.
* **Source**: read in GStreamer's source or documentation (links under References).
* **Unproven**: designed but not yet run. Each of these has a spike in
  section 13.

## 1. Summary

1. One provider, `gstplayer`, for three platforms: primary on Linux and
   Raspberry Pi, optional secondary on Windows (after Media Foundation).
2. It is a small Cython module that loads `libgstreamer` **at runtime**. Kivy
   needs no GStreamer headers or libraries to build, so the provider ships in
   every Linux, Pi and Windows wheel and switches on when a GStreamer runtime
   is present. No PyGObject.
3. The pipeline is `playbin3` with an `appsink` video sink. Kivy's thread polls
   the bus and the sink once per frame. There is no GLib main loop and no
   Python code on GStreamer's threads.
4. Rendering uses the GPU where it can: GStreamer's GL textures are shared
   with Kivy's context and drawn directly. On Linux with an EGL context this is
   true zero-copy from hardware decoders (Pi 5: 4K HEVC at 29 fps on 14–16% of
   one core). The fallback is NV12 planes plus a shader.
5. Failures go through `VideoBase._do_error()` once, from `_update()`, with
   GStreamer's own message text. Nothing is raised.
6. GStreamer itself comes from the distro on Linux and the Pi, and from the
   GStreamer project's official PyPI wheels (or installer) on Windows.
7. Eleven technical questions are still open. Section 13 lists a spike for
   each; three of them should run before the main implementation starts. The
   product decisions are made (section 14).

## 2. Context

Kivy 3.0 video provider plan (primary / secondary):

| Platform | Primary | Secondary |
|---|---|---|
| macOS | AVFoundation (done) | none |
| Windows | Media Foundation (WIP, PR not yet merged) | **GStreamer (modernized, optional)** |
| Linux | **GStreamer (modernized)** | none |
| Raspberry Pi | **GStreamer (modernized)** | none |
| iOS | AVFoundation (done) | none |
| Android | Media3 ExoPlayer (WIP, PR not yet merged) | none |

Additional providers may be considered later, on demonstrated user demand
for broader codec support, streaming or compatibility.

How this design serves each goal:

| Goal | In this design |
|---|---|
| Prefer native OS media frameworks | On Linux and the Pi, GStreamer *is* the platform media framework: distros ship it and hardware decoders (V4L2, VA-API) are exposed through it. On Windows it stays secondary to Media Foundation |
| GPU-efficient, zero-copy rendering | Shared GL textures; DMABuf import on EGL (section 7, D6–D8) |
| Minimal binary dependencies, simple distribution | No build-time dependency; nothing bundled on Linux or the Pi; Windows uses upstream's own wheels instead of the frozen `kivy_deps.gstreamer` 1.18.6 |
| HLS/DASH where practical | `playbin3` + `adaptivedemux2`, proven on all three platforms |
| Few maintained providers | One code base for three platforms; it also replaces the build-time-linked `_gstplayer` used by the audio provider |

## 3. Evidence

### The spike (`GSTREAMER-SPIKE.md`)

| Finding | Where measured |
|---|---|
| `playbin3` + `appsink`, polled per tick, works with no GLib loop; idle tick 0.02–0.05 ms | Windows, Pi 5, WSL2 |
| The same loop with no `gi`, through the C ABI: 22–36 µs per tick | Windows, Pi 5, WSL2 |
| Error text is identical across platforms (`Resource not found.`, `Not Found`, `Couldn't download fragments`, ...) | Windows, Pi 5, WSL2 |
| HLS (TS and fMP4) and DASH play; legacy `playbin` failed silently on HLS fMP4 | Windows, Pi 5, WSL2 |
| Shared GL texture: 4K frame costs 0.25 ms per tick, ~15% CPU (NV12: 7.5 ms, 40%; RGBA: 16 ms, 125%) | Windows, WGL |
| DMABuf from `v4l2slh265dec` into `glupload`: 4K HEVC 29 fps at 14–16% CPU; 6.5 fps without it | Pi 5, EGL |
| Kivy 3.0's `gl` and `sdl3` GL backends behave the same; only EGL vs GLX matters | Pi 5, Kivy 3.0 |
| For software-decoded video, NV12 + shader is cheaper than the GL path (48% vs 71–76%, 1080p H.264) | Pi 5 |
| EGL needs no context release while GStreamer creates its shared context; WGL does | Pi 5, Windows |

### What the GStreamer source review changed

The spike worked through PyGObject and found several things by trial. Reading
the 1.28 source and docs confirmed most of them and corrected the design in
five places:

1. **Frames while paused need a different call.** `gst_app_sink_try_pull_sample()`
   "will only return samples when the appsink is in the PLAYING state". The
   first frame after `load()` and the frame after a paused seek must come from
   `gst_app_sink_try_pull_preroll()`. The spike never noticed because its
   benchmark always called `play()` immediately.
2. **No bus sync handler is needed for GL.** GStreamer's context design says a
   context set on a bin with `gst_element_set_context()` is propagated to its
   children, "even to elements added after". Setting the display and
   application GL contexts on the pipeline before it starts removes the only
   cross-thread callback the spike had.
3. **Creating the shared GL context has a locking rule.**
   `gst_gl_display_create_context()` and `gst_gl_display_add_context()`
   require the display's object lock, which the spike didn't take.
   `gst_gl_display_ensure_context()` (since 1.24) does the create, lock and
   add correctly in one call.
4. **Version floors are now sourced.** `adaptivedemux2` arrived in 1.22, and
   `playbin3` was "no longer considered experimental" then. `playbin3` became
   "stable and default" in 1.24, which also introduced `DMA_DRM` caps. V4L2
   elements negotiate `DMA_DRM` from 1.26, where `adaptivedemux2` also gained
   `max-retries` and errors started to include the URI.
5. **HTTP errors carry structured details.** `souphttpsrc` attaches
   `http-status-code` to its error message, readable with
   `gst_message_parse_error_details()`. That is a better source than parsing
   debug strings.

Confirmed as the spike found them:

* `glupload` only imports DMABuf when the GL context's platform is EGL; the
  check is explicit in `gstglupload.c`.
* The official Windows GstGL build is WGL-only. GstGL *can* be built with EGL
  on Windows, but the official binaries aren't.
* Applications must hold the pipeline in PAUSED while BUFFERING is below 100,
  except for live pipelines (`set_state` returns `NO_PREROLL`).
* When `GST_PLUGIN_SYSTEM_PATH` isn't set, plugins are found relative to the
  loaded `libgstreamer`, so a relocated install works without environment
  variables.

## 4. Scope

In scope:

* `VideoGstplayer(VideoBase)`: load, play, pause, stop, seek, position,
  duration, volume, `eos` modes, `on_load`, `on_frame`, `on_eos`,
  `on_buffering`, `on_error`, `options`, `generate_thumbnail`.
* Local files, `http(s)` progressive, HLS and DASH.
* Linux x86_64, aarch64 and armv7l (desktop distros and Raspberry Pi OS; the
  three Linux wheel targets) and Windows x86_64. Only x86_64 and aarch64 have
  been measured.
* Porting `audio_gstplayer` to the new core (it imports the same module).
* Build, CI, packaging extras, tests and documentation.

Out of scope for this work:

* macOS, iOS and Android. The provider is neither built nor registered there.
* Zero-copy with ANGLE on Windows. It would need D3D11 texture sharing or a
  custom GStreamer build; Windows is the secondary platform, so ANGLE gets the
  NV12 path.
* HDR output and tone mapping. 10-bit content is converted to 8-bit.
* Track selection (multiple audio or video streams), subtitles, DRM, capture,
  recording.
* Removing `ffpyplayer` and `video_ffmpeg`. That stays the last step of the
  overhaul.

## 5. Dependencies and sequencing

| Item | State | Needed for |
|---|---|---|
| `VideoBase.on_error` (kivy/kivy#9528) | merged | error reporting |
| `Texture(nofree=True)` | on upstream master | wrapping GStreamer-owned textures |
| SDL3 window requests EGL on Linux (branch `sdl3-linux-force-egl`, commit `eab2ae1ed`) | committed, not pushed; reported 28.6 fps / 17% CPU for 4K HEVC on the Pi 5 with no settings, not re-measured here | zero-copy by default on X11 and Xwayland desktops. Without it the provider still works but on the slow path |
| SDL3 `SDL_Init` result check (branch `sdl3-init-check`, commit `94f96c407`) | committed, not pushed | independent; only makes window failures visible |
| Media Foundation provider | WIP | none. The Windows registration order just puts `gstplayer` after it |

The provider must not *depend* on the EGL change: it detects the context type
at run time and logs once when it finds GLX next to a DMABuf-capable decoder.

## 6. Architecture

```
kivy/uix/video.py  Video widget
        |
kivy/core/video/video_gstplayer.py      VideoGstplayer(VideoBase)   Python
        |   load / play / pause / stop / seek ...      _update(): player.poll()
kivy/lib/gstplayer/_gstplayer.pyx       GstPlayer                   Cython
        |   pipeline, bus drain, sample pull, texture update, GL sharing
kivy/lib/gstplayer/_gst_loader.c/.h     runtime loader              C
        |   dlopen / LoadLibraryEx + symbol table (no GStreamer headers)
libgstreamer-1.0, libgstapp-1.0, libgstvideo-1.0, libgstgl-1.0, libgstpbutils-1.0
        |   from the distro (Linux, Pi) or the official wheels / installer (Windows)
playbin3 -> [decoders] -> video sink bin -> appsink        GStreamer threads
```

Threads:

* **Kivy's thread** does everything the provider does: builds the pipeline,
  changes state, seeks, drains the bus, pulls frames, updates textures and
  dispatches events.
* **GStreamer's threads** decode, demux, download and (in GL mode) upload and
  convert on GstGL's own GL thread. They never call into Python.
* **One short-lived worker thread** runs `gst_init_check()` (D2).

The provider name stays `gstplayer` and the class stays `VideoGstplayer`, so
`KIVY_VIDEO=gstplayer` keeps working.

## 7. Design decisions

### D1. Bindings: runtime-loaded C ABI, no PyGObject

`_gst_loader.c` holds a table of function pointers, filled with `dlsym` /
`GetProcAddress` from the libraries it opens. `_gstplayer.pyx` calls through
that table. Kivy vendors its own small header with the prototypes it uses
(an estimated 60 to 70 functions) and mirrors four small public structs: `GError`,
`GstMapInfo`, the head of `GstMessage` (for its type) and the head of
`GstVideoMeta` (plane offsets and strides).

Why not PyGObject:

* Kivy's manylinux and Pi wheels are built with `USE_GSTREAMER=0` today,
  because linking needs GStreamer at build time. Runtime loading removes
  that.
* kivyforge's Linux AppImage runs python-build-standalone, which can't import
  the distro's `gi`.
* `pip install PyGObject` compiles from source and needs C headers.
* The GL path needs C-level calls PyGObject can't make correctly (map flags
  are dropped, metas come back untyped). **Proven.**

Safety checks at import, all cheap and all before `gst_init`:

* every required symbol resolved, else the import fails and Kivy selects the
  next provider, exactly as when the module is missing today;
* `gst_version()` at or above the floor (D15).

After `gst_init` (on the worker, D2), a struct self-test creates an EOS
message with `gst_message_new_eos()` and checks the mirrored struct reads
`GST_MESSAGE_EOS` back; a failure is reported as an initialization error.
(Moved from import in spike 1: creating a message before initialization
isn't something to rely on.)

`libgstgl` and `libgstpbutils` are optional. Without the first there is no GL
path; without the second, missing-plugin errors use the generic text.

Library lookup order:

| Platform | Order |
|---|---|
| Linux, Pi | `KIVY_GSTREAMER_ROOT` if set; else `libgstreamer-1.0.so.0` through the normal loader search |
| Windows | `KIVY_GSTREAMER_ROOT` if set; else the `gstreamer_libs` wheel (its `gstreamer_env()` gives the DLL directories; the loader registers them with `os.add_dll_directory`, which the wheel's `.pth` doesn't do); else the official installer's root environment variable; else `PATH`. Both `gstreamer-1.0-0.dll` (MSVC) and `libgstreamer-1.0-0.dll` (MinGW) names |

The loader logs the path and version it picked, once. **Proven** in spike 1
(`GSTREAMER-CORE-SPIKES.md`) on the Windows wheels, Pi OS Trixie and Ubuntu
26.04.

### D2. Initialization off the UI thread

The first `gst_init` on a machine scans every plugin: 7.3 s with the Windows
wheels (**proven**). To keep that off Kivy's thread:

* Import does only the cheap checks in D1.
* The first `load()` starts a worker thread that calls `gst_init_check()`
  without the GIL. `load()` records the URI and returns.
* `_update()` builds the pipeline once the worker has finished. If
  initialization failed, it reports through `on_error`.

While this runs, `buffering` is `True` if the app has asked to play, which is
what the `buffering` contract already says about "the initial pre-playback
wait". **Proven** (spike 1): first-run init took 3.4–13 s on Windows, 5 s on
the Pi and 1.6 s on WSL2, and a 60 Hz loop never waited more than 20 ms.

### D3. Pipeline

`playbin3`, one per loaded video:

| Property | Value |
|---|---|
| `uri` | file paths converted with `gst_filename_to_uri()` |
| `video-sink` | the sink bin from D6–D8 |
| `audio-sink` | unset: `playbin3` uses `autoaudiosink` |
| `volume` | from `VideoBase.volume` |
| `flags` | `video`, `audio`, `soft-volume`, `buffering`, `deinterlace`; plus `force-sw-decoders` when the option asks for it. Not `soft-colorbalance`. `text` off on Linux (an overlay would be blended on the CPU), **on on Windows**: with it off, a stream with a subtitle track stalled with the 1.28.7 wheels (spike 3) |

`playsink` inserts a software converter chain when `soft-colorbalance` is set
and the sink has no colour-balance interface (**source**). The spike ran with
the default flags and DMABuf still passed through; with the reduced flags it
still does (**proven**, spike 3). `playbin3` selects a subtitle stream even
with `text` off; a `select-streams` event for audio and video only had no
visible effect on Windows and is left for later.

`appsink` settings: `emit-signals=false`, `max-buffers=1`, `drop=true`,
`sync=true`, `enable-last-sample=false`, `qos=true`. `qos` lets decoders skip
work when frames are late; the old provider set it, the spike didn't.

### D4. The tick

`VideoBase.__init__` schedules `_update` at `1/fps` with `fps=30` by default,
which would drop frames on 60 fps video. The provider unschedules that and
runs one Clock event every frame while a video is loaded. An idle poll costs
about 25 µs (**proven**).

Each tick, in order:

1. If initialization is pending, check it (D2).
2. Drain the bus with `gst_bus_pop()` until empty, collecting what happened.
3. Act on the collected messages (D9, D10, D11).
4. Pull a frame: `try_pull_sample(0)` when playing; `try_pull_preroll(0)` once
   after each preroll (after `load()` and after a seek while paused)
   (**source**).
5. Update the texture (D6–D8) and dispatch `on_load` (first frame) and
   `on_frame`.

Messages handled:

| Message | Action |
|---|---|
| `ERROR` | D9 |
| `EOS` | `_do_eos()` if the state isn't stopped |
| `BUFFERING` | D10 |
| `ASYNC_DONE` | preroll finished: allow a preroll pull; issue a queued seek (D11) |
| `DURATION_CHANGED` | drop the cached duration |
| `CLOCK_LOST` | set PAUSED then PLAYING, as GStreamer's docs require |
| `LATENCY` | `gst_bin_recalculate_latency()` |
| `ELEMENT` (missing-plugin) | remember the description for the error text |
| `WARNING` | log |
| anything else | ignore |

### D5. Controls

| Call | Behaviour |
|---|---|
| `load()` | Never raises. Stores the URI; builds the pipeline (now or after D2) and sets PAUSED so the first frame arrives without playing. `on_load` fires from `_update()` with that frame |
| `play()` | PLAYING, unless holding for buffering (D10) |
| `pause()` | PAUSED |
| `stop()` | PAUSED plus a seek to 0. The texture is kept. The `Video` widget also sets `position = 0` after `stop()`; D11 coalesces the duplicate |
| `unload()` | NULL, release the held frame and texture wrappers, cancel the Clock event |
| `seek(percent, precise)` | D11 |
| `position` | `gst_element_query_position()`; the pending target while a seek is in flight |
| `duration` | cached `gst_element_query_duration()`; 0 when unknown, the base-class default. It never changes pipeline state (the old provider blocked for up to 2 s) |
| `volume` | `playbin3`'s `volume` |
| `eos='loop'` | `VideoBase._do_eos` seeks to 0 and plays. Gapless looping is a later improvement |

`playbin3`'s documentation says it should go back to READY or NULL after EOS
or an error before being reused. A flushing seek from EOS is the usual way to
loop and worked in the spike's UI, but wasn't measured: spike 3.

### D6. Rendering: choosing a path

Three paths, chosen at `load()`:

| Path | Used when | Cost (4K, measured) |
|---|---|---|
| `gl`: shared GstGL texture | Kivy's context is EGL (Linux) or WGL (Windows desktop GL), `libgstgl` loaded, shared context created | Pi 5 EGL: 14–16% CPU. Windows: ~15%, 0.25 ms per tick |
| `nv12`: two plane textures + shader | GL sharing not possible (ANGLE, sharing failed), or chosen by option | Windows: 40%, 7.5 ms per tick |
| `rgba`: CPU conversion + upload | only by option, for debugging | Windows: 125%, 16 ms per tick |

`options={'render': 'auto' | 'gl' | 'nv12' | 'rgba'}` overrides it; the
default is `auto`. The chosen path is logged once, the way
`video_mediafoundation` logs its CPU fallback.

A GLX context can share textures but can't import DMABuf (**source**,
**proven**: 6.5 fps for 4K HEVC on the Pi 5). In `auto`, GLX still uses `gl`,
because on desktop GPUs it remains the cheapest path, and the provider logs a
warning when the decoder could have produced DMABuf.

One known cost: for software-decoded video on the Pi 5, `nv12` beats `gl`
(48% vs 71–76% at 1080p). Choosing per stream, by what the decoder outputs,
would fix that. It needs a sink that decides at caps time and is **unproven**:
spike 4. Until then `auto` is a per-context choice.

### D7. The GL path

Setup, once per `load()`, on Kivy's thread:

1. Read Kivy's current context and display from the GL platform itself
   (`eglGetCurrentContext`/`eglGetCurrentDisplay`, `glXGetCurrentContext`,
   `wglGetCurrentContext`), so it works with any Kivy window provider.
2. Create the matching `GstGLDisplay`: `gst_gl_display_egl_new_with_egl_display()`
   for every EGL case (**proven** on Wayland and X11 with EGL; KMSDRM is
   spike 5),
   `gst_gl_display_x11_new_with_display()` for GLX,
   `gst_gl_display_new()` for WGL.
3. Wrap Kivy's context with `gst_gl_context_new_wrapped()`, passing the
   platform and the API (GLES2, or OpenGL | OpenGL3), then
   `gst_gl_context_activate()` and `gst_gl_context_fill_info()`.
4. Create GStreamer's own context shared with it:
   `gst_gl_display_ensure_context()` on 1.24+. On WGL only, Kivy's context is
   released (`wglMakeCurrent(NULL, NULL)`) around this call and restored
   after; without that every attempt failed (**proven**).
5. Put both the display context and the `gst.gl.app_context` context on the
   pipeline with `gst_element_set_context()` (**source**; the spike used a bus
   sync handler instead).

Video sink bin: `glupload ! glcolorconvert ! appsink` with caps
`video/x-raw(memory:GLMemory), format=RGBA, texture-target=2D`.

Per frame, on Kivy's thread:

1. `gst_gl_sync_meta_wait()` on the buffer's sync meta, in Kivy's context.
2. Map the memory with `GST_MAP_READ | GST_MAP_GL` to read the texture id.
3. Wrap the id in `Texture(texid=..., nofree=True)`, flipped vertically. The
   wrappers are cached by id, because GStreamer's pool reuses a few textures.
4. Keep a reference to the sample until the next frame replaces it, so the
   pool can't recycle a texture Kivy is still drawing.

If any setup step fails, the provider falls back to `nv12` for that video and
logs why. A step failing is not an error for the app.

Steps 4 and 5 differ from what the spike ran: spike 2.

### D8. The NV12 path

Video sink: `appsink` with caps `video/x-raw, format=NV12`. Per frame:

1. Map the buffer; read plane offsets and strides from `GstVideoMeta`, or
   compute GStreamer's default layout when there is no meta.
2. Upload the Y plane as `luminance` and the UV plane as `luminance_alpha`
   with `Texture.blit_buffer(..., rowlength=stride)`. Kivy's `rowlength` is in
   bytes. From Cython the mapped memory is passed as a buffer, so there is no
   intermediate `bytes` copy as there was in the spike.
3. Draw both through a YUV-to-RGB shader into an `Fbo`; the Fbo's texture is
   the video texture. `Fbo.ask_update()` is needed every frame.

The shader's matrix and range come from the caps' `colorimetry` field
(BT.601, BT.709 or BT.2020, limited or full range; chroma centred on 128/255,
not 0.5, which put R and B one level high in spike 3), not from the frame height as in
the spike. **Unproven**: spike 9.

### D9. Errors

Rules, all applied on Kivy's thread inside `_update()`:

* Drain the whole bus first, then report **once**. `playbin3`'s generic
  "state change failed" error can arrive before the element's real one
  (**proven**); when both are present the specific one wins.
* Text, in order of preference:
  1. a missing-plugin description, as `missing GStreamer plugin: <description>`;
  2. the error's own message, with the HTTP status from
     `gst_message_parse_error_details()` when there is one;
  3. for the generic `GST_STREAM_ERROR_FAILED`, the message plus the last line
     of the debug string.
* Report through `VideoBase._do_error(text)`, which stops playback, clears
  `buffering` and dispatches `on_error`. Log the same text with the filename.
* After an error the pipeline is set to NULL and stays unloaded until the next
  `load()`.
* A synchronous failure from `gst_element_set_state()` is not reported by
  itself; the real error is already on the bus.

Timing differs by platform and the provider handles both: when an HLS server
died, the error arrived after 0.05 s on Linux and 6.2 s on Windows, where
playback continued from the buffer first (**proven**).

DNS, refused connections and TLS failures (spike 11, **source** and
**proven**): with libsoup 3, `souphttpsrc` only logs the reason at DEBUG, so
the bus carries just `Internal data stream error.` from the source element.
That case (`stream-failed` from a `souphttpsrc`, no HTTP status) is reported
as "Could not connect to the server (the host name could not be resolved,
the connection was refused, or the secure connection failed)". An upstream
patch to post the `GError` would let the provider say which.

### D10. Buffering

* `buffering` is `True` only while the app wants to play and either the
  pipeline hasn't prerolled or the last `BUFFERING` percent is below 100.
* When it becomes `True`, the pipeline is held in PAUSED; when the percent
  reaches 100, it goes to PLAYING. `on_buffering` fires on each change.
* Live pipelines (`set_state` returned `NO_PREROLL`) are never paused for
  buffering (**source**).

### D11. Seeking

* `seek(percent, precise)` converts to a time and issues one flushing seek:
  `ACCURATE` when `precise`, `KEY_UNIT` otherwise.
* Before the first preroll the target is stored and issued on `ASYNC_DONE`.
* While a seek is in flight, a new one replaces the stored target and is
  issued when the current one completes. A scrubbing slider then costs one
  seek at a time.
* A seek while paused refreshes the texture with the preroll frame (D4).

The old provider blocked the UI in `get_state()` for up to a second on every
seek. **Unproven** as a whole: spike 3.

### D12. Orientation and pixel aspect ratio

* Rotated video (phone recordings) carries an `image-orientation` tag.
  **Changed:** not with `videoflip`/`glvideoflip`. `videoflip
  video-direction=auto` halved the Pi 5's nv12 rate for 4K HEVC even with no
  tag (**proven**), so the sinks don't contain it (`GstPlayer(flip=False)`).
  Instead the provider reads the tag from the bus (TAG message) and rotates
  when drawing: the NV12 path already draws through an Fbo; the GL and RGBA
  paths need one extra Fbo pass only for rotated streams.
* Non-square pixels: the GL sink adds `glcolorscale` with
  `pixel-aspect-ratio=1/1` in its caps; the NV12 path sizes its Fbo to the
  display size.

Neither was exercised: spike 9.

### D13. Options

Keys the provider reads from `options`:

| Key | Values | Purpose |
|---|---|---|
| `render` | `'auto'`, `'gl'`, `'nv12'`, `'rgba'` | D6 |
| `force_sw_decoders` | bool | sets `playbin3`'s `force-sw-decoders` flag; for testing and for broken drivers |

Unknown keys are logged once and ignored, following the other providers (and
not warning about `eos`, fixed upstream in kivy/kivy#9535).

Candidates for later, each a small addition: `http_headers` and `user_agent`
(set on the source through `playbin3`'s `source-setup`), `connection_speed`
(caps the adaptive bitrate), `max_retries` (`adaptivedemux2`, 1.26+).

### D14. Thumbnails

`generate_thumbnail(filename, time, size)` is synchronous in the other
providers. Here it builds a separate short pipeline (`playbin3`, video only,
RGBA `appsink`, audio to `fakesink`), sets PAUSED, waits for preroll with a
timeout, seeks to `time`, pulls the preroll frame, uploads it and tears the
pipeline down. It returns `None` on any failure or timeout. It uses the CPU
path: one frame doesn't justify GL sharing. A time past the end gives the
last frame. **Proven** (spike 10): 0.14–0.53 s for local files and local
HLS after the first call, failures in 2 ms to 2 s, never past the 10 s bound,
never raised.

### D15. Versions

| | Version | Why |
|---|---|---|
| Minimum on Linux and the Pi | 1.22 | first release with `adaptivedemux2`; `playbin3` no longer experimental (**source**). Keeps Debian 12 and Pi OS Bookworm, on the NV12 path |
| Minimum for the GL path (Linux, Pi) | 1.24 | `gst_gl_display_ensure_context()`, `DMA_DRM` caps (**source**) |
| Minimum on Windows | 1.24 | `playbin3` declared stable; one tier, so the GL path is always available. No distro holds Windows back: the official wheels start at 1.28 and the installer version is the user's choice |
| V4L2 DMABuf zero-copy (Pi) | 1.26 upstream | V4L2 elements negotiate `DMA_DRM` from 1.26 (**source**) |
| Tested | 1.26.2 (Pi OS Trixie), 1.28.2 (Ubuntu 26.04), 1.28.7 (Windows wheels) | the spike |

Below the minimum, the import fails with a logged reason and Kivy selects the
next provider. Nothing older than 1.26 has been run: spike 7. **Decided:**
on Linux and the Pi, 1.22 minimum with the GL path needing 1.24 (provisional:
if spike 7 shows 1.22 is unreliable, it becomes 1.24 and the docs say
Debian 12 and Pi OS Bookworm need a newer GStreamer). On Windows, 1.24
minimum. The loader therefore checks the version per platform.

The minimum is a floor, not a pin. There is no maximum: the provider accepts
any 1.x release at or above it, relying on GStreamer's stable 1.x ABI and the
import-time self-test (D1). On Linux and the Pi that means whatever the distro
or the user has installed. On Windows the `gstreamer` extras pin one exact
version only so that pip installs a matching set; a user can instead install
a newer set of the official wheels directly, run the official installer, or
point `KIVY_GSTREAMER_ROOT` at any build. Versions newer than 1.28 are
untested, and a future GStreamer 2.0 would not be loaded (different library
name).

Distro reference points: Debian 12 and Pi OS Bookworm ship 1.22, Ubuntu 24.04
ships 1.24, Debian 13 and Pi OS Trixie ship 1.26, Ubuntu 26.04 ships 1.28.

### D16. Audio provider

`audio_gstplayer.py` imports the same `GstPlayer`. It is ported in the same
PR: same loader, an audio-only `playbin3` (video flag off), and EOS and errors
polled from a Clock event instead of delivered on GStreamer's threads.

Today the audio provider is registered ahead of `sdl3`, but wheel users never
get it because the module isn't built. Once the module is always built, any
Linux machine with GStreamer installed would start playing sounds through
GStreamer. **Decided:** `gstplayer` is registered after `sdl3` for audio, so
default audio doesn't change; it still handles formats SDL3 doesn't claim, and
`KIVY_AUDIO_OUTPUT=gstplayer` selects it. A wider review of the audio
providers happens after the video work, not as part of it.

## 8. Runtime distribution

| | Windows | Linux desktop | Raspberry Pi OS |
|---|---|---|---|
| Role | secondary, optional | primary | primary |
| GStreamer comes from | official PyPI wheels `gstreamer-libs`, `gstreamer-plugins`, `gstreamer-plugins-libs`, `gstreamer-ext-runtime`, all pinned to one exact version; or the official installer | distro packages | Pi OS packages (Pi-patched builds) |
| What the user installs | nothing (the app bundles the wheels), or the installer | `gstreamer1.0-plugins-base`, `-good`, `-bad`, `gstreamer1.0-libav`, `gstreamer1.0-gl` on Debian/Ubuntu; the `gstreamer1-*` equivalents plus RPM Fusion codecs on Fedora | nothing on the Trixie desktop image (**proven**: only `gi` typelibs were missing, which this design doesn't use). Lite images unchecked |
| Size | 118 MB download, 278 MB installed; about 64 MB with a playback plugin allowlist (estimate, never run) | distro-managed | distro-managed |
| Hardware decode | D3D11/D3D12 decoders | VA-API (`va` plugin), NVIDIA (`nvcodec`) | HEVC only on the Pi 5 (`v4l2slh265dec`); H.264 is software there |
| Codec licensing | the base `gstreamer-plugins` wheel already contains gst-libav (FFmpeg decoders); MPEG-TS, needed for most HLS, is in the separate `gstreamer-plugins-restricted` wheel, which only `kivy[gstreamer-restricted]` installs | the distro's and user's choice | Pi OS ships libav |

Windows specifics the provider handles itself:

* The wheels' environment. Their `.pth` comes from `gstreamer-meta`, which
  the `gstreamer` extra doesn't install, so the loader applies
  `gstreamer_libs.gstreamer_env()` itself: `PATH`, the `GST_PLUGIN_*`,
  `GST_REGISTRY_1_0` (never over a user value), `GIO_EXTRA_MODULES` and
  `XDG_*`. `GIO_EXTRA_MODULES` carries the TLS backend: without it every
  `https://` URI failed (**proven**, spike 1).

* Exact pins. Upstream's meta-packages use `~=`, and pip assembled a mixed
  1.28.7 / 1.28.8 install on the first try; 1.28.8 and 1.28.9 were published
  without a complete Windows set.
* The plugin registry. Outside a venv the wheels use one file in `%TEMP%`
  shared by every app on that Python version. When GStreamer was loaded from
  the wheels in a bundled app, the loader sets `GST_REGISTRY_1_0` to a file
  under Kivy's home directory before `gst_init`. `GST_REGISTRY_1_0` is a
  single path (**source**). **Unproven**: spike 8.

kivyforge work (separate repo, tracked there):

* **Windows:** support the four `gstreamer_*` wheels as an optional component,
  with exact-version locking; an optional plugin allowlist; LGPL notices
  collected into the bundle.
* **Linux and Pi:** bundle nothing. Declare GStreamer as a host requirement
  with a doctor check and a per-distro install hint, and check at build time
  that the AppImage carries no GLib of its own. **Unproven**: spike 8.

## 9. Build changes

`setup.py`:

* `use_gstreamer` no longer probes pkg-config, the macOS framework or
  `gst/gst.h`. The module is built on Linux and Windows unconditionally and
  never on macOS, iOS or Android.
* Sources: `lib/gstplayer/_gstplayer.pyx` plus `_gst_loader.c`. Links: `dl`
  on Linux, nothing extra on Windows.
* `USE_GSTREAMER=0` remains as an opt-out for distro packagers.

CI and wheels:

| File | Change |
|---|---|
| `.github/workflows/manylinux_wheels.yml` | drop `USE_GSTREAMER=0` |
| `.ci/Dockerfile.armv7l` and the Pi wheel workflow | drop `USE_GSTREAMER=0` |
| `.github/workflows/windows_wheels.yml` | stop installing `kivy_deps.gstreamer_dev`. Its Python 3.14 pin stays while the other `kivy_deps.*_dev` wheels stop at cp314 |
| `.github/workflows/osx_wheels_app.yml` | check it no longer expects the GStreamer framework |
| `pyproject.toml` build requirements | remove `kivy_deps.gstreamer_dev` and `kivy_deps.gstreamer` |
| `pyproject.toml` extras | see "Windows extras" below |

Windows extras (decided; all are no-ops on other platforms, where pip can't
install GStreamer):

| Extra | Installs on Windows |
|---|---|
| `gstreamer` | the four official wheels (`gstreamer-libs`, `gstreamer-plugins`, `gstreamer-plugins-libs`, `gstreamer-ext-runtime`), all at one exact version |
| `gstreamer-restricted` | `gstreamer` plus `gstreamer-plugins-restricted` at the same version. Opt-in only; nothing else pulls it |
| `full` | Pillow plus `gstreamer` |
| `media` | `gstreamer` (and ffpyplayer on Linux and macOS until it is removed); its future is decided then |

The names follow upstream's package names, so "restricted" carries upstream's
meaning: plugins it considers patent-encumbered. To get everything:
`pip install "kivy[full,gstreamer-restricted]"`. Upstream's GPL sets are not
offered through Kivy extras.

Other code touched:

* `kivy/core/video/__init__.py`: register `gstplayer` only on Linux and
  Windows; the `try: from kivy.lib.gstplayer import GstPlayer` guard stays.
* `kivy/core/audio_output/__init__.py`: same guard; `gstplayer` registered
  after `sdl3` (D16).
* `kivy/tools/packaging/pyinstaller_hooks`: its GStreamer logic finds binaries
  through `kivy_deps.gstreamer` and `gst-inspect`. Upstream ships these hooks
  and tests them, so they need updating or the GStreamer part removed. This
  is upstream maintenance, not a packaging path for this project
  ([[use-kivyforge-for-packaging]]).
* `kivy/lib/gstplayer/__init__.py`: the public names change (`glib_iteration`
  goes away). The module docstring already says it is not a supported API.

## 10. Testing

### Automated, in Kivy's CI

No test may need a committed video file or a patented codec. Clips are
generated at test time by GStreamer itself with VP8 or VP9 in WebM
(`videotestsrc ! vp8enc ! webmmux`), which `plugins-good` provides.

| Layer | Runs where | Covers |
|---|---|---|
| Unit, no GStreamer needed | every CI job | loader failure makes the provider unavailable and selection falls through; error-text selection; option parsing; path selection as a pure function of context type and capabilities; seek coalescing state machine |
| Integration, headless | Ubuntu job with `gstreamer1.0-plugins-{base,good,bad}` and `gstreamer1.0-gl` installed, under Xvfb | load to `on_load`; frames; EOS; `eos='loop'`; pause, stop, seek while playing and paused; volume; position and duration; unload during playback; two players at once; thumbnails; missing, corrupt and non-video files each fire `on_error` exactly once; a local HTTP server for 404 and for a server that dies mid-stream |
| Render correctness | same job | a solid-colour and a four-corner test pattern, read back from the video texture: checks colour, orientation and size in `nv12` and `gl` paths. Software GL (llvmpipe) shared textures correctly in the spike's WSL2 runs; Xvfb itself is untested |
| Windows | Windows job with the pinned wheels installed | the same integration set on the `nv12` path; the first-run registry scan doesn't block (D2) |
| Audio | Ubuntu job | `audio_gstplayer` load, play, stop, loop, seek, length |

Tests skip, with a reason, when GStreamer or a needed element is missing, so
contributors without GStreamer still get a green run. HLS tests are included
only where an H.264 encoder and `hlssink2` exist, and are skipped otherwise.

### Hardware matrix, run by hand before release

The spike's `run_linux.sh` and `run_kivy3_matrix.sh` become the runner. Each
run writes JSON; the table is the acceptance bar.

| Target | Clip and path | Pass when |
|---|---|---|
| Pi 5, Pi OS Trixie, EGL | 4K30 HEVC, `gl` | at least 28 fps, at most 25% of one core, `glupload` input is DMABuf |
| Pi 5 | 1080p30 H.264 (software), `auto` | at least 28 fps |
| Pi 5, GLX | 4K30 HEVC | plays, and the slow-path warning is logged |
| Windows, desktop GL | 4K30 H.264, `gl` | at least 28 fps, at most 25% CPU, tick under 1 ms |
| Windows, ANGLE | 1080p30, `auto` | selects `nv12`, at least 28 fps |
| Linux x86, Intel or AMD | 4K30 H.264 and HEVC, `gl`, EGL | hardware decoder selected, `glupload` input is DMABuf |
| Linux x86, NVIDIA proprietary | same | plays at 28 fps; path recorded |
| All | HLS TS, HLS fMP4, DASH | play; `on_buffering` toggles; killing the server fires `on_error` |
| All | 200 load/unload cycles | no growth in threads, file descriptors or GL textures |
| All | 30 minutes of looped playback | no drift between audio and picture by ear; memory flat |

Hardware plan (decided): release isn't blocked on hardware we don't have.

* **Intel:** the user's Windows laptop booted from an Ubuntu live USB with
  persistence, driven over SSH like the Pi 5.
* **NVIDIA, AMD, Pi 4:** untested unless hardware turns up. The same USB stick
  in the development desktop would cover NVIDIA.
* **Community test app:** a kivyforge-built AppImage (x86_64 and aarch64) that
  runs the matrix against bundled clips and writes one JSON file (GPU, driver,
  GStreamer version, decoder chosen, frame rate, CPU) for the tester to send
  back. It can't be built from the spike app, which needs PyGObject; it sits
  on the new core and a Kivy 3.0 Linux wheel from the provider branch, so it
  follows PR A. It is also the AppImage half of spike 8.

### Packaging

* A kivyforge Windows build with the GStreamer wheels: plays from the bundle,
  first launch doesn't freeze, HTTPS streams work.
* A kivyforge AppImage on Ubuntu and on the Pi: finds the system GStreamer and
  plays; a clear log line when GStreamer is absent.

## 11. Documentation

| Where | What |
|---|---|
| `kivy/core/video/video_gstplayer.py` module docstring | what the provider is, platforms, how GStreamer is found, the render paths and how to read the log line, options, codec notes, version floors, troubleshooting (`GST_DEBUG`, missing plugins) |
| `kivy/uix/video.py`, `options` property | add the `gstplayer` keys to the per-provider list |
| `doc/sources/guide/environment.rst` | `KIVY_GSTREAMER_ROOT`; `KIVY_VIDEO` values per platform; a pointer to GStreamer's own variables |
| `doc/sources/installation/installation-linux.rst`, `installation-rpi.rst` | the runtime packages for video, per distro; no `-dev` packages needed any more |
| `doc/sources/gettingstarted/installation.rst`, `doc/sources/installation/installation-windows.rst` | `kivy[gstreamer]`, `kivy[gstreamer-restricted]` and what each installs; that `full` leaves out the restricted set, with the `kivy[full,gstreamer-restricted]` line; Media Foundation is the default |
| `doc/sources/guide/licensing.rst` | GStreamer is LGPL; what the Windows wheels contain (FFmpeg decoders; the separate restricted set); what that means for a shipped app |
| `doc/sources/migration.rst` | `kivy_deps.gstreamer` is gone; extras changed; GStreamer minimum (1.22 on Linux, 1.24 on Windows); `on_error` instead of silent failure; `kivy.lib.gstplayer` internals changed; GStreamer audio now comes after SDL3 |
| `doc/sources/guide/packaging-windows.rst`, `packaging-osx.rst` | remove or correct the GStreamer instructions that assume `kivy_deps.gstreamer` |
| `doc/sources/changelog.rst` | the usual entry |
| kivyforge docs | enabling GStreamer on Windows; the Linux and Pi host requirement |

## 12. Delivery

| Step | Content | Depends on |
|---|---|---|
| 0 | Spikes 1, 2 and 3 (section 13) | nothing |
| 1 | PR: SDL3 window requests EGL on Linux (`sdl3-linux-force-egl`); PR: `SDL_Init` check | nothing; can go up now |
| 2 | PR A: runtime loader, new `GstPlayer` core, `VideoGstplayer` on the `nv12` path, errors, buffering, seeking, thumbnails, audio port, `setup.py` and CI changes, unit and integration tests | spikes 1 and 3 |
| 3 | PR B: the `gl` path and path selection, render tests | PR A, spike 2 |
| 4 | PR C: extras, installation, licensing and migration docs, PyInstaller hook cleanup | PR A |
| 5 | kivyforge: Windows component, Linux host check | PR A, spike 8 |
| 6 | Community test app (kivyforge AppImage), sent to testers with Intel, AMD, NVIDIA and Pi 4 hardware | PR B, step 5 |
| 7 | Hardware matrix and fixes | PR B, step 6 |

PR A is useful by itself: it gives every Linux and Pi wheel a working
provider with HLS, DASH and `on_error`, on the CPU path. PR B adds the
zero-copy path. The remaining spikes run alongside steps 2 to 4.

## 13. Spikes for the open technical questions

The first three decide the shape of the core and should run before PR A.
**Spikes 1 to 3 are done** (2026-11-20, `GSTREAMER-CORE-SPIKES.md`): all three
answered yes, on Windows, the Pi 5 and WSL2. **Spikes 10 and 11 are done**
too (same file): thumbnails are bounded and fast; DNS, connection and TLS
failures can't be told apart from the bus with libsoup 3, so they get one
clearer message.

| # | Question | Method | Done when |
|---|---|---|---|
| 1 | Does the runtime-loaded C/Cython core work as designed, including `gst_init` on a worker thread? | Build `_gst_loader.c` and a minimal `_gstplayer.pyx` (no GStreamer headers); run the spike's headless cases through it on Windows (wheels) and Linux (distro); delete the registry and time the first `load()` | all headless cases pass on both; the UI thread never blocks more than one frame during first-run init; the struct self-test passes on 1.26 and 1.28 |
| 2 | Can the GL contexts be handed over with `gst_element_set_context()` on the pipeline, and created with `gst_gl_display_ensure_context()`, instead of the spike's sync handler? | Change the spike's GL setup to those calls (through the C layer from spike 1); run on Windows WGL and Pi 5 EGL with `GST_DEBUG=gldisplay:5` | the log shows the context created "from other context"; DMABuf still reaches `glupload` on the Pi; no callback runs off Kivy's thread |
| 3 | Do paused frames, seeking and looping behave through the `Video` widget? | Implement D4, D5 and D11 in the spike app: preroll pull after `load()` and after paused seeks, seek coalescing, `stop()`, `eos='loop'`, and the reduced `playbin3` flags. Drive it with a `VideoPlayer` slider | first frame shows without playing; paused seeks update the picture; 50 rapid seeks leave one in flight; loop restarts cleanly from EOS; DMABuf still passes with the reduced flags |
| 4 | Is per-stream path selection worth it? | Prototype a sink bin that accepts GL/DMABuf and system-memory caps and links the GL or NV12 branch on the caps event; measure Pi 5 software H.264 and hardware HEVC | either it gets both best cases (HEVC at about 15%, H.264 at about 48%) and is adopted into D6, or it is dropped and `auto` stays per-context |
| 5 | Does sharing work with a GLES context and under KMSDRM? | Pi 5 from the console (no desktop), Kivy 3.0 on SDL3 KMSDRM, and with a GLES2 context; run the matrix | 4K HEVC meets the acceptance bar, or the limits are documented |
| 6 | Does the zero-copy path hold on x86 Linux? | A native Linux machine with Intel or AMD (VA-API) and one with NVIDIA's driver: run `run_kivy3_matrix.sh` | decoder and `glupload` input recorded per GPU; any driver that breaks with forced EGL is identified |
| 7 | What actually works on 1.22 and 1.24? | Debian 12 and Ubuntu 24.04 containers, headless cases plus the struct self-test; on Windows, the same cases against the official 1.24 installer | D15's floors confirmed or raised |
| 8 | Do the Windows and AppImage packages work as assumed? | kivyforge build with the wheels: registry redirect, pruned plugin set, HTTPS (TLS modules are loaded dynamically and aren't in the size estimate), the installer's root variable name on a machine with the installer; an AppImage on Ubuntu and the Pi; confirm what the `GPL-2.0-or-later` and `LGPL-3.0-only` entries in the wheels' licence metadata refer to | bundled app plays local and HTTPS video on a clean VM; real pruned size known; AppImage loads system GStreamer with no symbol clashes; licence entries explained |
| 9 | Are colour, rotation and pixel aspect ratio right? | Clips with BT.601 and BT.709, limited and full range, 90-degree rotation, anamorphic pixels, and 10-bit HEVC; compare read-back pixels with a reference in both paths | within tolerance in `gl` and `nv12`; cost of 10-bit on the Pi 5 measured |
| 10 | Is a synchronous thumbnail reliable and fast enough? | Implement D14; time it on local files, HTTP and HLS, and on missing files | returns within a bounded time in every case; never hangs |
| 11 | Can DNS and connection failures say what went wrong? | Capture all bus messages and error details for DNS failure, refused connection and TLS failure on 1.26 and 1.28 | a better text is found and added to D9, or an upstream issue is filed |

Two smaller checks ride along with these: the single unexplained run with no
output (`gl` backend on Wayland, Pi 5) repeated 20 times in a loop, and what
happens to shared textures when Kivy's GL context is recreated (fullscreen
toggle, display change).

## 14. Decisions

Made with the user on 2026-11-19.

| # | Question | Decision |
|---|---|---|
| 1 | Where GStreamer sits in the audio provider order | After `sdl3`, so default audio is unchanged (D16). The audio providers get their own review after the video work is complete |
| 2 | What the Windows pip extras install | `gstreamer` = the four official wheels at one exact version; `gstreamer-restricted` = that plus the restricted wheel; `full` = Pillow + `gstreamer`; `media` unchanged in meaning (section 9) |
| 3 | MPEG-TS and the other restricted plugins on Windows | Never installed by default. Users opt in with `kivy[gstreamer-restricted]`, or `kivy[full,gstreamer-restricted]` |
| 4 | Hardware for the untested Linux cases | Don't block the release. Intel on the user's laptop from a live USB; a kivyforge test app for community members with other hardware; NVIDIA and Pi 4 untested unless hardware turns up (section 10) |
| 5 | Minimum GStreamer version | Linux and Pi: 1.22, with the GL path needing 1.24, provisional until spike 7. Windows: 1.24 (D15) |

## 15. Risks

| Risk | Mitigation |
|---|---|
| Mirrored struct layouts drift in a future GStreamer | only four small public structs, all part of the stable 1.x ABI; the import-time self-test; CI on the newest release |
| Kivy 3.0 on X11 or Xwayland gets GLX, so users land on the slow path | the EGL window change (step 1); the provider's warning; documentation |
| Forcing EGL on Linux breaks some driver | that change falls back to GLX when EGL fails; spike 6 |
| Upstream's Windows wheels are published incomplete or mixed | exact pins in the extra; kivyforge locks; pin only versions with a complete set |
| The official wheels' licence metadata lists `GPL-2.0-or-later` and `LGPL-3.0-only` among component licences | possibly dual-licensed components; confirm in spike 8 before `full` ships them |
| Two GStreamer copies in one process on Windows (wheels and an installer) | the loader takes the first match in a fixed order and logs it; `KIVY_GSTREAMER_ROOT` overrides |
| Windows 1.28.7 wheels: an HLS stream whose demuxer is missing (TS without the restricted set) aborts the process in libsoup at teardown; reproduced with PyGObject too (spike 1) | report upstream; document `kivy[gstreamer-restricted]` for HLS TS on Windows until fixed |
| Windows 1.28.7 wheels: a subtitle track stalls playback with `text` off and `wasapi2sink` (spike 3) | `text` kept on, on Windows only; report upstream; stream selection later |
| An app also uses PyGObject and GStreamer directly | same `libgstreamer` in the process; `gst_init` is idempotent; nothing in this design owns a GLib main loop |
| Crashes at interpreter exit with streaming threads alive | keep today's `atexit` hook that sets every pipeline to NULL |
| Distro GStreamer lacks a codec (Fedora without RPM Fusion) | missing-plugin errors name the missing decoder through `on_error`; documented |

## References

GStreamer source and documentation, 1.28 branch:

* `gst_app_sink_try_pull_sample`, `gst_app_sink_try_pull_preroll`:
  [gstappsink.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/app/gstappsink.c)
* `playbin3` usage, buffering duties, flags:
  [gstplaybin3.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst/playback/gstplaybin3.c),
  [gstplay-enum.h](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst/playback/gstplay-enum.h),
  [gstplaysink.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst/playback/gstplaysink.c)
* Context propagation:
  [design/context.md](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-docs/markdown/additional/design/context.md)
* Buffering and live pipelines:
  [advanced/buffering.md](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-docs/markdown/application-development/advanced/buffering.md)
* GL contexts and displays:
  [gstglcontext.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstglcontext.c),
  [gstgldisplay.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstgldisplay.c),
  [gstglutils.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstglutils.c),
  [gstglcontext_wgl.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/wgl/gstglcontext_wgl.c)
* DMABuf import requires EGL:
  [gstglupload.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstglupload.c)
* Sync meta and `GST_MAP_GL`:
  [gstglsyncmeta.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstglsyncmeta.c),
  [gstglbasememory.h](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/gst-libs/gst/gl/gstglbasememory.h)
* HTTP error details and retries:
  [gstsouphttpsrc.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-good/ext/soup/gstsouphttpsrc.c),
  [gstadaptivedemux.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-good/ext/adaptivedemux2/gstadaptivedemux.c)
* Plugin and registry paths:
  [gstregistry.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gstreamer/gst/gstregistry.c)
* Automatic rotation:
  [gstvideoflip.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-good/gst/videofilter/gstvideoflip.c),
  [gstglvideoflip.c](https://github.com/GStreamer/gstreamer/blob/1.28/subprojects/gst-plugins-base/ext/gl/gstglvideoflip.c)
* Release notes: [1.22](https://gstreamer.freedesktop.org/releases/1.22/),
  [1.24](https://gstreamer.freedesktop.org/releases/1.24/),
  [1.26](https://gstreamer.freedesktop.org/releases/1.26/)
* Downloads and wheels: [download page](https://gstreamer.freedesktop.org/download/)

Project notes: `GSTREAMER-SPIKE.md` and `gstreamer-spike/` (scripts and
results), in this folder.
