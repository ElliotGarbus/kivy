# SDL3 versus native audio on Android and iOS

What each provider gives an app and its users on the two mobile platforms,
to choose the default order (C1) on merit. The developer can always change
the order (`KIVY_AUDIO_OUTPUT`, `audio_output_provider=`); this is about the
default.

Sources are SDL 3.4.20 and SDL3_mixer 3.2.0 (the versions in
`tools/build_*_dependencies.sh`), Kivy master, kivyforge, and the Android
and Apple documentation linked inline. Rows marked "measure" are not yet
verified on a device; SP8 measures them.

## What happens today

- Kivy 2.x: `kivy_options['audio']` was `gstplayer, pygame, ffpyplayer,
  sdl2, avplayer`. `android` wasn't in it, so `core_register_libs()` never
  loaded the Android provider unless the app set `KIVY_AUDIO`. kivy-ios's
  app template sets `KIVY_AUDIO=sdl2`. Both mobile platforms played
  everything through SDL2.
- Kivy 3.0 master: `PROVIDER_CONFIGS['audio_output']` is `gstplayer,
  ffpyplayer, sdl3, avplayer, android`, and that list, not the append order
  in `audio_output/__init__.py`, is the order providers register in. So
  `sdl3` is first on Android and iOS for what it decodes. `android` gets
  m4a, aac, 3gp, MIDI and `content://`; `avplayer` gets m4a, aac, caf, aiff.

## Android: `sdl3` versus `android` (MediaPlayer, later Media3)

