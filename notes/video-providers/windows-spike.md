# Windows video provider — spike

Status: investigation only, no code changes. Written against the 3.0 goals:
platform media framework first, zero-copy to GPU, near-zero binary
distribution, streaming where the platform allows, documented capability
matrix. Compared against the two providers that already embody this
architecture: [`video_avfoundation.pyx`](../../kivy/core/video/video_avfoundation.pyx)
(macOS/iOS) and [`video_android.py`](../../kivy/core/video/video_android.py).

## TL;DR

Primary provider: **Media Foundation via `IMFMediaEngine`**, the high-level
playback API (Microsoft's analogue of `AVPlayer`). It uses Windows' in-box
decoders (no bundled codec, no patent exposure) and hands back frames as a
caller-supplied `ID3D11Texture2D` via `TransferVideoFrame`.

How that texture reaches GL depends on which GL backend is active, and
Kivy on Windows genuinely has two to deal with:

- **`angle_sdl3`** (ANGLE/GLES over D3D11 — CI-tested, but *not* the pip
  default): wrap the D3D11 texture directly with ANGLE's
  **`EGL_ANGLE_d3d_texture_client_buffer`** extension
  (`eglCreatePbufferFromClientBuffer` with `EGL_D3D_TEXTURE_ANGLE`). This is
  the literal Windows counterpart of the `EGL_ANGLE_iosurface_client_buffer`
  trick `video_avfoundation.pyx` already uses on macOS — same mechanism
  class, same `nofree=True` Texture-wrapping code pattern, no further GPU
  copy. True zero-copy, not just "no CPU copy."
- **`glew` / `sdl3`** (native desktop GL via WGL — the actual pip-install
  default): there's no EGL context to extend, so the interop point has to be
  the WGL extension **`WGL_NV_DX_interop2`** instead. Still GPU-to-GPU, no
  CPU copy, but not the same clean single-mechanism story, and driver
  support is less consistent across vendors (see risks).

So the provider should detect the active backend at runtime
(`cgl_get_initialized_backend_name()`, already used elsewhere in
`texture.pyx`) and pick the matching interop path, falling back to a
CPU-copy path if neither registers successfully — same philosophy as
`video_avfoundation`'s zero-copy probe-and-fall-back.

This is *not* a drop-in port of the AVFoundation pattern — Windows forces an
extra backend-detection branch AVFoundation doesn't need, and the streaming
goal is weaker than on the other two platforms. Both are explained below.

**Update:** both interop bridges are no longer just a proposal — both are
built and verified against real hardware now (`WGL_NV_DX_interop2` in
Prototype 1, `EGL_ANGLE_d3d_texture_client_buffer` in Prototype 2, see
below), along with their fallback triggers on mismatch. The two bridges
turned out to differ in strictness: WGL only needs the right *adapter*,
ANGLE needs the exact *device object* it's using internally. **A real
`IMFMediaEngine`-decoded frame has now been pushed through both bridges
too (Prototype 3), and playback over time plus seeking are confirmed as
well (Prototype 4, below)** — the architecture is confirmed end-to-end on
this hardware, not just the bridge in isolation. **The real
`video_mediafoundation.pyx` provider is now built and verified in a
running Kivy app under both GL backends** (see "Real implementation"
below) — `glew`/`sdl3` gets true zero-copy; `angle_sdl3` currently runs
via the CPU-copy fallback because this SDL3 build has no working way to
expose its own in-use ANGLE device externally, a real finding the
prototypes' self-contained design couldn't have caught. Broader hardware
coverage (AMD, other driver generations) and finding a working
`angle_sdl3` device accessor are the pieces still open; see "Suggested
next steps" at the end.

## Fact-finding on the current Kivy Windows graphics stack

**Correction after review:** an earlier draft of this spike claimed ANGLE
isn't really available on Windows. That was wrong — checked against the
source below.

- `c_options['use_angle_gl_backend'] = platform in {'darwin', 'ios'}`
  ([`setup.py:333`](../../setup.py)) only gates whether Kivy's *own* C++
  `EGLANGLE` wrapper class
  ([`egl_angle.pyx`](../../kivy/graphics/egl_backend/egl_angle.pyx)) and the
  bare `"angle"` backend (used by macOS/iOS, native Metal surface) get
  compiled. That part of the read was accurate.
- But Windows has a **separate, independent path to ANGLE**: the
  `"angle_sdl3"` CGL backend. It is not an alias for plain `sdl3` — in
  [`_window_sdl3.pyx:174-177`](../../kivy/core/window/_window_sdl3.pyx), when
  `gl_backend_name == "angle_sdl3"` Kivy sets
  `SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, 4)` (the ES profile) and
  `SDL_SetHint(SDL_HINT_VIDEO_WIN_D3DCOMPILER, "none")` — a hint that only
  means anything if SDL3 is loading ANGLE to satisfy that ES context request.
  SDL3 itself then creates and owns the EGL context
  (`sdl_manages_egl_context` is `True` for `angle_sdl3`, so it goes through
  plain `SDL_GL_CreateContext`, not Kivy's `EGLANGLE` class — that class is
  genuinely macOS/iOS-only). The ANGLE binaries themselves ship as
  `kivy_deps.angle` (a build of Microsoft's ANGLE fork), installed alongside
  `kivy_deps.sdl3`.
- **It is not the default.** [`installation.rst:394-397`](../../doc/sources/gettingstarted/installation.rst)
  is explicit: `KIVY_GL_BACKEND` defaults to `glew` on Windows; `angle_sdl3`
  and `sdl3` are both selectable alternates. A plain `pip install kivy` user
  is on native desktop GL (WGL), not ANGLE.
- **But it's not a toy, either.** Both
  [`windows_wheels.yml`](../../.github/workflows/windows_wheels.yml) and
  [`test_windows_python.yml`](../../.github/workflows/test_windows_python.yml)
  pin `KIVY_GL_BACKEND: angle_sdl3` for the whole CI run — Kivy's own Windows
  CI exercises the ANGLE path, not the pip default. And upstream ANGLE
  defaults to its **D3D11** renderer on Windows.

Net effect: Windows has *two* GL backends in real, maintained use
(`glew`/`sdl3` native desktop GL by default; `angle_sdl3` as a CI-tested,
opt-in alternate), and they need different zero-copy mechanisms:

- Under `angle_sdl3`, there **is** an EGL context, backed by D3D11, exactly
  like the EGL context AVFoundation's ANGLE build gets on macOS. ANGLE
  exposes `EGL_ANGLE_d3d_texture_client_buffer` for it — the literal
  Windows counterpart of `EGL_ANGLE_iosurface_client_buffer`, letting a raw
  `ID3D11Texture2D*` be wrapped straight into an EGL pbuffer/GL texture via
  `eglCreatePbufferFromClientBuffer(..., EGL_D3D_TEXTURE_ANGLE, ...)`. No
  additional GPU copy, no vendor-specific WGL extension — true zero-copy,
  same mechanism *class* and the same `nofree=True` Texture-wrapping code
  pattern `video_avfoundation.pyx` already uses.
- Under `glew`/`sdl3`, there's no EGL context to extend, so the interop
  point has to be the WGL extension `WGL_NV_DX_interop2` instead — see
  candidate A below.

This also means the choice isn't purely technical: if Kivy wanted the
*default* Windows experience to get the clean, single-mechanism zero-copy
path, that's really an argument for making `angle_sdl3` the default GL
backend on Windows generally — a graphics-backend-wide decision (shader
behavior, perf characteristics, an extra DLL in the default install) well
beyond what a video spike should drive. Noted as a strategic observation
for whoever owns that decision, not a recommendation this spike is making.

## Candidates considered

### A. `IMFMediaEngine` + `TransferVideoFrame`, bridged to GL per active backend — recommended primary

The Media Foundation "Media Engine" is the high-level playback object:
`SetSource(url)`, `Play`/`Pause`/`Seek`, an `OnVideoStreamTick` callback that
fires when a new frame is ready. It's built on the same in-box decoder
infrastructure DirectShow and raw `IMFSourceReader` use, so it gets
hardware-accelerated H.264/HEVC/AV1 decode for free where the OS/OEM provides
an MFT for the codec.

Frame delivery is `TransferVideoFrame(ID3D11Texture2D *pDstSurf, ...)` —
Microsoft's docs describe it as *copying* the current frame into a
texture you supply. That's a GPU-side blit (via an internal video processor),
**not a CPU readback**, but it is one extra GPU copy beyond the decoder's own
output surface — not the same "zero-copy, literally sample the decoder's own
surface" guarantee AVFoundation gets from `IOSurface`. Worth stating
precisely in the eventual provider docstring, the way `video_avfoundation.pyx`
is precise about what is and isn't copied.

Pipeline: create a `ID3D11Device` with `D3D11_CREATE_DEVICE_VIDEO_SUPPORT`,
wrap it with `MFCreateDXGIDeviceManager`, hand that to the engine via
`IMFMediaEngineEx::SetDXGIManager`. On each `OnVideoStreamTick`,
`TransferVideoFrame` into a persistent `ID3D11Texture2D` (render-target bind
flag required). From there, **the bridge into GL depends on the active CGL
backend** (checked at runtime via `cgl_get_initialized_backend_name()`):

- **`angle_sdl3`**: wrap that same `ID3D11Texture2D` with
  `eglCreatePbufferFromClientBuffer(dpy, EGL_D3D_TEXTURE_ANGLE, tex, cfg,
  attribs)`, then `eglBindTexImage` into a `GL_TEXTURE_2D`, same as
  `_frame_to_texture`'s zero-copy branch in `video_avfoundation.pyx`. One
  GPU blit total (the `TransferVideoFrame` one); from there on it's a true
  zero-copy GL texture.
- **`glew` / `sdl3`** (the default): `wglDXRegisterObjectNV` the texture
  into the current WGL context, `wglDXLockObjectsNV` it per frame, bind as
  a GL texture. Also GPU-to-GPU, no CPU copy, but subject to the
  `WGL_NV_DX_interop2` driver-support caveats in the risks section below.

Either way, drive the existing FBO/shader pipeline the same way
`video_android.py` does (`Callback`-driven texture-bind) — no
`blit_buffer`, no `memcpy`, in either branch.

Open implementation risk, specific to Windows and absent on macOS: the
`ID3D11Device` the provider creates has to live on **the same GPU adapter**
Kivy's GL context is rendering on. On a multi-GPU laptop (integrated +
discrete), picking the wrong `IDXGIAdapter` either breaks the interop
registration outright (both the EGL and WGL paths) or silently forces a slow
copy-through-system-memory path. AVFoundation has no equivalent problem (one
GPU, no adapter enumeration). This needs to be solved by enumerating
adapters and matching against the one the GL context's window is actually
on — not just "take the default adapter".

Threading: Media Engine callbacks land on an MF work-queue thread, not
Kivy's main thread. Follow the pattern already in this codebase
(`video_avfoundation` polls `hasNewFrame()`/`isBuffering()`/`isEOS()` from a
`Clock.schedule_interval`, rather than driving GL calls from a callback
thread) instead of touching GL from the MF callback directly.

### B. Raw `IMFSourceReader` against the decoder's own D3D11 texture array — true zero-copy, phase 2 candidate

Lower-level: configure `IMFSourceReader` with a DXGI device manager and
`MF_SOURCE_READER_D3D11_BIND_FLAGS`, pull `IMFSample`s whose buffer is an
`IMFDXGIBuffer`, and get the *decoder's actual output texture* (usually one
slice of an NV12 texture array) via `IMFDXGIBuffer::GetResource` +
`GetSubresourceIndex`. Share that slice via the same per-backend bridge as
(A) (`EGL_ANGLE_d3d_texture_client_buffer` under `angle_sdl3`,
`WGL_NV_DX_interop2` under `glew`/`sdl3`), but skip `TransferVideoFrame`'s
blit entirely — this is the pattern FFmpeg's `d3d11va` hwaccel and mpv's
`d3d11va` vo use.

