# Drafts: two GStreamer 1.28.7 Windows reports

Posted on 2026-11-23: report 1 as https://gitlab.freedesktop.org/gstreamer/gstreamer/-/work_items/5459 and report 2 as
https://gitlab.freedesktop.org/gstreamer/gstreamer/-/work_items/5460. `src/repro_gst.py` is included in both.
Both reproduce without Kivy, using stock `playbin3` through ctypes.

---

## 1. d3d12h264dec: "Couldn't allocate output buffer" after a seek, when downstream wants system-memory NV12

**Labels:** d3d12

### Summary

`d3d12h264dec` fails with a flow error about 4 s after a flushing seek in a
multi-variant HLS stream:

```
gst_d3d12_decoder_process_output: Couldn't allocate output buffer
```

It only fails when downstream asks for system-memory video
(`video/x-raw,format=NV12`). The same seek works with D3D12 memory
downstream: the default video sink, or a plain `fakesink`.

### Environment

* Windows 11 Home, build 26200.
* GStreamer 1.28.7, MSVC x86_64, from the `gstreamer-libs`,
  `gstreamer-plugins`, `gstreamer-plugins-libs` and
  `gstreamer-plugins-restricted` 1.28.7 wheels on PyPI.
* The decoder runs on D3D12 adapter 0: NVIDIA GeForce RTX 2060
  (0x10de:0x1f08), driver 32.0.16.1060. The machine also has Intel UHD
  630 (adapter 1).

### Steps

```
python repro_gst.py https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8 "video-sink=\"capsfilter caps=\\\"video/x-raw,format=NV12\\\" ! fakesink sync=true\""
```

* `repro_gst.py` (attached) runs `playbin3 uri=... <properties>`. It plays
  8 s, pauses 3 s, resumes 12 s, then seeks to 50 % with FLUSH | KEY_UNIT
  and plays 15 s. It prints the position and any bus error.
* `GST_PLUGIN_FEATURE_RANK=wasapi2sink:NONE` was set for every run, to keep
  report 2 out of the way.
* Probably equivalent, but not tried, since the wheels don't ship gst-play:

  ```
  gst-play-1.0 --videosink="capsfilter caps=video/x-raw,format=NV12 ! fakesink sync=true" https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8
  ```

  then seek forward several minutes.

### Expected

Playback continues after the seek, as it does before it.

### Actual

About 4 s after the seek:

```
WARN  h264decoder gsth264decoder.c:983:gst_h264_decoder_handle_frame_num_gap:<d3d12h264dec0> Invalid frame num 1, maybe frame drop
WARN  h264decoder gsth264picture.c:405:gst_h264_dpb_get_short_ref_by_pic_num: No short term reference picture for 0
WARN  h264decoder gsth264decoder.c:2005:gst_h264_decoder_handle_memory_management_opt:<d3d12h264dec0> memory management operation type 1 failed
WARN  videodecoder gstvideodecoder.c:3187:gst_video_decoder_prepare_finish_frame:<d3d12h264dec0> decreasing timestamp (0:06:21.983322222 < 0:06:22.083333333)
ERROR d3d12decoder gstd3d12decoder.cpp:1628:gst_d3d12_decoder_process_output:<d3d12h264dec0> Couldn't allocate output buffer
WARN  d3d12decoder gstd3d12decoder.cpp:1705:gst_d3d12_decoder_output_loop:<d3d12decoder0> Last flow was error
WARN  adaptivedemux2 gstadaptivedemux.c:3732:gst_adaptive_demux_output_loop:<hlsdemux2-0> error: Internal data stream error.
WARN  adaptivedemux2 gstadaptivedemux.c:3732:gst_adaptive_demux_output_loop:<hlsdemux2-0> error: streaming stopped, reason error (-5)
```

The frame-number gaps and the decreasing timestamp come as hlsdemux2
switches variant after the seek.

### What changes the result

| Video sink (`playbin3 video-sink=`) | Decoder | Seek |
|---|---|---|
| default (d3d12videosink) | d3d12h264dec | ok, 2 runs |
| `fakesink sync=true` | d3d12h264dec | ok |
| `capsfilter caps=video/x-raw,format=NV12 ! fakesink sync=true` | d3d12h264dec | **fails** |
| `appsink caps=video/x-raw,format=NV12 max-buffers=1 drop=true sync=true` | d3d12h264dec | **fails** |
| `glupload ! glcolorconvert ! appsink` (GLMemory RGBA; seen in Kivy's pipeline, not the stock script) | d3d12h264dec | **fails** (same error) |
| default | d3d11h264dec (`d3d12h264dec:NONE`) | ok: one `No available codec frame with frame number N` error, then playback continues |

So the failure seems to be in the decoder's system-memory output path,
probably its download pool, when it is renegotiated or flushed across the
seek and the variant switch.

---

## 2. wasapi2sink: playbin3 stalls after resume or seek on HLS with a separate audio rendition

**Labels:** wasapi2

### Summary

Apple's advanced fMP4 HLS example has its audio as a separate rendition
(`EXT-X-MEDIA TYPE=AUDIO`), and subtitles too. With `wasapi2sink` as the
audio sink:
* `playbin3` plays the stream.
* After PAUSED → PLAYING, it never advances again.
* After a flushing seek, it doesn't advance either.

There's no error and no EOS. The position just freezes. The same pipeline
with `wasapisink`, or with audio sent to a `fakesink`, plays through
pause, resume and seek.

### Environment

As in report 1. `wasapi2sink` is what `autoaudiosink` picks by default.
The default output device is Realtek Audio.

### Steps

```
python repro_gst.py https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8
```

### Expected

Playback resumes after unpausing, and continues after the seek.

### Actual

```
REPRO play ended at 5.0
REPRO pause ended at 5.0
REPRO STALL during resume at 5.0
REPRO resume ended at 5.0 STALLED
REPRO seek to 300.0
REPRO STALL during after seek at 294.0
REPRO after seek ended at 294.0 STALLED
```

### What changes the result

| Variation | Pause → resume | Seek |
|---|---|---|
| default (`autoaudiosink` → wasapi2sink) | **stalls** | **stalls** |
| `audio-sink="fakesink sync=true"` | ok | ok |
| `GST_PLUGIN_FEATURE_RANK=wasapi2sink:NONE` (→ wasapisink) | ok | ok |
| the video-only variant `v5/prog_index.m3u8` (no audio; Kivy's pipeline) | ok | ok |
| our own HLS with AAC muxed into each segment, with wasapi2sink (Kivy's pipeline) | ok | ok |

So it needs both wasapi2sink and an audio stream that comes from its own
rendition (its own adaptive-demux output and download timing). A related
symptom, seen in Kivy's pipeline: with playbin3's `text` flag off, the
same stream didn't start at all with wasapi2sink.

---

## Notes for filing

* Attach `src/repro_gst.py`. It only needs the PyPI wheels (`pip install
  gstreamer-libs gstreamer-plugins gstreamer-plugins-libs
  gstreamer-plugins-restricted`) and the stream URL.
* Both public streams are long-standing test streams (Mux, Apple), so the
  maintainers can reproduce them.
* Kivy's side:
  * Report 1 hits every Kivy user on Windows who seeks in such a stream,
    because Kivy's sinks always ask for NV12 or GLMemory.
  * The workaround would be ranking `d3d11h264dec` above `d3d12h264dec` in
    the provider's Windows policy. Note that one `d3d11h264dec` run also
    stalled once after resume, with wasapisink. It's unconfirmed, so check
    before relying on d3d11.
