# Audio plan: Windows machine (Windows, Linux, Raspberry Pi, Android)

Executed on the Windows machine by a Cursor agent, with the user steering.
Covers the `mediafoundation` (new), `gstplayer` and `android` providers, and
checks the shared `sdl3` work on Windows, Linux, the Pi and Android. The Mac
does the shared Python and `sdl3` changes and the Apple provider
(`PLAN-MAC.md`). Read `CONTRACTS.md` before any step; it fixes the API both
machines implement, so this plan doesn't wait for the Mac's PRs to merge.

## Rules

- Every PR branch starts from `upstream/master`, is pushed to `origin` (the
  fork, `ElliotGarbus/kivy`) and opened against `kivy/kivy`. Notes never go
  into an upstream PR; they live on `audio-provider-notes`.
- Code that needs the shared base class (`SoundLoadError`, `_do_eos`,
  `_do_error`, `pause`/`resume`, `schemes()`) is tested by merging
  `origin/audio-sound-contracts` (Mac step S1) into a local test branch.
  The PR branch itself stays on `upstream/master` and is rebased once S1
  merges. Don't open a provider PR for review before S1 merges; open it as
  a draft.
- Don't edit the shared files the Mac owns (`kivy/core/audio_output/__init__.py`,
  `audio_sdl3.pyx`, `test_audio_contracts.py`) except to register a new
  provider (W3) or remove your provider's `_KNOWN_FAILURES` entries
  (`test_audio_behavior.py`). If a shared change is needed, describe it in
  `README.md` under "Hand-offs" for the Mac.
- No AI attribution in commits, PR titles, descriptions or comments.
- When a step finishes, update its row in `README.md` ("Status") on
  `audio-provider-notes`, then `git pull --rebase` and push. Edit only rows
  this machine owns.

## Environment

The same machines and setups as the video provider work. Their notes are on
the `video-provider-notes` branch, `notes/video-providers/`:

- **Windows 11**: Kivy built from source in a worktree (as for #9525 and
  #9546). GStreamer from the official 1.28.9 wheels (`kivy[gstreamer]`;
  `gstreamer-pack/README.md`).