This is strictly better on the zero-copy goal (no GPU blit at all), but it
replaces `TransferVideoFrame`'s one function call with: manual demux/decode
session setup, NV12-to-RGB color conversion (now your problem, not the video
processor's — needs a shader, since GL doesn't sample NV12 planes as a single
unit), texture-array padding/alignment handling, and losing
`IMFMediaEngine`'s built-in A/V sync and rate control. Recommended as a
**follow-up optimization once (A) ships and is stable**, not a v1 target —
the complexity jump is large for a marginal-cost GPU blit that's already not
touching the CPU.

### C. `Windows.Media.Playback.MediaPlayer` (WinRT) — best streaming story, biggest integration risk

The WinRT media stack has a real adaptive-streaming API:
`Windows.Media.Streaming.Adaptive.AdaptiveMediaSource.CreateFromUriAsync`
supports both HLS and DASH manifests, wrapped into a `MediaSource` for
`MediaPlayer`. If the streaming goal mattered more than it does here
("wanted but not a hard requirement"), this would be the obvious pick.

Two problems kept this from being the recommendation:

1. Frame delivery (`IsVideoFrameServerEnabled` +
   `CopyFrameToVideoSurface(IDirect3DSurface)`) has the same
   "GPU copy, not literal zero-copy" shape as `TransferVideoFrame` — no
   advantage over (A) on the zero-copy goal.
