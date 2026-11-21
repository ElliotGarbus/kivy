# Draft upstream reports for GStreamer (not posted)

Found while spiking Kivy's GStreamer provider (`GSTREAMER-CORE-SPIKES.md`).
For https://gitlab.freedesktop.org/gstreamer/gstreamer/-/issues. Each has a
PyGObject reproducer so it doesn't depend on Kivy. Posting needs the user's
go-ahead.

---

## 1. Windows wheels 1.28.7: libsoup assertion aborts the process when an HLS pipeline with a missing demuxer is set to NULL

**Environment:** Windows 11, Python 3.12, official PyPI wheels 1.28.7
(`gstreamer-bundle` set, or the four wheels `gstreamer-libs`, `-plugins`,
`-plugins-libs`, `-ext-runtime`). Not reproduced on Linux with 1.26.2
(Pi OS Trixie) or 1.28.2 (Ubuntu 26.04) using the same rank override.

**Steps** (reduced from the reproducer that was run,
`gstreamer-spike/gst_headless.py --case hls-ts` with
`GST_PLUGIN_FEATURE_RANK=tsdemux:NONE`, which polls the bus at 60 Hz and
uses an `appsink`; run this reduced form once before posting):

```python
import os, time
os.environ['GST_PLUGIN_FEATURE_RANK'] = 'tsdemux:NONE'   # or don't install -restricted
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
p = Gst.ElementFactory.make('playbin3')
p.set_property('uri', 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8')
p.set_property('video-sink', Gst.ElementFactory.make('fakesink'))
p.set_state(Gst.State.PLAYING)
bus = p.get_bus()
msg = bus.timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.ERROR)
print(msg.parse_error()[0].message)   # missing-plugin / no suitable demuxer
time.sleep(0.2)
p.set_state(Gst.State.NULL)            # aborts here
```

**Result:** the process aborts:

```
libsoup:ERROR:../libsoup/soup-session.c:1529:message_completed:
assertion failed: (item->context == soup_thread_default_context ())
Bail out! ...
```

3 of 3 runs. Setting READY first, or waiting 1 s before NULL, doesn't help.
Tearing down HLS/DASH pipelines while they download normally, or after a
404 or a server going away, doesn't trigger it. It seems specific to
hlsdemux2's download helper having requests in flight when the pipeline
errors before a demuxer is linked.

**Expected:** a clean shutdown, as on Linux.

---

## 2. Windows wheels 1.28.7: playbin3 never starts a stream that has a subtitle track when the text flag is off (wasapi2sink)

**Environment:** as above.

**Steps:** `playbin3` with flags `video | audio | soft-volume | buffering |
deinterlace` (no `text`), an `appsink` video sink (`sync=true`), default
audio sink, URI Apple's fMP4 HLS example
`https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8`
(video, audio and a subtitle track). Set PLAYING.

**Result:** reaches PLAYING, buffering reaches 100%, but position stays at 0
and no sample reaches the sink, with no error, indefinitely.
STREAMS_SELECTED lists audio, **text** and video even though `text` is off.

| Variation | Result |
|---|---|
| default flags (text on, playsink's textoverlay) | plays |
| text off, `audio-sink=directsoundsink` | plays |
| text off, `audio-sink=fakesink sync=true` | plays |
| text off, video only (no `audio` flag) | plays |
| text on, `text-sink=fakesink sync=false` | stalls |
| text off, `select-streams` event with only the audio and video ids, sent on STREAM_COLLECTION | stalls; STREAMS_SELECTED still lists text |

Plays on Linux (1.26.2, 1.28.2) with text off.

**Expected:** with the text flag off, playbin3 doesn't select (or doesn't
wait on) the text stream; and a `select-streams` event without it is
honoured.

---

## 3. souphttpsrc with libsoup 3: transport errors are not posted, only "Internal data stream error"

**Environment:** any platform with libsoup 3 (Windows wheels 1.28.7, Pi OS
1.26.2, Ubuntu 1.28.2).

**Steps:** play `https://no-such-host.invalid/video.mp4`,
`http://127.0.0.1:9/x.mp4` (refused), `https://expired.badssl.com/` or
`https://self-signed.badssl.com/`.

**Result:** the only ERROR from `souphttpsrc` is
`GST_STREAM_ERROR_FAILED` "Internal data stream error." (from basesrc,
"streaming stopped, reason error (-5)"). In `_session_send_cb` /
`gst_soup_http_src_send_message` (gstsouphttpsrc.c, 1.28) the `GError` from
`soup_session_send_finish()` is only logged at DEBUG ("Sending message
failed: ...", "Didn't get an input stream: ..."). The libsoup 2 path posted
`GST_RESOURCE_ERROR_NOT_FOUND` "Could not resolve server name.",
`OPEN_READ` "Could not establish connection to server." and "Secure
connection setup failed.".

**Expected:** post a resource error with the `GError` message (or map
`G_RESOLVER_ERROR` / `G_IO_ERROR_CONNECTION_REFUSED` / `G_TLS_ERROR` to the
old texts), so applications can tell the user what went wrong.
