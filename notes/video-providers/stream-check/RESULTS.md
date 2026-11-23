# Network streams on every Kivy 3.0 video provider

Run on 2026-11-22 and 2026-11-23 against Kivy at `remove-ffpyplayer`
(8d2c3bbd0), the branch with the GStreamer rewrite (#9540, #9542) and
ffpyplayer removed (#9546).

## Setup

* **Server:** Raspberry Pi 5, `server/streams.py`.
  * RTSP from gst-rtsp-server: `/h264` is 720p30 H.264 with PCMA audio,
    `/h265` is 540p30 8-bit H.265.
  * HLS from ffmpeg: live MPEG-TS, live fMP4, and recorded (45.6 s).
  * Recorded DASH (45.6 s).
  * MJPEG, served multipart (640x360, 15 fps).
  * A control port that drops RTSP or HLS.
* **Public streams:** Mux `x36xhzz` (HLS, TS, multi-variant) and Apple
  bipbop advanced (HLS, fMP4, multi-variant with alternate audio and
  subtitles).
* **Test app:** `src/main.py`. Steps per stream: load, fps over 8 s, pause,
  resume, seek to 50 %, stop then play, unload. Error cases record how long
  `on_error` takes. Drop cases play, drop the server, then wait up to 60 s.
* **Devices:**
  * Windows 11: Media Foundation. GStreamer 1.28.7 from the wheels
    (`gstreamer-libs`, `-plugins`, `-plugins-libs`), first without and then
    with `gstreamer-plugins-restricted`.
  * Pixel 8a (Android 16): ExoPlayer, Media3 1.11.1 with the hls, dash and
    rtsp modules.
  * Pi 5: GStreamer 1.26.2 from Pi OS, Kivy 3 built from this tree, on the
    Wayland desktop (labwc). The Pi plays its own streams.

## Matrix

Legend: **plays** means every step passed. **err** means it didn't play, but
`on_error` fired cleanly.

| Stream | Media Foundation | GStreamer Win, no restricted | GStreamer Win + restricted | GStreamer Pi 5 | ExoPlayer Pixel 8a |
|---|---|---|---|---|---|
| RTSP H.264, UDP | plays (load 5.7 s) | plays | plays (d3d12h264dec, 2.3 s) | plays (avdec_h264, 2.6 s) | plays, but stop then play doesn't resume |
| RTSP H.264, TCP (`rtspt://`) | plays | plays | plays | plays | plays (Media3 handles `rtspt`), but stop then play doesn't resume |
| RTSP H.265 | err 0xC00D2EE6 [1] | plays (d3d12h265dec) | plays (d3d12h265dec) | **segfault on stop** [2] | err: Exynos HEVC decoder failed [3] |
| HLS live, MPEG-TS | err 0xC00D36C4 | **process abort** [4] | plays | plays | plays |
| HLS live, fMP4 | err | plays | plays | plays | plays, but stop then play doesn't resume [5] |
| HLS recorded (TS) | err | **process abort** [4] | plays | plays | plays |
| DASH recorded | err | plays | plays | plays | plays |
| MJPEG over HTTP | err | plays | plays (10.5 fps) | plays (10 fps) | err: no extractor |
| Mux HLS (TS) | err | **process abort** [4] | plays, then stalls after seek [6] | plays | plays |
| Apple HLS (fMP4) | err | stalls after resume [6] | stalls after resume [6] | plays | plays |
| RTSP 404 | `on_error` 0.6 s | 0.19 s | 0.3 s | 0.09 s | 0.5 s |
| RTSP refused port | `on_error` 21 s | 2.3 s | 2.2 s | 0.05 s | 0.13 s |
| HLS 404 | `on_error` 1.1 s | 0.23 s | 0.1 s | 0.09 s | 3.3 s |
| RTSP server drop | `on_error` 0.1 s | `on_error` 28 s | `eos` 28 s [7] | `eos` 27 s [7] | `eos` 8.5 s [7] |
| HLS server drop | err (HLS unsupported) | **process abort** [4] | `on_error` 0.2 s | `on_error` 1.7 s | `on_error` 6.9 s |

**[1] Media Foundation can't play H.265 over RTSP, though it has the
decoder.**
* The HEVC Video Extensions are installed, and a local H.265 MP4 plays
  through the same provider in 1.2 s.
* So the failure is in Windows' RTSP source (H.265 RTP depacketizing), not a
  missing codec.

**[2] Pi 5: a segfault inside `GstPlayer.seek()`, during `stop()`.**
* It reproduces every time.
* The crashing pipeline is live RTSP H.265 decoded in hardware
  (`v4l2slh265dec`), with DMABuf frames on the GL path.
* faulthandler stack: `Video.on_state('stop')` → `VideoGstplayer.stop()` →
  `_request_seek(0.0)` → `_issue_seek` → `player.seek()`, which crashes in C.
* Pause, resume and seek all passed just before the crash. The difference at
  stop is that the provider sets PAUSED and seeks to 0 straight away on a
  live, no-preroll pipeline.
* Live RTSP H.264, decoded in software on the nv12 path, survives the same
  stop.
* Not yet known: whether the cause is v4l2codecs flushing, the GL/DMABuf
  path, or seeking a live source at all. A native backtrace (gdb on the Pi)
  is the next step.
* Skipping the seek for live pipelines (`self._live`) is probably right
  anyway. It may also avoid the crash.

**[3] Pixel 8a: `ERROR_CODE_DECODING_FAILED` from `c2.exynos.hevc.decoder`.**
* The format is `hvc1.1.6.L90`, 8-bit 4:2:0, 960x540, and the device
  reports it as supported (`format_supported=YES`).
* The same stream plays with GStreamer on Windows (d3d12h265dec) and on the
  Pi (v4l2slh265dec). So the stream is valid.
* Possible causes: x265's `tune=zerolatency` output, or Media3's H.265 RTP
  reader. A real H.265 camera would settle it.

**[4] GStreamer without the restricted plugins: the process aborts on any
HLS stream with MPEG-TS segments.**
* The provider first logs the right error: "missing GStreamer plugin:
  MPEG-2 Transport Stream demuxer".
* Then, before `on_error` reaches the app, libsoup aborts:
  `soup-session.c:1529:message_completed: assertion failed: (item->context
  == soup_thread_default_context ())`.
* It reproduced on live TS, recorded TS, Mux, and the HLS drop case, each in
  its own process.
* It happens while the failed pipeline is torn down: an HLS download thread
  finishing a message on a different main context.
* fMP4 HLS and DASH play without the restricted plugins, because no TS
  demuxer is needed.
* This matters: an app that ships only the LGPL wheels crashes on the most
  common HLS format, instead of getting `on_error`.

**[5] ExoPlayer provider: stop then play doesn't resume live streams.**
* It failed on RTSP UDP, RTSP TCP and live fMP4 HLS. Live MPEG-TS HLS
  recovered.
* `stop()` does `pause()` then `seekTo(0)`. On a live window, position 0 is
  outside the live window, and ExoPlayer delivers nothing more. Logcat shows
  no error.
* Likely fix: use `seekToDefaultPosition()`, which goes to the live edge,
  when `isCurrentMediaItemLive()`.

**[6] GStreamer 1.28.7 on Windows: multi-variant public HLS stalls after
pause or seek. Cause: `wasapi2sink` (Apple) and `d3d12h264dec` (Mux).**
* The provider asks for PLAYING and the buffer reads 100 %, but no frames
  arrive and the position stays fixed. `src/probe_buffer.py` logs this
  every 0.5 s.
* It happens on both the GL and nv12 paths. So it's not the buffering hold
  or the GL texture path.
* Our own single-variant HLS passes every step on the same build.
* The same two public streams pass every step on the Pi: GStreamer 1.26.2,
  avdec_h264, nv12 path.
* **Narrowed:** these are two separate GStreamer 1.28.7 element bugs. Neither
  is Kivy's fault, and neither is known upstream yet. Each test was a
  `probe_buffer.py` run: play 8 s, pause 3 s, resume 12 s, seek to 50 %,
  then 15 s more.
* **Apple (fMP4 with alternate audio and subtitle renditions): `wasapi2sink`.**
  The wasapi2sink audio sink is the default in the 1.28.7 wheels.

  | Variation | Result |
  |---|---|
  | Default | Stalls after resume and after seek |
  | Audio off (`audio=False` on `GstPlayer`) | Pause, resume and seek all play |
  | Video-only variant (`v5/prog_index.m3u8`) | Pause, resume and seek all play |
  | `GST_PLUGIN_FEATURE_RANK=wasapi2sink:NONE`, so autoaudiosink picks `wasapisink` | Pause, resume and seek all play |

  Our own HLS (muxed AAC) resumes fine with wasapi2sink. So the trigger is
  this stream's separate audio rendition and wasapi2sink together. The
  `PLAY_TEXT` comment in `_gstplayer.pyx` already records wasapi2sink
  failing to start this same stream.
* **Mux (TS, multi-variant): `d3d12h264dec`.**
  * About 4 s after the seek, the decoder logs
    `gst_d3d12_decoder_process_output: Couldn't allocate output buffer`.
  * Just before that come frame-number gaps and "decreasing timestamp", as
    the variant switches.
  * The flow error goes up to hlsdemux2 ("Internal data stream error,
    reason error (-5)"). The provider reports `on_error`, and then the
    process hits the libsoup abort from finding [4].
  * The nv12 path fails the same way, so it isn't the GL path holding
    buffers.
  * With `d3d12h264dec:NONE`, playbin picks `d3d11h264dec`. It logs one
    "No available codec frame" error, then recovers and keeps playing.
  * Stock `playbin3` without Kivy (`src/repro_gst.py`): the seek works with
    the default d3d12videosink or a plain `fakesink`. It fails with
    `capsfilter caps=video/x-raw,format=NV12 ! fakesink` or an NV12
    `appsink`. So the bug is d3d12h264dec's system-memory output path, which
    Kivy's sinks always use.
  * The stock repro reproduces both bugs without Kivy. Reported upstream on
    2026-11-23: d3d12h264dec as [gstreamer#5459](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/work_items/5459), wasapi2sink as
    [gstreamer#5460](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/work_items/5460). The text is in `UPSTREAM-REPORTS-DRAFT.md`.
  * Both still reproduce on the 1.28.9 wheels (retested 2026-11-23):
    d3d12h264dec 3 of 3 runs, wasapi2sink 2 of 2 runs. Kivy #9550 pins
    the Windows wheels to 1.28.9.
  * gstreamer!12607 (merged to main 2026-10-24) fixes the d3d12h264dec
    bug. It's not in 1.28.9. I cherry-picked its 11 commits onto 1.28.9,
    built gstcodecs, gstdxva, gstd3d12 and the d3d12 plugin, and swapped
    them into the wheels. The control build of plain 1.28.9 failed 3 of 3
    runs. The patched build played through the seek 3 of 3, also through
    Kivy's GL path. No backport requested: Kivy 3.0 has no ship date yet.
* **Kivy-side mitigation options** (not done):
  * Rank `d3d11h264dec` above `d3d12h264dec`, and `wasapisink` above
    `wasapi2sink`, in the provider's Windows policy.
  * Or leave the defaults, report both upstream, and document the issue.
  * The d3d11 decoder works on the GL path too (`render path gl, decoder
    d3d11h264dec`).

**[7] A dropped RTSP server ends with `eos`, not `on_error`.**
* This happens on ExoPlayer, and on GStreamer depending on timing (about
  28 s, once each way on Windows).
* Media Foundation fires `on_error` within 0.1 s.
* For the docs: for a live stream, apps should treat `eos` as "the stream
  ended" and reconnect.

## For the docs ("Network streams")

* **Windows, Media Foundation:**
  * RTSP H.264 over UDP and TCP (`rtspt://`) plays.
  * Not supported: RTSP H.265, HLS, DASH, MJPEG. Use GStreamer for these.
* **Windows, GStreamer wheels:**
  * Plays everything tested.
  * HLS with MPEG-TS segments, which is most HLS, needs
    `gstreamer-plugins-restricted`. Until [4] is fixed, it crashes without
    them.
* **Linux and Raspberry Pi, distro GStreamer:** plays everything tested.
  Pi 5 hardware H.265 decoding on a live stream crashes on stop [2].
* **Android, ExoPlayer:**
  * HLS (TS and fMP4), DASH, and RTSP H.264 over UDP and TCP play, once the
    app adds `media3-exoplayer-hls`, `-dash` and `-rtsp`.
  * Cleartext `http://` needs `android:usesCleartextTraffic`.
  * MJPEG isn't supported.
* **Everywhere:** a dropped live stream may end with `eos` rather than
  `on_error`.
* **Not tested:** macOS and iOS (AVFoundation). They aren't in the matrix
  for lack of a device.

## Files

* `results/windows-mediafoundation.log`
* `results/windows-gstreamer-restricted.log`
* `results/windows-gstreamer.log` (no restricted; aborted on case 4)
* `results/windows-gstreamer-case-*.log` (no restricted, one case per
  process)
* `results/android-56051JEKB16396/` (`scheck.txt`, `logcat.txt`)
* Pi logs stay on the Pi, under `~/stream-check-run/` (`pi-gstreamer.log`,
  `case-*.log`). The numbers above come from them.
* `pi/`: the scripts that send the tree to the Pi, build it, and run there.
  Run them from `build/stream-check/` in the Kivy worktree.