| | `sdl3` | `android` |
|---|---|---|
| App goes to the background | SDL pauses its audio device in `Android_OnPause` (`SDL_HINT_ANDROID_BLOCK_ON_PAUSE`, default on; Kivy and kivyforge don't change it) and resumes it on return. Sounds continue where they stopped. | The playing `MediaPlayer` keeps playing, because playback runs in the media server. Nothing else runs: SDL also blocks Kivy's main loop, so the Clock, `on_eos`, `on_stop` and any Python logic (next track, fade) wait until the app returns. |
| Keeps playing in the background reliably | No. | Not without a foreground service. Without one the system may kill or freeze the process, and from Android 17 audio playback APIs fail silently for background apps without one ([background audio hardening](https://developer.android.com/about/versions/17/changes/bg-audio)). |
| What Android expects | Sound stops when the app leaves the screen, unless playing audio is the app's purpose. | Apps that do play in the background use a `mediaPlayback` foreground service with a MediaStyle notification and lock-screen controls, and request audio focus ([core app quality: Audio](https://developer.android.com/docs/quality-guidelines/core-app-quality)). Media3's `MediaSessionService` provides this. Today's provider does none of it: in the background the user has no control to stop the sound except killing the app. |
| Audio focus (calls, other apps) | Not requested. | Not requested by `MediaPlayer`. Media3 handles it with `setAudioAttributes(..., handleAudioFocus=True)`. |
| Latency to start a sound | One mixed AAudio stream in low-latency mode (`SDL_HINT_ANDROID_LOW_LATENCY_AUDIO`, default on). Measure. | Each `start()` goes through the media server; Android recommends `SoundPool`, not `MediaPlayer`, for short effects. Measure. |
| Many sounds at once | No limit on tracks (`SDL_mixer.h`); all mix in-process. | One native player and decoder per `Sound`. Devices cap concurrent players. Measure where loading fails. |
| Load cost | Opens and parses the file; decodes while playing (`predecode=False`). | `prepare()` per `Sound`, synchronous. Measure. |
| Formats | The decoders built into Kivy's Android SDL3_mixer; W1 lists them. No AAC/m4a; no MIDI (decided). | Platform codecs: mp3, AAC (m4a, mp4, 3gp, ADTS), FLAC, Vorbis, Opus (Ogg on Android 10+), WAV, AMR, MIDI ([supported formats](https://developer.android.com/media/platform/supported-formats)). |
| `pitch` | Changes speed and pitch together (frequency ratio), as on every other platform. | Changes speed and keeps pitch (`PlaybackParams`, API 23+). The same property sounds different depending on which provider the file's format landed on. |
| `loop` | Gapless (`MIX_SetTrackLoops`, #9533). | `setLooping`; gap on some devices. Media3 is gapless. Measure. |
| URLs | None. | `content://` today; `http(s)` in phase 6. |

## iOS: `sdl3` versus `avplayer` (AVAudioPlayer)

On iOS, background playback isn't a property of the provider. It depends on
the app's audio session category, which is shared by every audio API in the
process, and on `UIBackgroundModes` containing `audio` in `Info.plist`
(kivyforge can add extra Info.plist keys). With category `playback` and
that key, both providers keep playing in the background; with `ambient` or
`soloambient`, neither does.

| | `sdl3` | `avplayer` |
|---|---|---|
| Audio session | SDL sets it when the mixer opens its device (the first `sdl3` load): category `playback` with mix-with-others and duck-others, unless `SDL_HINT_AUDIO_CATEGORY` is set (`KIVY_AUDIO_CATEGORY`, #9533). The device stays open for the app's life, so the session stays active. | Doesn't set it. If `sdl3` never opened its device, the session is iOS's default `soloambient`: silenced by the silent switch, stops other apps' audio, no background playback. Once `sdl3` has opened its device, `avplayer` follows SDL's setting. |
| Effect of SDL's default on users | Plays with the silent switch on. Other apps' music is lowered while the session is active, which is the app's whole life. This is Kivy 2.x behavior too: SDL2 2.28.5 (kivy-ios) uses the same setting, and SDL has since 2.0.14 ([SDL #3831](https://github.com/libsdl-org/SDL/issues/3831)). SDL keeps it deliberately ([SDL #11140](https://github.com/libsdl-org/SDL/issues/11140)), and no Kivy issue reports it; the one related Kivy report, kivy-ios #234, is a user who thought audio was broken under `ambient`. Apple's guidance differs: it favors `ambient` for non-essential sound and says to duck "on a temporary basis only" ([duckOthers](https://developer.apple.com/documentation/avfaudio/avaudiosession/categoryoptions-swift.struct/duckothers)). | Same, if `sdl3` is in use. |
| Interruptions (calls, Siri) | SDL pauses on interruption begin and resumes on end or when the app becomes active. | AVAudioPlayer pauses; `audioPlayerBeginInterruption` and `audioPlayerEndInterruption` on the delegate (S3). |
| Latency to start a sound | One mixed CoreAudio stream. Measure. | One player per `Sound`; `prepareToPlay` reduces start latency. Measure. |
| Formats | wav, flac, mp3, ogg Vorbis (the Xcode project enables `DECODER_WAV`, `DECODER_FLAC_DRFLAC`, `DECODER_MP3_DRMP3`, `DECODER_OGGVORBIS_STB`). | Core Audio: AAC/m4a, ALAC, mp3, wav, aiff, caf, flac. No Vorbis. |
| Decoding | Software, on the CPU. | Hardware AAC decoding where available. Matters for long music, not for effects. |
| `pitch` | Speed and pitch together. | `enableRate` and `rate` (0.5 to 2): speed with pitch kept (S3). |
| `loop` | Gapless. | `numberOfLoops = -1`. |
| Threading | Mixer thread; safe from Kivy's thread. | Not documented as thread-safe; SP3. |

## Consequences for the default

- **Android. Decided (2026-11-25, CONTRACTS O1): `sdl3` first.** With
  `android` first, sounds would continue in the
  background by accident: no notification, no way for the user to stop
  them, no Python running to stop them, and from Android 17 not reliably at
  all. Apps whose purpose is background audio need a foreground service and
  a media session regardless of provider; that's a separate feature (Media3
  `MediaSessionService`, phase 5 or later), not something provider order
  gives. With `sdl3` first, sounds pause with the app and resume with it,
  latency is lower, there's no per-sound player limit, and `pitch` and
  `loop` behave as on every other platform.
- **Android, mixed formats.** Today an app playing m4a music and wav effects
  gets MediaPlayer for the music (continues in the background) and SDL3 for
  the effects (pauses). Whatever the default, document this per format.
- **iOS.** Provider order doesn't decide background behavior; the session
  category does. Decided: keep SDL's default (`playback` with mix-with-
  others and duck-others), unchanged from Kivy 2.x, and document
  `KIVY_AUDIO_CATEGORY=ambient` prominently for games and apps with only UI
  sounds. When only `avplayer` plays, the session is never set, so Kivy
  applies the same category itself (S3), not only through SDL's hint.
  Media apps that play in the background add the `audio` background mode.

## SP8. Measure on devices

Run on the Android phone (Windows machine) and the iPhone (Mac) with the
`short1` and `tone10` fixtures. Record in `results/sp8/`:

1. Start latency for `sdl3` and the native provider: time from `play()` to
   audio at the output (loopback with a microphone, or an onset detector on
   a recording), 20 trials each.
2. Concurrent sounds: load and play `short1.wav` repeatedly until load or
   play fails; record the count and the error for each provider.
3. Background: play `tone10`, press Home for 5 s, return. Record what is
   heard, `state`, `get_pos()`, and which events fire, with the default
   lifecycle and (Android) with `SDL_HINT_ANDROID_BLOCK_ON_PAUSE=0`.
4. `loop = True` on `short1.wav`: record any gap at the loop point.
5. iOS: confirm the silent switch and Music-app ducking for the default and
   for `ambient`, with `sdl3` loaded and with only `avplayer` loaded (after
   S3, both should match).