2. **Unconfirmed**: whether `MediaPlayer` is usable at full fidelity from an
   unpackaged Win32 process (Kivy apps are typically `pip install`ed, not
   MSIX-packaged). Some WinRT classes are gated behind "package identity
   required"; search didn't turn up a definitive answer either way for
   `MediaPlayer` specifically, and Microsoft's own compatibility list is the
   place to check, not a search result. This is exactly the kind of
   assumption a spike should flag rather than build on.

Worth a narrow follow-up spike of its own if HLS/DASH support becomes an
actual requirement later, specifically aimed at resolving point 2 first.

### D'. GStreamer / mpv (libmpv) as *primary* — considered and rejected

Worth addressing directly since the list of open Media Foundation risks
above reads, at a glance, like an argument for a more mature third-party
stack instead. It isn't, for two reasons:

1. **GStreamer on Windows doesn't avoid Media Foundation, it wraps it.**
   `gst-plugins-bad`'s D3D11 decoder elements (e.g. `d3d11h264dec`) are
   themselves MFT wrappers around the same in-box hardware decoders
   `IMFMediaEngine` would use. Picking GStreamer trades *writing* the
   `WGL_NV_DX_interop2`/`EGL_ANGLE_d3d_texture_client_buffer` bridge for
   *reusing* GStreamer's own already-shipped, already-tested D3D11↔GL
   interop elements — a real reduction in implementation risk. The explicit
   cost is the thing goal 3 is written to avoid: a large separate runtime
   Kivy would have to ship, not the OS's own framework in the sense goal 1
   means. This tradeoff already exists today as `video_gstplayer.py` /
   `video_ffpyplayer.py`, correctly scoped as **secondary** (CPU-copy
   allowed, bundling allowed) — nothing here argues for promoting either to
   primary.
2. **mpv/libmpv is worse on these goals, not better.** It can drive
   Media Foundation's D3D11VA for hardware decode, but libmpv is built on
   FFmpeg, which bundles its own software decoder for every codec as a
   fallback path regardless of which hardware path is taken. That
   reintroduces exactly the codec-patent/binary-size exposure goal 3 exists
   to eliminate.

The Media Foundation risks listed above are almost entirely about the
*GPU-interop bridge* (a Windows graphics-stack problem every option here
has to solve one way or another), not about `IMFMediaEngine`'s decode or
playback capability. Swapping frameworks moves who already solved that
bridge; it doesn't remove the need to solve it, and it trades a
prototype-sized implementation risk for a by-design goals violation. Hence:
de-risk the interop bridge directly (prototype, §"Suggested next step"),
with the CPU-copy fallback as the safety net so the provider degrades
gracefully rather than breaking outright on GPUs where the chosen interop
extension doesn't register — same resilience philosophy
`video_avfoundation.pyx` already uses for its own zero-copy probe.

### D. Legacy DirectShow — ruled out

Superseded by Media Foundation since Windows 7/8; Microsoft has pointed new
development at MF for over a decade. No reason to build new code against it.

### E. Keep only `ffpyplayer` / `gstplayer` on Windows — ruled out as primary

Both already exist and already work on Windows today — they stay as
**secondary** providers (this spike doesn't threaten them). But neither
satisfies "platform's own media framework" or "near-zero binary
distribution" (ffpyplayer bundles/depends on an FFmpeg build; GStreamer is a
large separate runtime) — wrong fit for *primary*, which is what this spike
was scoped to find.

## Capability matrix (draft — Windows row is this spike's proposal, not implemented)