- **Linux**: WSL2 Ubuntu 26.04 with WSLg (audio through WSLg's PulseAudio),
  distro GStreamer (`GSTREAMER-CORE-SPIKES.md`). The XPS 9320 Linux laptop
  for anything WSL can't show.
- **Raspberry Pi 5**: Pi OS Trixie, labwc, reached over SSH
  (`GSTREAMER-SPIKE.md`). Use the HDMI or headphone output and check by
  ear where a step says so.
- **Android**: kivyforge from Git Bash, `adb` at
  `/c/Android/Sdk/platform-tools/adb.exe`, the Pixel 8a (and the device
  `56051JEKB16396` used in `stream-check`). The check-app pattern is
  `android-provider-check/`: `pyproject.toml`, `make_wheels.py` (overlays
  a branch's Python files on the published Kivy 3 Android wheels) and
  `run_check.sh`. `make_wheels.py` is only valid for branches that change
  no compiled code; for `.pyx` changes, build real wheels with kivyforge's
  `docs/guides/guides/cross-platform/kivy-dev-build.md`, as the Media3
  video plan did.
- Fixtures and the probe come from the Mac (step M0):
  `notes/audio-providers/fixtures/` and `tools/format_probe.py`. Pull
  `audio-provider-notes` first.

## Order

| Step | What | Branch | Needs | Unblocks |
|---|---|---|---|---|
| W1 | What SDL3 plays on each platform; O2 measurement | `audio-provider-notes` (results only) | M0 | M1 merge |
| W-SP1 | Spike: when each provider notices a bad file (Windows, Linux, Pi, Android) | `audio-provider-notes` (results only) | M0 | S1, S2 |
| W2 | Check the Mac's phase 1 branch on 4 platforms | none (results only) | M1 pushed | M1 merge |
| SP5 | Spike: Media Foundation audio-only engine | scratch only | nothing | W3 |
| W3 | Phase 3: `mediafoundation` audio provider | `audio-mediafoundation` | SP5, S1 (local merge) | W6 |
| SP6 | Spike: GStreamer gapless loop | scratch only | nothing | W4a |
| W4a | `gstplayer` meets the contracts | `audio-gstplayer-contract` | SP6, S1 (local merge), S2 | W6 |
| W4b | `android` (`MediaPlayer`) meets the contracts | `audio-android-contract` | S1 (local merge), S2 | W5 |
| W4c | Check the Mac's `sdl3` contract branch on 4 platforms | none (results only) | S4 pushed | S4 merge |
| SP7 | Spike: Media3 load wait across threads | scratch only | nothing | W5 |
| W5 | Phase 5: Media3 in the `android` provider | `audio-android-media3` | SP7, W4b | W6 |
| W6 | Phase 6: streaming through `mediafoundation`, `gstplayer`, `android` | one branch per provider | W3, W4a, W5 | none |

SP5, SP6 and SP7 can start right away. W1 and W-SP1 start as soon as M0 is
pushed; run them together, they use the same probe and fixtures. W3 starts
after SP5, against `CONTRACTS.md`; it needs S1 only to run.

## Spikes

A spike answers one question with throwaway code, inside a time box, and
writes its answer to `results/spikes/<name>.md`: the question, what was
run, the numbers, the answer, and what it changes in `CONTRACTS.md` or a
plan step. A contract change goes to `README.md` ("Hand-offs") for the
Mac, which owns `CONTRACTS.md`, before the dependent step starts. Spike
code lives under `notes/audio-providers/spikes/<name>/` if worth keeping.

### W-SP1. When does a provider notice a bad file? (Windows half of SP1)

Time box: one day, run with W1. Needs M0's fixtures (`notaudio.<ext>`:
random bytes; `damaged.<ext>`: a valid header, then random bytes).

C2 assumes `load()` raises for a file that can't be played, but `sdl3`
decodes during playback (`predecode=False`) and other frameworks may only
read the header when opening (O4).

1. With `format_probe.py`, for each extension and each provider that
   claims it, record: does `load()` fail; if not, what `length` says and
   what `play()` does (error, early stop, noise, silence for the full
   length). Providers: `sdl3` on Windows, WSL2, the Pi and Android;
   `gstplayer` on Windows and WSL2; `android` (`MediaPlayer`) on Android.
2. `gstplayer` returns before GStreamer opens the file today: record when
   the error arrives (milliseconds after `load()`) for both kinds.
3. Write `results/spikes/W-SP1.md` and add a hand-off for the Mac, which
   combines it with SP1 to settle O4.

### SP5. Media Foundation audio-only engine

Time box: two days. Answers what W3's design assumes.

In a scratch C++ console program (then a minimal Cython wrapper inside a
Kivy app, so SDL's window and COM setup are present):

1. Create `IMFMediaEngine` with `MF_MEDIA_ENGINE_AUDIOONLY`, no DXGI
   manager, no window. Does it play `short1.m4a`, `.aac`, `.wma`, `.mp3`?
2. COM: SDL may initialize COM on Kivy's thread before the provider does.
   Check `CoInitializeEx` results (`S_OK`, `S_FALSE`,
   `RPC_E_CHANGED_MODE`) inside a running Kivy app, and that the video
   provider's approach (around line 161 of
   `video_mediafoundation_implem.cpp`) works for audio as is.
3. Time from `SetSource` to `MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA` for
   local files (cold and warm), and to `..._ERROR` for `notaudio.m4a` and a
   missing file. Is C2's 2 s timeout right?
4. `SetLoop(TRUE)`: the gap at the wrap with `tone10.wav` and
   `tone10.m4a` (record the output with a loopback capture, or by ear).
5. `SetPlaybackRate(1.5)`: does pitch stay the same?
6. No audio device (disable the output device in Sound settings, as on CI):
   does engine creation fail, or `Play`? What error?
7. Answer each question in `results/spikes/SP5.md`, and adjust W3's design
   (and C2's timeout via a hand-off) if needed.

### SP6. GStreamer gapless loop

Time box: two days.

1. In `kivy/lib/gstplayer/_gstplayer.pyx` (scratch branch), try both:
   a segment seek (`GST_SEEK_FLAG_SEGMENT`, then a non-flushing segment
   seek back to 0 on `SEGMENT_DONE`), and re-setting the URI on playbin3's
   `about-to-finish` signal.
2. For each: the new C or Cython needed (signal handlers run on a
   GStreamer thread; the core polls messages from the Clock today), and
   the gap at the wrap with `tone10.wav` and `tone10.ogg` on Windows, WSL2
   and the Pi. Target: under 10 ms.
3. Also check a rate seek for `pitch` (C7): does it change pitch with
   speed, and does it survive the loop?
4. Answer in `results/spikes/SP6.md`: the method W4a uses.

### SP7. Media3 load wait across threads

Time box: one day, in the Android check app with Media3 added.

C2 now requires the `android` provider's Media3 backend to wait in
`load()`, on Kivy's thread, until the player (on the UI thread) reports
`STATE_READY` or an error.

1. Build an ExoPlayer for a local file on the UI thread
   (`run_on_ui_thread`), with a `Player.Listener` that sets a
   `threading.Event`; wait on it from Kivy's thread with a 2 s timeout.
   Confirm no deadlock (the pyjnius callback needs the GIL while Kivy's
   thread waits) and measure the time to `STATE_READY` (cold and warm)
   and to the error for `notaudio.mp3` and a missing file.
2. Audio-only playback with no `Surface`: no warnings or errors.
3. Load 20 sounds in a row (a game loading its effects at startup): total
   time, and whether players must be released before more are created.
4. Answer in `results/spikes/SP7.md`; adjust W5 (and C2's timeout via a
   hand-off) if needed.

---

## W1. What SDL3 plays on each platform

No Kivy changes; results go to `notes/audio-providers/results/`.

1. Run `tools/format_probe.py` with Kivy `upstream/master` on:
   - Windows 11 (source build), with and without the GStreamer wheels
     installed;
   - WSL2 Ubuntu 26.04 (source build, or the manylinux wheel from the
     latest `manylinux_wheels.yml` run);
   - the Pi 5 (source build, or the `rpi_wheels.yml` artifact);
   - Android: create `notes/audio-providers/android-check/` from
     `android-provider-check/` (package `org.kivyforge.audiocheck`; no
     Media3 dependency yet). `main.py` calls `run_probe()` on the bundled
     fixtures and logs `APROBE` lines; `run_check.sh` pulls them from
     logcat. Use the published dev wheels (master has no audio changes
     since).
2. Save each run as `results/<platform>-w1/probe.json` and write
   `results/W1.md`: per platform, the SDL3 decoder names, which fixtures
   SDL3 opens and plays to the end, `length` for each (O2: is
   `short1.aiff`'s length right?), and which provider plays each format by
   default.
3. Optional: if a small public-domain tracker file (`.mod` or `.xm`) is at
   hand, add it to the fixtures and probe it on Linux (the `XMP` decoder).
4. Add a "Hand-offs" entry in `README.md` for the Mac: the decoder sets
   and the O2 result (AIFF `length` per platform).

## W2. Check phase 1 (`audio-sdl3-first`) on 4 platforms

When the Mac pushes `origin/audio-sdl3-first`:

1. Build it on Windows, WSL2 and the Pi; run `format_probe.py` and the
   existing audio tests (`kivy/tests/test_audio*.py`).
2. Android: the branch changes `audio_sdl3.pyx`, so build real wheels
   (kivy-dev-build guide) and run the check app.
3. Expected on Windows, Linux and the Pi: wav/flac/mp3/ogg (and opus,
   tracker and game-music formats where W1 found the decoders) go to
   `SoundSDL3`; m4a/aac go to `gstplayer` (on Windows only with GStreamer;
   nothing without it until W3). Expected on Android (order unchanged,
   O1): everything `android` claims stays on `SoundAndroidPlayer`; opus,
   tracker and game-music formats go to `SoundSDL3` where the build has
   the decoders.
4. Save to `results/<platform>-w2/` and report in `README.md`.

## W3. Phase 3: `mediafoundation` audio provider

Branch `audio-mediafoundation`. Plays what SDL3 can't on Windows (m4a, aac,
wma, mp4 audio) with no extra package, through `IMFMediaEngine` in
audio-only mode.

Design:

- **Files**: `kivy/core/audio_output/audio_mediafoundation.pyx`, plus
  `audio_mediafoundation_implem.h` and `audio_mediafoundation_implem.cpp`
  compiled as a separate translation unit, for the same reason the video
  provider does it (Windows COM headers' macros corrupt Cython output;
  see the header comment in `kivy/core/video/video_mediafoundation_implem.h`).
- **Build**: in `setup.py`, under `if c_options['use_mediafoundation']:`,
  add `sources['core/audio_output/audio_mediafoundation.pyx']` with
  `libraries: ['mfplat', 'mf', 'mfuuid', 'ole32', 'oleaut32']` and the
  two implem files in `depends` (the `.cpp` must keep its extension; see
  the comment there). No D3D11, DXGI or GL.
- **Reuse, don't refactor video**: copy from
  `video_mediafoundation_implem.cpp` the COM/`MFStartup` setup (around
  line 161), the `EngineNotify` callback (line 186) and the
  `IMFSourceResolver` extension (lines 244-370, so any installed MF source
  opens, not only the web formats). Create the engine with
  `MF_MEDIA_ENGINE_AUDIOONLY` and without `MF_MEDIA_ENGINE_DXGI_MANAGER`
  or `MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT`. Leave the video files
  untouched; factoring shared code into one place can be a follow-up.
- **C++ class** `AudioPlayer(const char *url)`: `load()`, `play()`,
  `pause()`, `stop()` (pause and `SetCurrentTime(0)`), `seek(double)`,
  `position()`, `duration()`, `setVolume(double)`, `setLoop(bool)`
  (`IMFMediaEngine::SetLoop`), `setRate(double)` (`SetPlaybackRate`),
  `isEOS()`, `hasError()`, `lastError()`, and
  `waitReady(int timeout_ms)` that blocks until
  `MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA` or `..._ERROR` (an event set by
  `EngineNotify`, which runs on an MF thread, so waiting on Kivy's thread
  can't deadlock).
- **Python provider** `SoundMediaFoundation(Sound)`, `_provider_name =
  'mediafoundation'`:
  - `extensions()`: `m4a, mp4, aac, adts, wma, 3gp, 3g2, mp3, wav, flac`
    (SDL3 takes the formats it claims first; the rest are here so
    `audio_output_provider='mediafoundation'` and fallback work).
  - `load()`: create the player, `waitReady(2000)`; raise `SoundLoadError`
    with `lastError()` on error or timeout (C2: required, `gstplayer` is
    behind it).
  - Poll `isEOS()`/`hasError()` from a Clock event while playing, as
    `video_mediafoundation.pyx` does (`_update`, around line 369): EOS
    with `loop` off calls `_do_eos()`; an error calls `_do_error()`.
  - `pause()`/`resume()` (C5), `get_pos()`, `length`, `on_volume`,
    `on_pitch` (`setRate`; document whether pitch is kept, per SP5, C7),
    `loop` through `setLoop` (document the gap SP5 measured).
- **Register**: add `('mediafoundation', 'audio_mediafoundation')` to
  `PROVIDER_CONFIGS['audio_output']` after `avplayer`, and in
  `kivy/core/audio_output/__init__.py` append it on `win32` after `sdl3`
  and before `gstplayer`. This is the one shared-file edit W3 makes.
- **Docs**: module docstring (formats, pitch behavior, loop gap),
  `environment.rst` `KIVY_AUDIO_OUTPUT` values, `migration.rst` Audio
  section ("m4a and aac play on Windows without GStreamer").

Verify:

- Unit tests that don't need a device: extension list, registration on
  `win32` (patched platform), `load()` raising for a missing file.
- `format_probe.py` on Windows without GStreamer: m4a/aac/wma go to
  `SoundMediaFoundation` and play to the end; `notaudio.m4a` falls back
  (with GStreamer installed, to `gstplayer`, which also fails, and the
  aggregated warning names both).
- The shared suite (`test_audio_behavior.py`) passes for
  `mediafoundation` with a local merge of S1 and S2.
- Windows CI (`test_windows_python.yml`) builds the module; tests skip
  without an audio device.

PR title: "Audio: Media Foundation provider (Windows)".

## W4a. `gstplayer` meets the contracts

Branch `audio-gstplayer-contract`. `kivy/core/audio_output/audio_gstplayer.py`
was rewritten on the runtime-loaded core in #9540; this finishes the
contract.

1. **Load failure**: `gstplayer` is the last candidate everywhere (C1), so
   it may return before GStreamer has opened the file (C2). Missing runtime,
   missing plugin and open errors that arrive later go through
   `_do_error()` instead of `_fail()`'s log-only path. When the runtime is
   already known to be missing at `load()` (`init_status()[0] ==
   'error'`), raise `SoundLoadError` instead.
2. **End of stream**: EOS with `loop` off calls `_do_eos()`.
3. **Gapless loop**: today EOS seeks back to 0, which leaves a gap. Use
   the method SP6 chose; add the needed message type to `pop_messages()`
   if missing. Confirm the gap with `tone10.wav` on Windows, WSL2 and the
   Pi; target under 10 ms.
4. **Pause** (C5): `set_state(STATE_PAUSED)` without the seek `stop()` does.
5. **`pitch`** (C7): a rate seek, as SP6 found; document whether pitch
   changes with speed.
6. Remove `gstplayer` from `_KNOWN_FAILURES`; run the shared suite on
   Windows (with GStreamer), WSL2 and the Pi.

PR title: "GStreamer audio: gapless loops, pause, on_eos and on_error".

## W4b. `android` (`MediaPlayer`) meets the contracts

Branch `audio-android-contract`. Python only, so `make_wheels.py` overlays
work for device checks.

1. `load()`: wrap `prepare()`'s `JavaException` (and the `content://`
   context error) in `SoundLoadError`.
2. `pause()`/`resume()`: `MediaPlayer.pause()`/`start()`.
3. End of sound: the existing `OnCompletionListener` calls `_do_eos()`
   when not looping (on Kivy's thread: schedule through the Clock, as the
   listener runs on a Java thread).
4. Loop: `setLooping(True)` instead of restarting on completion; measure
   the gap.
5. Errors after load: an `OnErrorListener` calls `_do_error()` with
   `what`/`extra` decoded to text.
6. `pitch`: `PlaybackParams().setSpeed()` on API 23+ (keeps pitch);
   document.
7. Run the shared suite in the Android check app (a "behavior" scenario
   running the suite's checks in-app), and the probe.

PR title: "Android audio: pause, gapless loops, on_eos and on_error".

## W4c. Check the `sdl3` contract branch on 4 platforms

When the Mac pushes `origin/audio-sdl3-contract` (S4): build it on Windows,
WSL2 and the Pi, run the shared suite for `sdl3`; on Android build real
wheels (it's a `.pyx` change) and run the behavior scenario. Report in
`README.md`.

## W5. Phase 5: Media3 in the `android` provider

Branch `audio-android-media3`, after W4b.

Design:

- One provider, `android`, two backends in `audio_android.py`:
  `_Media3Backend` and `_MediaPlayerBackend` (today's code). `load()` uses
  Media3 when its classes resolve (cache the probe, like
  `_get_media3()` in `kivy/core/video/video_android.py`) and the extension
  isn't a MIDI or ringtone format (`mid xmf mxmf rtttl rtx ota imy`), and
  `MediaPlayer` otherwise. No app needs a new dependency to keep today's
  audio; MIDI keeps working.
- Threading as in the Media3 video provider (its plan's D2): the player is
  created and called on the Android UI thread
  (`kivy.mobile._platform.android.run_on_ui_thread`), state is read back
  through a `Player.Listener` into a lock-protected snapshot, and Kivy
  polls it from the Clock.
- Mapping: `setRepeatMode(REPEAT_MODE_ONE)` for `loop` (gapless);
  `PlaybackParameters(speed)` for `pitch` (pitch kept, C7);
  `pause()`/`play()`; `STATE_ENDED` with repeat off calls `_do_eos()`;
  `onPlayerError` calls `_do_error()` with the error code name and message
  (reuse `_describe_error()` from the video provider).
- Load (C2): `android` is first on Android with `sdl3` behind it, so for
  local files `load()` must wait up to 2 s for `STATE_READY` or an error
  and raise `SoundLoadError` on error or timeout, letting `SoundLoader`
  fall back to SDL3. The wait runs on Kivy's thread while the player
  lives on the UI thread; that can't deadlock as long as nothing on the UI
  thread waits for Kivy's thread. URL sources (`content://`, and
  `http(s)` in W6) may return early and report failures through
  `on_error`.
- `content://` plays through Media3's default data source. Keep `schemes()
  == ('content',)` until W6.
- Audio attributes: `USAGE_MEDIA`, `AUDIO_CONTENT_TYPE_MUSIC`; audio focus
  handling off (today's `MediaPlayer` path doesn't request focus either);
  note it as a possible follow-up.
- **MIDI decision** (AUDIO-PLAN "Decisions"): with MIDI on the
  `MediaPlayer` backend, no `media3-exoplayer-midi` dependency is needed.
  Record that in AUDIO-PLAN.md as the decision unless the user decides
  otherwise.
- Docs: the Android packaging guide gets "Playing audio": which Media3
  modules add what (`media3-exoplayer` for gapless loops, `-hls`/`-dash`
  for streams in W6), the R8 keep rule (same as video), and what happens
  without them.

Verify in the Android check app, with Media3 in `[tool.kivy.android.gradle]`
dependencies (as `android-provider-check/pyproject.toml` declares it) and
without: probe, shared suite, `content://` (a MediaStore file), MIDI
(`short1.mid` if available, otherwise any `.mid`), debug and release
(R8) variants.

PR title: "Android audio: play through Media3 when the app includes it".

## W6. Phase 6: streaming

One branch per provider (`audio-gstplayer-streams`,
`audio-mediafoundation-streams`, `audio-android-streams`), after the
provider's contract step. Each provider declares `schemes()` (C6) and
reports stream failures through `on_error`.

| Provider | `schemes()` | Notes |
|---|---|---|
| `gstplayer` | `http`, `https`, `rtsp` | playbin3 opens them already; HLS/DASH through the same `http(s)` URLs |
| `mediafoundation` | `http`, `https` | the source resolver opens progressive HTTP; check HLS on Windows 11 and document what works |
| `android` | `content`, `http`, `https` | Media3 backend only; HLS/DASH need the app's `media3-exoplayer-hls`/`-dash` modules; the `MediaPlayer` backend keeps `content` only |

- `length` is 0 for live streams; `seek` is ignored on live streams (log at
  debug).
- Test with the streams in `notes/video-providers/stream-check/` (the Pi 5
  stream server, HTTP MP3/AAC, HLS audio-only), on Windows, WSL2, the Pi
  and Android. Results to `results/<platform>-w6/`.
- Docs: add rows to the schemes table in the `audio_output` module
  docstring (the Mac's M6 adds Apple's).

PR titles: "GStreamer audio: play network streams", "Media Foundation audio:
play http and https streams", "Android audio: play network streams through
Media3".
