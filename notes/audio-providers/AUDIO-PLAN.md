# Kivy 3.0 Audio Provider Plan

The high-level plan. The detailed, executable plans are in this directory:
`CONTRACTS.md` (the shared API every provider implements), `PLAN-MAC.md`
(shared work, macOS, iOS) and `PLAN-WINDOWS.md` (Windows, Linux, Raspberry
Pi, Android). `README.md` has the status and the hand-offs between the two
machines.

## Matrix

| Platform | Primary | Secondary (fills format and streaming gaps) |
|---|---|---|
| macOS | SDL3 | AVFoundation (`avplayer`, rubicon-objc; [#9530](https://github.com/kivy/kivy/pull/9530)) |
| iOS | SDL3 | AVFoundation (`avplayer`, rubicon-objc; [#9530](https://github.com/kivy/kivy/pull/9530)) |
| Windows | SDL3 | Media Foundation (new, shares the video provider's `IMFMediaEngine` code) / GStreamer (modernized, optional) |
| Linux | SDL3 | GStreamer (modernized, shared with video) |
| Raspberry Pi | SDL3 | GStreamer (modernized, shared with video) |
| Android | SDL3 | `android`: Media3 ExoPlayer when the app includes it (shared with video), `MediaPlayer` otherwise |

Removed in 3.0: `ffpyplayer` (audio, and with the video plan, video and image).

## Goals

- SDL3 first on every platform: low start latency (12-42 ms measured on a
  Mac and an iPhone), a non-blocking `play()`, mixing many sounds, and the
  same behavior everywhere. This is the opposite of video, where SDL has
  nothing to offer. On Android, SDL3 pauses with the app; background
  playback is a separate feature (see Decisions and `MOBILE-AUDIO.md`).
- Native OS frameworks only to fill gaps: formats SDL3_mixer can't decode
  (m4a/aac, and wma on Windows), and streaming URLs.
- Share secondary providers with video (AVFoundation, Media Foundation,
  GStreamer, Media3), so audio adds no new frameworks.
- No extra binary dependencies for a default install.
- Keep the number of maintained providers small: five audio providers,
  the same count as today (ffpyplayer goes, Media Foundation comes in), and
  four of the five share their framework with a video provider.
- The same behavior from every provider (errors, end of stream, loop,
  pause), so switching provider by file type doesn't change what an app
  sees.

## Why each secondary

- **macOS, iOS: AVFoundation.** SDL3_mixer can't decode m4a, aac or caf
  in Kivy's Apple builds, and its AIFF decoder (3.2.0) reports no duration.
  `AVAudioPlayer` plays all of them with gapless loops and time-stretch
  `pitch` (length is exact except raw ADTS `.aac`, which is about 1 s off).
  Its 55-176 ms start latency and 13-107 ms blocking
  `play()` are acceptable for the files it gets (mostly music), and SDL3
  keeps wav/mp3/flac/ogg. `AVAudioPlayer` can't stream: streaming would use
  `AVPlayer`, which the video provider already wraps.
- **Windows: Media Foundation.** Same gap as Apple (m4a/aac, plus wma), no
  extra package, and `IMFMediaEngine` plays audio-only files with the setup
  code the video provider already has. GStreamer stays an optional
  install, as in the video plan.
- **Linux, Raspberry Pi: GStreamer.** The distribution's own framework.
  It covers aac/m4a when the distro's plugins are installed, and streaming.
  Shared with the modernized video provider.
- **Android: Media3 ExoPlayer.** The video plan brings in Media3 anyway;
  using it for audio too means one Android media stack, gapless playback,
  streaming (HLS/DASH), and `content://` URIs, which today's `MediaPlayer`
  provider handles ([#9500](https://github.com/kivy/kivy/pull/9500)). Until
  Media3 lands, `MediaPlayer` stays.

## What SDL3 covers, and the gaps

| build | SDL3_mixer decoders | gap the secondary fills |
|---|---|---|
| macOS, iOS (Xcode project) | wav, flac, mp3, ogg | m4a, mp4, aac, caf, aiff, au |
| Linux, Raspberry Pi (CMake, vendored) | wav, flac, mp3, ogg, opus, aiff, au, voc, wavpack, tracker (mod/xm/it/s3m via libxmp), game music (gme), MIDI (Timidity, only with installed patches; not used, see Decisions) | m4a, aac, streaming |
| Windows (`kivy_deps.sdl3`) | to verify | m4a, aac, wma, streaming |
| Android (python-for-android recipe) | to verify | m4a, aac, streaming, `content://` |

`SoundSDL3.extensions()` only maps the Apple build's decoder names (plus
AIFF/AU/VOC/WavPack). On Linux, `OPUS`, `FLAC` (libFLAC), `XMP`, `GME` and
`TIMIDITY` are logged as "Unrecognized audio decoder" and their formats
are never routed to SDL3, even though the build can play them. Mapping
them restores tracker formats (lost from 2.x's `MusicSDL2`) and adds Opus
on Linux at no cost. `TIMIDITY` stays unmapped (no MIDI support; see Decisions).

## Provider order

The order providers register in is `kivy_options['audio_output']`, which
comes from `PROVIDER_CONFIGS['audio_output']` in `kivy/core/__init__.py`
(or `KIVY_AUDIO_OUTPUT`). `core_register_libs()` imports in that order;
the appends in `kivy/core/audio_output/__init__.py` only decide which
modules a platform can import.

Today (master) the list is `gstplayer, ffpyplayer, sdl3, avplayer,
android`. So:

- Windows and Linux: `gstplayer` is first whenever GStreamer is installed,
  and takes wav, mp3, ogg and flac from SDL3 (#9540's comment says it was
  put after `sdl3`, but only the append moved).
- macOS, iOS: `sdl3` is already ahead of `avplayer`.
- Android: `sdl3` is already ahead of `android`, which gets m4a, aac, 3gp,
  MIDI and `content://`.

Kivy 2.x: `kivy_options['audio']` was `gstplayer, pygame, ffpyplayer,
sdl2, avplayer`, without `android`, so the Android provider never loaded
unless the app set `KIVY_AUDIO`; kivy-ios's template sets
`KIVY_AUDIO=sdl2`. Android and iOS played everything through SDL2, and on
macOS SDL2 came before AVFoundation.

New order: `sdl3`, then the platform's secondary (`avplayer`,
`mediafoundation`, `android`), then `gstplayer` where its runtime exists.
One global list does it for every platform. `content://` routes to the
Android provider by scheme. Each secondary keeps its full extension list;
it only receives the formats SDL3 doesn't claim. The exact rule is
contract C1 in `CONTRACTS.md`.

So "SDL first" was already the effective order on macOS (since 2015), iOS
and Android. Where SDL wasn't first, it was behind GStreamer and
ffpyplayer, which came first in the options list; nothing on record says
why. The recorded reasons for adding avplayer (2015, "supports far more
format than the current sdl2 (mp3, ogg, wav)") and android (2020, #6603,
"support natively AAC (mp4/aac/m4a)") are format coverage. SDL3 closed the
quality gaps of Kivy 2's `sdl2` provider (whole-file decode at load, no
seek or position, buffer-copy pitch, gapped loops); AAC/m4a, streaming and
`content://` are still gaps, and the secondaries keep them.

## Removing ffpyplayer

Why: not installable on Python 3.14 (the `media`/`full` extras already skip
it there, and CI carries a TODO), open crash reports
([#8747](https://github.com/kivy/kivy/issues/8747),
[#6552](https://github.com/kivy/kivy/issues/6552),
[#6744](https://github.com/kivy/kivy/issues/6744)), an asynchronous seek
that breaks play-then-seek ([#8591](https://github.com/kivy/kivy/issues/8591)),
and every format it plays is covered by SDL3 plus the platform secondary.

Scope (audio part of a coordinated removal; #9546 implements all of it):

- `kivy/core/audio_output/audio_ffpyplayer.py` and its registry and
  `audio_output/__init__.py` entries.
- With the video plan: `kivy/core/video/video_ffpyplayer.py`,
  `kivy/core/image/img_ffpyplayer.py`.
- `pyproject.toml` `media` and `full` extras, PyInstaller hooks and test
  specs, `environment.rst` values, installation docs, the CI TODO.
- Migration guide: what replaces it per platform, and that
  `KIVY_AUDIO_OUTPUT=ffpyplayer` is gone.

Open PRs that become moot: [#8546](https://github.com/kivy/kivy/pull/8546)
(ffpyplayer volume at load).

## Same behavior from every provider

Like `on_error` for video ([#9528](https://github.com/kivy/kivy/pull/9528)):

1. **Errors**: today every provider only logs, and avplayer, SDL3 and
   GStreamer return a `Sound` that will never play. `SoundLoader` already
   tries the next provider that claims the extension, but only when the
   provider's constructor raises, which only `android` does
   (`MediaPlayer.prepare()`). Unlike video, audio has a fallback, so an
   error must only reach the app once every provider has failed:
   - **Load failures raise; they don't dispatch `on_error`.** Each
     provider checks in `load()` that the file opened (SDL3:
     `MIX_LoadAudio` returned NULL; avplayer: `AVAudioPlayer` returned
     nil) and raises. `SoundLoader` tries the next candidate. If all
     fail, `load()` returns `None` as it does today, with one warning
     listing each provider's reason (strict mode raises). The app can't
     bind `on_error` before `load()` returns anyway, so the return value
     is the signal.
   - **`on_error` is only for errors after a `Sound` was returned**
     (playback fails, decoding fails partway through, a stream drops).
     A provider has been chosen by then, so there's no fallback, the
     same as video's `on_error`.
   - **Providers that only learn about a failure asynchronously** (GStreamer
     today, possibly Media3) either confirm the file opens before `load()`
     returns (for example, preroll with a timeout for local files) or
     stay last in the order for their extensions, so a later `on_error`
     still means every provider failed. A URL goes only to its scheme's
     owner on that platform (one provider; no fallback), so it doesn't
     have this problem.
2. **End of stream**: `on_eos` for all providers
   ([#2156](https://github.com/kivy/kivy/issues/2156),
   [#8609](https://github.com/kivy/kivy/pull/8609), open since Feb 2024);
   see Decisions.
3. **`on_stop` and `loop`**: `on_stop` only when a playing sound stops;
   loops gapless with no events per pass. SDL3 and avplayer do this after
   [#9533](https://github.com/kivy/kivy/pull/9533) and #9530; GStreamer
   restarts at end of stream and needs the same treatment.
4. **Pause**: a native pause (`pause()`/`resume()` or a `'pause'` state,
   like `Video`). Every framework has one (`MIX_PauseTrack`,
   `AVAudioPlayer.pause`, `IMFMediaEngine::Pause`, GStreamer
   `PAUSED`, ExoPlayer `pause`). Today the only option is stop, play, seek.
5. **`pitch`**: document per provider (SDL3 and GStreamer: speed and pitch;
   AVFoundation, Media Foundation, ExoPlayer: rate with pitch kept), which
   also answers [#8364](https://github.com/kivy/kivy/issues/8364) and
   [#4274](https://github.com/kivy/kivy/issues/4274) on those platforms.
6. **A shared provider test suite**: one set of behavior tests run against
   every available provider (start, stop, loop, seek, length, events),
   based on the rubicon spike's benchmark, skipping playback when CI has no
   audio device.

## Phases

0. **In flight**: rubicon-objc and avplayer (#9529-#9532), SDL3 loops and
   events (#9533).
1. **SDL3 first**: set the order in `PROVIDER_CONFIGS` (which moves `gstplayer` behind `sdl3`); map the remaining SDL3_mixer
   decoder names (not `TIMIDITY`); verify the Windows and Android decoder
   sets. The `android` provider keeps its MIDI extensions (see Decisions).
   Update #9533's migration note, which says tracker formats aren't
   supported, for the builds where `XMP` is mapped.
2. **Remove ffpyplayer**: done as
   [#9546](https://github.com/kivy/kivy/pull/9546) (open), together with
   the video and image providers. The native video providers it waited on
   are merged (Media Foundation #9525, Media3 #9534, GStreamer #9540/#9542).
3. **Windows Media Foundation audio provider**, reusing the merged video
   provider's Media Engine code.
4. **Consistent behavior**: load failures raise so `SoundLoader` falls
   back to the next provider, `on_error` only after a `Sound` was returned
   (see "Same behavior from every provider"), end of stream, pause, pitch
   docs, shared test suite. Until then, a file SDL3 claims but can't open
   doesn't fall back, which matters more once SDL3 is first (phase 1).
5. **Android Media3**, reusing the merged video provider's patterns. Media3
   is used when the app includes it; otherwise the `MediaPlayer` path stays
   (see Decisions). Decide Android MIDI here.
6. **Streaming URLs** through the secondaries (`AVPlayer` on Apple, Media
   Foundation, GStreamer, Media3), with errors reported through `on_error`
   ([#7419](https://github.com/kivy/kivy/issues/7419),
   [#8591](https://github.com/kivy/kivy/issues/8591)).

## Decisions

- **Android: `sdl3` before `android`** (2026-11-25, CONTRACTS O1; reverses
  the 2026-11-24 decision, which assumed `android` led on Android in 2.x
  and on master). Compared on merit in `MOBILE-AUDIO.md`: with `android`
  first, sounds keep playing in the background with no notification, no
  Python running and no way for the user to stop them, and Android 17
  blocks it without a foreground service. SDL3 pauses and resumes with the
  app, starts faster, has no per-sound player limit, and behaves as on
  other platforms. Background music becomes an opt-in feature built on
  Media3's `MediaSessionService` (phase 5 or later), whatever the order.
- **AIFF through SDL3** (2026-11-24, CONTRACTS O2): mapped only on builds
  where SDL3_mixer reports its duration correctly (W1 measures).
- **Android: Media3 when the app has it, `MediaPlayer` otherwise.** Media3
  is only in an APK when the app adds the Maven dependency (the Media3 video
  provider has the same requirement). The `android` audio provider uses
  Media3 when its classes resolve and keeps the `MediaPlayer` path when they
  don't, so no app needs a new dependency to keep the audio it has today.
  MIDI and the ringtone formats can stay on `MediaPlayer` either way.
- **`on_eos` for audio**: a sound that reaches its end without looping
  dispatches `on_eos`, then `on_stop`; looping sounds dispatch neither per
  pass. Supersedes [#8609](https://github.com/kivy/kivy/pull/8609) (contract
  C4).
- **URL sources are routed by scheme.** Each provider declares the schemes
  it supports alongside `extensions()` (default: local files only).
  `SoundLoader` sends a URL to the first provider that supports its
  scheme, without requiring a recognizable extension, and returns `None`
  with a clear message if none does. URLs never reach SDL3, whose
  `MIX_LoadAudio` only reads local files. Apple has no streaming provider
  until an `AVPlayer` path exists. Download-then-play was rejected as a
  substitute (no live streams or HLS, a delay before playback).
- **No `AVAudioEngine` spike for Apple latency now.** Under this plan
  avplayer only gets m4a/aac/caf/aiff/au (mostly music), and an engine-based
  provider is much larger to maintain. In phase 4, call
  `AVAudioPlayer.play()` off the main thread to remove the 13-107 ms block
  (not the start delay), and document "use wav/ogg/mp3 for sound effects".
  Revisit if users report latency, or when an `AVPlayer` path for
  streaming is designed.
- **Keep SDL's iOS session default** (Playback with MixWithOthers and
  DuckOthers, the same as Kivy 2.x since SDL 2.0.14). No Kivy or kivy-ios
  issue complains about it; the only related Kivy report
  ([kivy-ios #234](https://github.com/kivy/kivy-ios/issues/234)) is the
  opposite failure (an Ambient app silenced by the switch). SDL rejected
  two requests to return to Ambient
  ([#3831](https://github.com/libsdl-org/SDL/issues/3831),
  [#11140](https://github.com/libsdl-org/SDL/issues/11140)) as an
  intentional choice. Through SDL's hint there's no Playback session that
  stops other apps' audio: `KIVY_AUDIO_CATEGORY=playback` ends up the same
  as the default, because SDL adds DuckOthers and iOS then sets
  MixWithOthers again (checked on an iPhone). Document
  `KIVY_AUDIO_CATEGORY=ambient` for games and UI sounds, and make avplayer set the same category on iOS so an app that
  only plays m4a/aac isn't left in SoloAmbient. Recheck when Kivy moves to
  SDL 3.6 ([#15452](https://github.com/libsdl-org/SDL/pull/15452) rewrites
  session handling).
- **No MIDI support added in Kivy 3.0; Android keeps what it has until
  Media3.** MIDI was never documented: no guide or reference page mentions
  it, on stable or master (the only written trace was one line in Kivy 2's
  `sdl2` provider docstring). In 2.x it worked on Windows and on Linux with
  Timidity patches installed (Android unverified; on 3.0 master the
  `android` provider plays it; see "MIDI background" below), and
  #7833 is the only MIDI issue in Kivy's history. Apps that need MIDI can
  drive an external synthesizer or MIDI library. Consequences:
  - Don't map SDL3_mixer's `TIMIDITY` decoder in `SoundSDL3.extensions()`.
  - Leave the `android` provider's MIDI extensions (`mid`, `xmf`, `mxmf`,
    and the ringtone formats Android plays with the same synth: `rtttl`,
    `rtx`, `ota`, `imy`) for now. SDL3 doesn't claim them, so they go to
    `android`, and `MediaPlayer` plays them with no extra library, so
    keeping them costs nothing.
  - Revisit in phase 5. Media3 plays MIDI only with the
    `media3-exoplayer-midi` extension (JSyn, published on jitpack): either
    take that dependency, keep `MediaPlayer` for these formats, or drop
    them then with a migration note.
  - #9533's migration note says the MIDI formats Kivy 2 played through
    SDL2_mixer aren't supported. Add to it: on Android, MIDI now plays
    through the `android` provider.

## MIDI background

**What Kivy 2.3.1 does.** Checked against the 2.3.1 wheels (the macOS
SDL2_mixer was loaded and asked for its decoders) and SDL2_mixer 2.6.3's
Xcode settings for kivy-ios:

| Platform | Who plays `.mid` in 2.3.1 | Result |
|---|---|---|
| Android | `MusicSDL2`: the `android` provider wasn't in `kivy_options['audio']`, so it never loaded by default | not verified; depends on python-for-android's SDL2_mixer MIDI build (Timidity has no patches on a device). In 3.0 the `android` provider plays MIDI with the platform synth (also `.xmf`, RTTTL, OTA, iMelody) |
| Windows | `MusicSDL2`, SDL2_mixer native MIDI (winmm, Microsoft GS Wavetable Synth) | works out of the box |
| Linux, Raspberry Pi | `MusicSDL2`, SDL2_mixer Timidity | only with patches installed (`/etc/timidity.cfg` or `TIMIDITY_CFG`) |
| macOS | `MusicSDL2` claims `.mid`, but SDL2_mixer 2.8.0 has no MIDI decoder (native MIDI is commented out in SDL2_mixer's Xcode config) | broken: warning, sound never plays |
| iOS | same; SDL2_mixer 2.6.3's Xcode build has no MIDI, and its native MIDI is macOS-only | broken |

`MusicSDL2.extensions()` claims `mid`/`midi` unconditionally ("Assume
native midi support"). Where MIDI worked, it used SDL2_mixer's single music
channel: one at a time, `length` 0, no seek or position, global music
volume, loop by polling. macOS played MIDI in older wheels (2.0/2.1, see
[#7833](https://github.com/kivy/kivy/issues/7833)); nobody reported losing
it. #7833 (a crash at the end of a MIDI file, never reproduced) is the only
MIDI issue in Kivy's history.