| | avfoundation (macOS/iOS) | android | **windows (proposed, A)** | ffpyplayer / gstplayer |
|---|---|---|---|---|
| Uses OS media framework | ✅ AVFoundation | ✅ `android.media.MediaPlayer` | ✅ Media Foundation | ❌ bundled/separate decoder |
| Frame path | zero-copy (`IOSurface` → EGL texture) | zero-copy (`SurfaceTexture` → GL external texture) | `TransferVideoFrame` (one GPU blit), then **true zero-copy** via `EGL_ANGLE_d3d_texture_client_buffer` under `angle_sdl3`, or GPU-to-GPU via `WGL_NV_DX_interop2` under `glew`/`sdl3` (the default) — **no CPU copy either way** | CPU copy (`blit_buffer`) |
| Binary added by Kivy | none | none | none | ffmpeg libs / GStreamer runtime |
| HLS | ✅ | device/OS dependent | ⚠️ unconfirmed for `IMFMediaEngine`, needs validation | ✅ (ffmpeg demuxer) |
| DASH | ❌ | ❌ | ❌ (would need candidate C) | ⚠️ partial |
| Thumbnail generation | ✅ `AVAssetImageGenerator` | not implemented | ✅ `IMFSourceReader` can grab+seek a single frame CPU-side without playback | ✅ |
| Known fragility | zero-copy probe can fail on non-ANGLE / exotic codec path → CPU fallback + EOS | — | under the default `glew`/`sdl3` backend, WGL interop driver support is inconsistent across GPU vendors/drivers (see risks) → needs CPU fallback, same philosophy as avfoundation's probe-and-fall-back; the `angle_sdl3` path is the more reliable one but isn't the default | — |

## Prototype 1 results: `WGL_NV_DX_interop2` — confirmed on real hardware

Built and run against this spike's own dev machine — a dual-GPU laptop
(NVIDIA GeForce RTX 2060 + Intel UHD Graphics 630), the exact scenario risk
3 below worried about. Source:
[`prototype/wgl_dx_interop_test.cpp`](prototype/wgl_dx_interop_test.cpp).
It does *not* decode real video — it fills a plain D3D11 texture with a
test-pattern gradient and pushes just that texture through the interop
bridge, because the bridge (not `IMFMediaEngine`'s decode API) was the
actual open question. For each DXGI adapter it creates a D3D11 device,
fills a 256×256 `B8G8R8A8_UNORM` texture, and tries
`wglDXOpenDeviceNV` → `wglDXRegisterObjectNV` → `wglDXLockObjectsNV` →
draw as a textured quad in the one WGL context (which the driver put on
the NVIDIA GPU) → `glReadPixels` → save as BMP.

**Result: works, and fails exactly where predicted.**

- Adapter 0 (NVIDIA RTX 2060 — the GPU the GL context is actually on):
  every call succeeds. `glReadPixels` matches the source texture's content
  pixel-for-pixel (corner `(0,0,128,255)`, center `(128,128,128,255)`,
  same values an independent D3D-side `CopyResource`-to-staging readback
  saw), and the saved screenshot shows the correct gradient — true
  zero-copy GPU-to-GPU content, confirmed, not just "the calls returned
  success." `WGL_ACCESS_READ_ONLY_NV` (the semantically correct mode for a
  decode-only consumer, which is what the eventual provider is) works
  identically to `WGL_ACCESS_READ_WRITE_NV` — no need for the more
  permissive mode.
- Adapter 1 (Intel UHD 630) and adapter 2 (Microsoft Basic Render Driver /
  WARP): `wglDXOpenDeviceNV` returns `NULL` outright —
  `GetLastError() = 0xC0070032` (`ERROR_NOT_SUPPORTED`, wrapped as an
  NTSTATUS-style HRESULT) for the Intel adapter, `0` for WARP. This is
  exactly risk 3's adapter-mismatch failure mode, confirmed on real
  mismatched hardware. Importantly, it fails **loudly and immediately** at
  the very first interop call, before any texture is registered or
  touched — not a silent slow path, not a crash. That's the easy case for
  a fallback: a provider can treat a `NULL` from `wglDXOpenDeviceNV` as
  the "fall back to CPU copy" signal with no ambiguity.

One real bug surfaced and fixed along the way, worth recording because it
nearly produced a false negative: the first run showed every call
succeeding but the saved screenshot was solid black. An independent D3D
staging-texture readback (added specifically to bisect the pipeline)
showed the source texture was correctly filled, which narrowed it to the
GL side — and the actual cause was `ShowWindow(hwnd, SW_SHOWMINNOACTIVE)`
on the probe's own test window, which **minimizes** it, leaving WGL
rendering into a window with no real client area. Switching to
`SW_SHOWNOACTIVATE` fixed it outright. Noted here because it's exactly
the kind of self-inflicted bug that would read as "zero-copy interop
doesn't work" if the verification step (independent D3D readback,
raw-pixel logging, not just HRESULT checks) hadn't been there to catch it
— the same lesson `video_avfoundation.pyx`'s own precise, value-checked
zero-copy probe already encodes.

**Still unverified / next:** this machine's NVIDIA driver is new enough to
have `WGL_NV_DX_interop2` solidly; the AMD/older-Intel driver variance the
spike's research turned up is still an open question. The
`EGL_ANGLE_d3d_texture_client_buffer` path under `angle_sdl3` is now
covered too — see Prototype 2 below.

## Prototype 2 results: `EGL_ANGLE_d3d_texture_client_buffer` — confirmed on real hardware

Counterpart to Prototype 1, for the `angle_sdl3` backend path instead of
`glew`/`sdl3`. Source:
[`prototype/egl_angle_interop_test.cpp`](prototype/egl_angle_interop_test.cpp).
Same test-pattern-texture approach as Prototype 1 (the bridge, not
`IMFMediaEngine`'s decode API, is the open question), but needed more
scaffolding than the WGL probe: no official EGL/GLES headers or import
libs ship with just the ANGLE DLLs either, so the EGL and GLES2 entry
points are hand-declared and resolved via `GetProcAddress`/
`eglGetProcAddress`, the same approach Prototype 1 used for
`WGL_NV_DX_interop2`. ANGLE's GLES2 has no fixed-function pipeline, so
drawing the test quad needed an actual shader + VBO instead of the WGL
probe's `glBegin`/`glEnd`. One upside: EGL pbuffers are offscreen, so this
probe needs no window at all, unlike the WGL one.

The key question Prototype 1 couldn't test — because `WGL_NV_DX_interop2`
only has one notion of "device" (the GPU adapter) — was whether ANGLE's
match requirement is *adapter-level* or *exact-device-level*. So this
probe runs three cases: (A) a D3D11 texture created on ANGLE's own
internal device, queried via `EGL_ANGLE_device_d3d`
(`eglQueryDisplayAttribEXT`/`eglQueryDeviceAttribEXT`); (B) a texture on
an independent D3D11 device created on the *same* adapter as ANGLE; (C) a
texture on an independent device on a *different* adapter.

**Result: works on the matching device, and fails identically — not just
similarly — on both mismatch cases.**

- ANGLE picked the NVIDIA RTX 2060 by default (same GPU the WGL probe's
  context landed on), confirmed by querying its internal device's DXGI
  adapter description and LUID directly rather than assuming it.
- Case A (ANGLE's own device): `eglCreatePbufferFromClientBuffer` →
  `eglBindTexImage` → draw → `glReadPixels` all succeed, and the readback
  matches an independent D3D-side staging-texture readback of the same
  texture pixel-for-pixel (corner `(0,0,128,255)`, center
  `(128,128,128,255)`, both sides agree once BGRA vs. RGBA ordering is
  accounted for) — visually confirmed too, the saved screenshot is a
  clean, correctly-oriented gradient with no artifacts. True zero-copy,
  pixel-verified, same bar Prototype 1 was held to.
- Case B (independent device, *same* adapter as ANGLE) and case C
  (independent device, *different* adapter): both fail identically, at
  the same call, with the same clean error —
  `eglCreatePbufferFromClientBuffer` returns `EGL_NO_SURFACE` with
  `eglGetError() == EGL_BAD_PARAMETER` (`0x300C`). Case B failing exactly
  like case C is the actual finding here: **adapter match is not
  sufficient for this bridge.** The texture must be created on the literal
  `ID3D11Device*` ANGLE itself is using, obtained via
  `EGL_ANGLE_device_d3d` — a stricter requirement than
  `WGL_NV_DX_interop2`'s, which only cares about the adapter. For the real
  provider this means Media Foundation must be handed that *exact* ANGLE
  device (`IMFDXGIDeviceManager::ResetDevice` with it) rather than letting
  Media Foundation create its own D3D11 device, even one on the right
  GPU — otherwise every decoded frame hits this same `EGL_BAD_PARAMETER`
  wall. Like Prototype 1's mismatch case, the failure is immediate and
  unambiguous, not silent — a clean fallback trigger.
- Minor implementation note, not a blocker: no EGL config on this driver
  actually advertises `EGL_BIND_TO_TEXTURE_RGBA` for a D3D11-backed
  pbuffer-capable config, so the probe picks a config without asking for
  that bit and the bridge works anyway — `eglBindTexImage` doesn't appear
  to require the config to have advertised it. Worth re-checking once
  real decode is wired in, but didn't block this probe.

**Still unverified / next:** same as Prototype 1 — only this one NVIDIA +
Intel pairing and driver generation has been tested; AMD and other
pairings are still open. Real `IMFMediaEngine` decode integration was
still outstanding at this point — see Prototype 3 below.

## Prototype 3: real `IMFMediaEngine` decode pushed through both bridges — confirmed

Prototypes 1 and 2 answered "does the interop bridge work" using a
synthetic test texture. This closes the remaining gap: does a frame that
actually came out of Media Foundation's decoder survive the same bridges.
Three files, built in order:

- [`prototype/mf_encode_test_clip.cpp`](prototype/mf_encode_test_clip.cpp)
  — a one-off helper, not part of the decode path under test. Encodes a
  tiny 256×256, 2-second, 30fps H.264-in-MP4 clip (`mf_test_clip.mp4`)
  using Windows' own in-box H.264 encoder MFT via `IMFSinkWriter`, so
  there's something real to decode without downloading any external file
  or shipping a binary asset in the repo. Content is the same `R=x, G=y,
  B=0x80` gradient the earlier probes used, encoded identically on every
  frame (so H.264's inter-frame prediction compresses it to ~12KB for 2
  seconds — expected for unchanging content, not a bug).
  [`mf_decode_sanity_check.cpp`](prototype/mf_decode_sanity_check.cpp) (a
  disposable `IMFSourceReader`-based CPU-readback check, not one of the
  interop prototypes) confirmed the clip decodes cleanly before any GPU
  interop code was added on top.
- [`prototype/mf_decode_wgl_test.cpp`](prototype/mf_decode_wgl_test.cpp)
  (Prototype 3a) — `IMFMediaEngine` decode bound to the WGL context's own
  adapter, `TransferVideoFrame` into a destination texture, then the
  *exact* `WGL_NV_DX_interop2` pipeline from Prototype 1 on that texture
  instead of a synthetic one.
- [`prototype/mf_decode_egl_test.cpp`](prototype/mf_decode_egl_test.cpp)
  (Prototype 3b) — the same decode, but `IMFDXGIDeviceManager::ResetDevice`
  bound to ANGLE's own queried device (per Prototype 2's device-identity
  finding), then the *exact* EGL/ANGLE pipeline from Prototype 2.

**Result: both worked end-to-end on the first real run after the usual
one or two build-time API fixes (see below) — no new interop surprises.**

- Media Foundation setup needed, confirmed by building it rather than
  reading docs: `D3D11_CREATE_DEVICE_VIDEO_SUPPORT` on a device we create
  ourselves (Prototype 3a; not needed/possible for 3b, since ANGLE
  already created its device before we get a handle to it — it worked
  anyway, so this flag is evidently not a hard requirement on this
  driver), `ID3D11Multithread::SetMultithreadProtected(TRUE)` on whichever
  device MF will share with this thread, `MFCreateDXGIDeviceManager` +
  `ResetDevice`, and an `IMFMediaEngineNotify` callback to know when
  `GetReadyState()` clears `MF_MEDIA_ENGINE_READY_HAVE_CURRENT_DATA` and
  when `OnVideoStreamTick` has a frame ready — all standard, documented
  COM usage, exactly the "lower risk than the bridge itself" item the
  spike's "Suggested next steps" predicted.
- Both prototypes decoded the same clip to matching pixel values
  (corner `(5,2,128,255)` RGBA, center `(130,131,130,255)` RGBA — small,
  consistent deviations from the exact `(0,0,128,255)` / `(128,128,128,255)`
  original, which is H.264's lossy compression showing through, not a
  bridge defect) and, in both cases, **the GL-side readback matched the
  independent D3D-side readback of the decoded texture exactly** — the
  interop bridges are lossless; only the codec's own compression
  introduces any difference from the source pattern. Both saved
  screenshots show a clean, correctly-oriented gradient with no bridge
  artifacts.
- This confirms the full chain end-to-end on this hardware: Media
  Foundation decode → GPU texture → zero-copy GL interop → correct pixels
  on screen, for both GL backends Kivy on Windows has to support.

**Still unverified / next:** same hardware-coverage gap as Prototypes 1
and 2 (only this one GPU pairing and driver generation). Also untested at
this point: seeking, and playback over time rather than one transferred
frame — closed out by Prototype 4, next.

## Prototype 4: playback over time + seeking — confirmed

Prototype 3 transferred exactly one frame. A real provider calls
`TransferVideoFrame` on every `on_frame` tick for the life of the video,
and seeking hadn't been touched at all — this prototype exercises both.
Source:
[`prototype/mf_playback_test.cpp`](prototype/mf_playback_test.cpp), fed
by a new encoder,
[`prototype/mf_encode_animated_clip.cpp`](prototype/mf_encode_animated_clip.cpp).
WGL only (Prototype 3 already showed the two bridges are interchangeable
once a frame reaches them — what's under test here is Media Foundation's
own playback/seek behavior and whether one WGL registration survives many
real frames, both bridge-agnostic).

Two things needed solving before this could even run:

- `mf_test_clip.mp4`'s content is identical on every frame (fine for
  transferring one frame, useless for telling frames apart), so a second
  clip was encoded with the same spatial `R=x, G=y` gradient but with the
  previously-constant blue channel repurposed as a per-frame time marker
  (`B(i) = i × 255 / 59`). A single sampled pixel after decode recovers
  which frame was actually delivered, independent of the spatial pattern.
- `IMFMediaEngineNotify`'s numeric event codes (logged but not decoded by
  Prototypes 3a/3b) needed actually checking against
  `MF_MEDIA_ENGINE_EVENT_*` in `mfmediaengine.h` rather than guessed —
  code 19 is `ENDED`, 17 is `SEEKED`, 13 is `PLAYING` (an earlier guess in
  this spike's own drafting mislabeled 13 as a seek-related event; it
  isn't — corrected here, not carried into any conclusion).

**Result: both phases passed clean, on the first run that reached this
far.**

- **Playback:** one WGL registration, done once before `Play()`, was
  locked and unlocked around 59 separate real decoded frames over the
  clip's ~2s with no re-registration — zero anomalies. Every delivered
  frame's recovered time marker matched its expected frame index to
  within ±1–2 (H.264 quantization noise on the marker ramp, not
  skipped/stuck/repeated frames — confirmed by checking that `pts` itself
  never regressed and the marker tracked it monotonically throughout).
  `MF_MEDIA_ENGINE_EVENT_ENDED` fired right on schedule and the loop
  exited on it cleanly. 59 of the clip's 60 frames were observed via
  `OnVideoStreamTick`; the last one wasn't, which reads as normal
  end-of-stream behavior (the engine reached `ENDED` without one final
  tick), not a dropped frame.
- **Seeking:** `SetCurrentTime(1.0)` produced a `SEEKED` event, and the
  next frame delivered afterward had `pts=1.0000s` with a recovered frame
  index of 31 (target: 30, i.e. the same ±1 marker-quantization tolerance
  seen throughout playback) — the seek landed on the actual target, not
  wherever playback had stopped. The saved screenshot is visibly
  different from frame 0's (a different, higher blue tint, as the marker
  design predicts for a mid-clip frame), a cheap independent confirmation
  that it isn't just replaying the start.
- This is a single run, not a stress test: it shows no *first-order*
  resource/state problem across ~60 lock/unlock cycles on one
  registration, not an absence of slow leaks over, say, hours of
  playback — that would need a longer-running variant, not reasoned about
  from this result.

**Still unverified / next:** same hardware-coverage gap as every prior
prototype. Also still open: long-duration playback (this ran ~2 seconds),
pause/resume, and rate changes — none of which this spike's goals treat
as must-haves, so left for whoever implements `video_mediafoundation.pyx`
to exercise against the real provider rather than another throwaway
prototype.

## Risks that need a prototype, not more reading

1. **`EGL_ANGLE_d3d_texture_client_buffer` under `angle_sdl3`. Confirmed,
   see Prototype 2 above** — works end-to-end on ANGLE's own device,
   fails cleanly (not silently) on any other device including one on the
   same adapter. The device-identity requirement (stricter than
   `WGL_NV_DX_interop2`'s adapter-level one) is now a known, load-bearing
   design constraint for the real provider, not an open question.
2. **`WGL_NV_DX_interop2` driver reliability (the `glew`/`sdl3` default
   path). Partially confirmed, see Prototype 1 above** — works cleanly on
   this machine's NVIDIA GPU, fails cleanly (not silently) on its Intel
   GPU and on WARP. Despite the `NV` name the extension is implemented by
   AMD too; search turned up developer reports of it failing on specific
   combinations (including a 2025 report of it failing on a *different*
   current NVIDIA laptop GPU while working on integrated Intel graphics on
   that same machine — the opposite pairing from what was reproduced
   here). The fallback design this points to is now validated, not just
   proposed: treat `wglDXOpenDeviceNV` returning `NULL` as the fallback
   trigger (confirmed to fire reliably on a real mismatch), drop to a
   CPU-copy path (`TransferVideoFrame` into a staging texture →
   `ID3D11DeviceContext::Map` → `Texture.blit_buffer`) with one logged
   diagnostic, same philosophy as `video_avfoundation`'s zero-copy probe.
   Still needs AMD and a second NVIDIA/Intel driver generation to size how
   often the fallback triggers in the wild.
3. **Adapter/device matching, for either bridge. Confirmed as a real,
   reproducible failure mode on this machine, see Prototypes 1 and 2
   above** — the interop registration call itself is the detection point;
   no separate adapter-enumeration/matching logic is even needed, since
   trying the "wrong" adapter (WGL) or the wrong device (EGL/ANGLE) just
   fails cleanly at that call. The two bridges differ in how strict this
   is, though: WGL only cares that the D3D11 device is on the *same
   adapter* as the GL context; ANGLE requires the *exact same device
   object* it's using internally (Prototype 2's case B, same adapter but
   an independent device, failed identically to case C, a different
   adapter). Simplifies the `glew`/`sdl3` provider path: no LUID-matching
   needed up front, just attempt the device the GL context's own adapter
   would naturally give you (or, failing a cheap way to determine that in
   advance, try adapter 0 and fall back on `NULL`). For `angle_sdl3`, the
   provider must fetch ANGLE's own device via `EGL_ANGLE_device_d3d` and
   hand that exact device to Media Foundation — there's no "pick the
   right adapter" option, only "use ANGLE's own device or fail." Still
   worth confirming the *other* direction (integrated GPU driving the GL
   context, discrete GPU for decode) on a machine configured that way,
   since this run only exercised "GL context on the GPU that also won the
   mismatch test."
4. **Codec availability floor.** H.264 decode is in-box on all supported
   Windows versions; HEVC and AV1 may require a free Microsoft Store
   extension on some SKUs/Windows versions. Not a Kivy problem (zero binary
   shipped either way), but it belongs in the capability matrix's footnotes
   so app authors aren't surprised.
5. **`IMFMediaEngine` + HLS.** Couldn't confirm from documentation whether
   `.m3u8` URLs just work via `SetSource`/`SetSourceElements`, or need an
   explicit byte-stream handler registered. Five minutes pointing a real
   `IMFMediaEngine` at a public `.m3u8` test stream settles this
   definitively; don't guess further from search results.

## Real implementation: `video_mediafoundation.pyx` — built and verified

The provider itself is now implemented, not just prototyped:
[`kivy/core/video/video_mediafoundation.pyx`](../../kivy/core/video/video_mediafoundation.pyx)
(Cython, mirrors `video_avfoundation.pyx`'s shape),
[`video_mediafoundation_implem.h`](../../kivy/core/video/video_mediafoundation_implem.h) /
[`.cpp`](../../kivy/core/video/video_mediafoundation_implem.cpp) (the
native side — a genuinely separate compiled translation unit, unlike
avfoundation's textually-`#include`d `.mm`, specifically because
`<windows.h>`/`<d3d11.h>`/`<mfapi.h>` macros would otherwise corrupt
Cython-generated C++ sharing the same unit). Wired into provider
selection (`kivy/core/__init__.py`, `kivy/core/video/__init__.py`) and
`setup.py` (`c_options['use_mediafoundation']`, gated to `win32`).

Verified end-to-end in a real running Kivy app (`kivy.uix.video.Video`,
not just the provider in isolation) on this machine, under both GL
backends:

- **`glew`/`sdl3` (pip default):** true zero-copy via
  `WGL_NV_DX_interop2`, confirmed by the texture's `colorfmt='bgra'`
  (the zero-copy branch's marker) and a correct, correctly-oriented
  on-screen frame.
- **`angle_sdl3`:** falls back to CPU-copy (`colorfmt='rgba'`) — see the
  new finding below — but still renders correctly, confirming the
  fallback path itself (decode → `TransferVideoFrame` → staging texture
  → `Map` → swizzle → `Texture.blit_buffer`) works end-to-end, not just
  on paper.
- `generate_thumbnail()` verified with a 15-call stress test (15/15,
  ~0.45s each) after fixing two real bugs it surfaced inside a running
  app — see "Thumbnail hang" below. (An earlier draft of this section
  blamed a hang on "Media Foundation's own setup" when run from a bare
  script; that was a guess, and the actual cause was the first bug
  below.)

Orientation: both texture branches need `flip_vertical()`, exactly as
`video_avfoundation.pyx` does. The D3D11 data is top-down, and GL/Kivy
treat texture row 0 as the bottom. I got this wrong at first. I removed
the flip after a test against the synthetic gradient clip. But I had
decided that clip's "correct" orientation from my own assumption about
Media Foundation's RGB32 row order, which was circular. Real content
(Big Buck Bunny) showed both thumbnail and playback upside down without
the flip, and upright with it. The interop prototypes are consistent
with this: their GL readback matched D3D row-for-row, which means the
rows were preserved, i.e. upside down in GL's convention. Lesson for
future checks: verify orientation against real footage, not a synthetic
pattern whose expected orientation was itself derived.

### Thumbnail hang: two real bugs, found by tracing rather than guessing

The thumbnail example hung intermittently ("Not Responding", widgets up,
no thumbnail), sometimes on the first run, sometimes on the second. A
temporary step trace in `generateThumbnail()` plus an env switch to
toggle the suspected fix gave a clean A/B:

- **Missing `SetMultithreadProtected(TRUE)` on the thumbnail device.**
  The playback path set it (`finishDeviceSetup()`); the separately
  written thumbnail path didn't. Media Foundation's worker threads use
  the shared device's immediate context concurrently with the calling
  thread, so without it the call raced. Fix off: hung on the first call,
  stuck between `HAVE_CURRENT_DATA` and the first `OnVideoStreamTick`
  frame. Fix on: 15/15 consecutive thumbnails, ~0.45s each.
- **Unbalanced `CoUninitialize()`.** The trace showed
  `CoInitializeEx(COINIT_MULTITHREADED)` returning `RPC_E_CHANGED_MODE`.
  SDL3 had already made Kivy's main thread an STA. But
  `mf_runtime_release()` called `CoUninitialize()` anyway, removing one
  of SDL3's own initializations per thumbnail. After three thumbnails,
  COM was fully torn down under SDL3 and the next call re-initialized
  the UI thread as MTA. Fixed by requesting `COINIT_APARTMENTTHREADED`
  (matching the thread, `S_FALSE`) and only uninitializing after a
  successful init. This affected the playback path too, since it shares
  the helper.

The polling waits also pump the thread's message queue now
(`PumpAndSleep`), which an STA should do while blocking anyway. That was
added first as a guess and is not what fixed the hang.

### New finding: `angle_sdl3`'s device isn't reachable via any supported API on this SDL3 build

Prototypes 2 and 3b never needed to connect to the application's
*already-running* ANGLE instance — their whole point was proving the
interop bridge, so they created a second, independent ANGLE EGL display
for themselves and used it entirely self-contained. Wiring the real
provider needs the opposite: Kivy's own GL/EGL context is already
current and already rendering, so the provider must reach *that* exact
in-use ANGLE device (not a fresh one — a second independent display
would be a different, mismatched device, exactly the failure mode
Prototype 2 confirmed). That connection turned out not to exist in this
build:

- There is no separately loadable `libEGL.dll` module in the real Kivy
  process to find by name (confirmed by enumerating every loaded module
  and checking for one exporting `eglGetCurrentDisplay` — none did).
  ANGLE is evidently statically linked into `SDL3.dll` itself in this
  `kivy_deps.sdl3==0.1.0` build, with its own `eglXxx` symbols kept
  private.
- SDL3 does export `SDL_EGL_GetCurrentDisplay` and
  `SDL_EGL_GetProcAddress` from `SDL3.dll` — the architecturally correct
  way to ask SDL for its own handles rather than guessing module names —
  but `SDL_EGL_GetCurrentDisplay()` returns `NULL` on this build
  regardless of when it's called, including from inside an actual
  render `Callback` with a GL context definitively current (ruling out
  a timing issue). Neither function is even declared in this build's
  own public `SDL3/*.h` headers, suggesting they're present in the
  binary for other platforms but non-functional for this Windows/ANGLE
  configuration specifically.

The provider's response is the honest one: `video_mediafoundation_implem.cpp`
tries the SDL3 accessors first, and if ANGLE's device can't be obtained,
falls back to an independent D3D11 device for Media Foundation to decode
into and CPU-copy delivery for every frame — real, correct playback
under `angle_sdl3`, just without zero-copy. This is a build/platform
limitation worth revisiting (a newer `kivy_deps.sdl3` might expose a
working accessor, or SDL3 might add one), not an architecture problem —
the `EGL_ANGLE_d3d_texture_client_buffer` bridge itself is already proven
correct by Prototypes 2 and 3b; what's missing is only a supported way to
ask this specific SDL3 build for the device it's already using.

## Suggested next steps

1. **Find a working way to obtain `angle_sdl3`'s live ANGLE device** —
   check whether a newer `kivy_deps.sdl3` build exposes a functional
   `SDL_EGL_GetCurrentDisplay`, or raise it with SDL3 upstream. Until
   then, `angle_sdl3` genuinely works (verified above), just via
   CPU-copy rather than zero-copy.
2. **Re-run all prototypes on AMD / a second NVIDIA-Intel pairing** if
   access to that hardware shows up, to size how often the CPU-copy
   fallback actually triggers in the wild — this machine only tested one
   specific GPU pairing and driver generation. (An Intel-only laptop is
   lined up for this — see `run_all_prototypes.ps1`.)
3. **Resolve the remaining paper-only risks** (4 and 5 above: codec
   extension floor, `IMFMediaEngine` + HLS) — these are quick, concrete
   checks against a real `IMFMediaEngine` instance, not architectural
   unknowns.

None of these are the same order of risk the prototypes and the real
implementation already retired — they're confirmation and completeness
work, not open unknowns about whether the architecture works at all.
