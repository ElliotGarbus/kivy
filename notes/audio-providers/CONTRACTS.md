# Audio provider contracts

The API and behavior every audio provider implements in Kivy 3.0. Both
machines code against this file, so provider work on the Windows machine
doesn't wait for the shared PRs on the Mac to merge. Change a contract only
here, in its own commit, and say so in `README.md` under "Contract changes".

The base-class part (C2-C6) is implemented once, in the Mac PR
`audio-sound-contracts` (`PLAN-MAC.md`, step S1). Until it merges, a provider
branch tests against it by merging `origin/audio-sound-contracts` locally;
the provider PR itself stays based on `upstream/master` and is rebased when
S1 merges.

Names used below:

- **Provider**: a `Sound` subclass registered with `SoundLoader`
  (`sdl3`, `avplayer`, `mediafoundation`, `android`, `gstplayer`).
- **Candidate**: a provider whose `extensions()` contains the file's
  extension (or whose `schemes()` contains the URL's scheme, C6).
- **Constructed**: `Sound.__init__` has returned.

---

## C1. Provider order

Every platform except Android tries `sdl3` first, then its native
secondary, then `gstplayer` when it is importable. Android keeps its
platform player first:

| Platform | Order |
|---|---|
| macOS, iOS | `sdl3`, `avplayer` |
| Windows | `sdl3`, `mediafoundation` (after phase 3), `gstplayer` |
| Linux, Raspberry Pi | `sdl3`, `gstplayer` |
| Android | `android`, `sdl3` |

- Android (decided, O1): SDL3's audio device pauses when the activity
  pauses, while `MediaPlayer` (and Media3) keep playing in the background,
  so `android` stays first, as in Kivy 2.x. SDL3 plays what `android`
  doesn't claim (opus, tracker and game-music formats where the build has
  the decoders). Apps that want SDL3's lower latency for sound effects pass
  `audio_output_provider='sdl3'` or set `KIVY_AUDIO_OUTPUT=sdl3,android`.
- `kivy/core/audio_output/__init__.py` appends in this order.
  `PROVIDER_CONFIGS['audio_output']` in `kivy/core/__init__.py` lists
  `sdl3, avplayer, mediafoundation, android, gstplayer`; the Android
  exception lives in the appends.
- `KIVY_AUDIO_OUTPUT` and `audio_output_provider=` still override it.
- `sdl3` only claims the extensions of decoders its build registered
  (`MIX_GetAudioDecoder`), never `TIMIDITY` or `FLUIDSYNTH` (no MIDI).
- Secondaries keep their full extension lists. They get what SDL3 doesn't
  claim, plus whatever SDL3 claims but fails to open (C2).

## C2. Load failures fall back; only the last failure reaches the app

Defined in `kivy/core/audio_output/__init__.py`:

```python
class SoundLoadError(Exception):
    '''Raised by a provider's load() when it can't open the source.'''
```

Provider side:

- `load()` raises `SoundLoadError(message)` when the source can't be
  opened or decoded. The message names the cause (missing file, no decoder,
  the framework's error text); `SoundLoader` adds the provider name and the
  source.
- A provider that only learns about a failure asynchronously (GStreamer,
  Media Foundation, Media3) does one of the following:
  - **Confirms before returning**: waits in `load()` for "metadata loaded"
    or "error" for a local file, with a timeout of 2 s, and raises on error
    or timeout. Required for `mediafoundation` (`gstplayer` is behind it on
    Windows) and for `android`'s Media3 backend (`sdl3` is behind it on
    Android). `MediaPlayer.prepare()` is already synchronous.
  - **Is the last candidate**: it may return, and report a later failure
    through `on_error` (C3). Allowed for `gstplayer` (last everywhere), and
    for URL sources on any provider, since a scheme has a single provider
    per platform.
- Providers never log a load failure at warning or error level themselves.
  Use `Logger.debug`; `SoundLoader` does the user-facing warning.

`SoundLoader.load()` side:

- Try the candidates in order. On `SoundLoadError` (or any other
  exception, as today), record `(provider name, message)` and try the next.
- If one succeeds, return it. Earlier failures are logged at debug level.
- If all fail, log one warning and return `None`:
  `Audio: Unable to load <source>: sdl3: <msg>; gstplayer: <msg>`.
  If no provider is a candidate, the warning says so:
  `Audio: No provider plays '<ext>' files (<source>)`.
- Strict mode (`KIVY_PROVIDER_STRICT=1`): raise `SoundLoadError` with the
  same text instead of returning `None`.
- With `audio_output_provider=` set, only that provider is tried, as
  today.

`Sound` side, for `source` changed after construction:

- `Sound.on_source` calls `load()`. During construction a
  `SoundLoadError` propagates (so `SoundLoader` can fall back). After
  construction there's nothing to fall back to: catch it and call
  `self._do_error(str(e))` (C3). Track this with a `_constructed` flag set
  at the end of `Sound.__init__`.

## C3. `on_error`

```python
__events__ = ('on_play', 'on_stop', 'on_eos', 'on_error')

def on_error(self, error):
    '''Fired when a loaded sound can't be played, for example because
    decoding fails partway through, the audio device is lost, a stream
    drops, or a new source set on an existing Sound can't be opened.
    `error` is a message. By then the sound is stopped (state is 'stop')
    and on_stop is not dispatched. .. versionadded:: 3.0.0'''

def _do_error(self, error):
    '''For providers: stop native playback first, then call this.
    Logs `error` once at error level, sets state to 'stop' without
    dispatching on_stop, then dispatches on_error.'''
```

- `on_error` is never dispatched for a failure inside `SoundLoader.load()`.
  That path returns `None` (C2).
- A provider calls `_do_error` at most once per failure, and never after
  `unload()`.
- No handler bound: the failure is only logged, nothing is raised.

## C4. End of stream: `on_eos`, then `on_stop`

```python
def on_eos(self):
    '''Fired when the sound reaches its end and isn't looping, just
    before on_stop. .. versionadded:: 3.0.0'''

def _do_eos(self):
    '''For providers: call when playback reached the end with loop False.
    Sets state to 'stop', dispatches on_eos, then on_stop.'''
```

- `loop = True`: playback wraps with no gap where the framework allows it,
  and no `on_eos`, `on_stop` or `on_play` per pass.
- Turning `loop` off while playing lets the current pass finish, then
  `on_eos` and `on_stop`.
- `stop()` while playing or paused dispatches `on_stop` only (no
  `on_eos`). `stop()` while stopped dispatches nothing.
- `play()` while stopped dispatches `on_play`. `play()` while playing or
  paused restarts from the start: `on_stop`, then `on_play` (as #9533
  documents for `sdl3`, and as in Kivy 2.x).

## C5. Pause

```python
state = OptionProperty('stop', options=('stop', 'play', 'pause'))

def pause(self):
    '''Pause a playing sound, keeping its position. Does nothing unless
    state is 'play'. .. versionadded:: 3.0.0'''

def resume(self):
    '''Continue a paused sound from where it was paused. Does nothing
    unless state is 'pause'. .. versionadded:: 3.0.0'''
```

- The base methods only change `state`. Providers override them, act on
  the framework (`MIX_PauseTrack`/`MIX_ResumeTrack`, `AVAudioPlayer.pause`/
  `play`, `IMFMediaEngine::Pause`/`Play`, GStreamer `PAUSED`/`PLAYING`,
  `MediaPlayer.pause`/`start`, ExoPlayer `pause`/`play`), then call
  `super()`.
- No new events. Apps bind `state`.
- While paused: `get_pos()` returns the paused position, `seek()` moves it,
  `volume` and `pitch` changes apply on resume at the latest.
- `stop()` from `'pause'` rewinds and dispatches `on_stop`.
- A provider that can't pause natively doesn't exist after phase 4. The
  shared test suite fails such a provider.

## C6. URL sources are routed by scheme

```python
@staticmethod
def schemes():
    '''URL schemes this provider opens, besides local files.
    Default: (). .. versionadded:: 3.0.0'''
    return ()
```

- `SoundLoader.load()` treats a source matching
  `^([A-Za-z][A-Za-z0-9+.-]*)://` as a URL, except `file://`, which is a
  local file. A URL goes to the providers whose `schemes()` contains its
  scheme (lower case), in C1 order, without checking the extension. C2
  fallback applies among them.
- No provider for the scheme: one warning
  (`Audio: No provider opens '<scheme>' URLs (<source>)`) and `None`.
- Today's `content://` special case becomes `android`'s
  `schemes() == ('content',)`. Phase 6 adds `http`/`https` (and others)
  to the providers that stream. `sdl3` never declares a scheme.
- `resource_find()` is skipped for URLs, as it is for `content://` today.

## C7. Properties every provider honors

| Property | Rule |
|---|---|
| `volume` | 0 to 1, applied while playing and before play |
| `pitch` | Speed factor. Document per provider what happens to pitch: `sdl3` and `gstplayer` change speed and pitch together; `avplayer`, `mediafoundation` and Media3 change speed and keep pitch; `MediaPlayer` changes speed and keeps pitch (`PlaybackParams.setSpeed`, API 23+) |
| `length` | Seconds, exact to 50 ms for the fixtures (C8). 0 only if the format has no duration (raw ADTS `.aac` is about 1 s off on Apple; record such cases in the provider's docstring) |
| `get_pos()` | Seconds while playing or paused, 0 when stopped |
| `seek(s)` | Works while playing and while paused. While stopped, providers may ignore it (unchanged from today; documented) |
| `loop` | C4 |

## C8. Shared behavior test suite

- File: `kivy/tests/test_audio_behavior.py`. Fixtures:
  `kivy/tests/audio_fixtures/`.
- Fixtures (generated by `notes/audio-providers/tools/make_fixtures.py`,
  step M0): `short1.<ext>` (1.0 s, 440 Hz, 48 kHz mono) and `corrupt.<ext>`
  (4 KiB of random bytes after a valid-looking header where cheap), for
  `wav flac mp3 ogg opus aiff m4a aac caf`. `wma` is added on Windows if a
  generator is at hand.
- Parametrized over `SoundLoader.available_providers()` and the fixtures
  each provider claims. Each test loads with `audio_output_provider=name`.
- Skipped when `KIVY_TEST_AUDIO=0`, or when the provider can't open an
  audio device (detect once per provider; skip with the reason).
- Tests, with tolerances for polling providers (end-of-sound events within
  0.3 s of `length`):
  1. Load returns a `Sound` of that provider; `length` is within 50 ms of
     1.0 (`aac` within 1.1 s, see C7).
  2. `play()`: `state == 'play'`, one `on_play`. Reaching the end: one
     `on_eos`, then one `on_stop`, `state == 'stop'`.
  3. `loop = True` for 2.5 s: no events per pass, `get_pos()` wraps;
     `stop()`: one `on_stop`, no `on_eos`.
  4. `pause()` at 0.3 s: `state == 'pause'`, `get_pos()` stays within
     50 ms for 0.3 s; `resume()`: position advances; reaches the end
     with one `on_eos`.
  5. `seek(0.5)` while playing and while paused: `get_pos()` within 0.1 s.
  6. `volume` and `pitch` set before and during play: no error.
  7. The provider's `load()` raises `SoundLoadError` for `corrupt.<ext>`.
     `SoundLoader.load()` returns `None` and logs one warning naming every
     candidate.
  8. Setting `source` of a loaded, stopped sound to `corrupt.<ext>`: one
     `on_error`, `state == 'stop'`, no `on_stop`, and `play()` afterwards
     does nothing. (On a playing sound, `on_source` unloads first, which
     dispatches `on_stop` before `on_error`.)
  9. An unknown scheme (`foo://x.wav`) returns `None` with the C6 warning.
- Providers that don't meet the contract yet are listed in
  `_KNOWN_FAILURES = {'provider': {'test_name', ...}}` at the top of the
  file and marked `xfail(strict=True)`. Each provider PR removes its own
  entries. That dict is the only shared line both machines edit; rebase
  conflicts there are expected and trivial.
- The suite drives the Kivy clock with the `kivy_clock` fixture pattern
  already used in `kivy/tests/test_audio.py`, polling with a deadline
  rather than fixed sleeps.

## Open questions

Decide these before the PR named next to each merges.

- **O1. Android background playback. Decided (2026-11-24): keep `android`
  first on Android** (C1). With `sdl3` first, wav/mp3/ogg would have played
  through SDL3, whose audio device pauses when the activity pauses (#9500's
  routing comment), while `MediaPlayer` keeps playing in the background.
- **O2. AIFF through SDL3. Decided (2026-11-24): the rule below; W1
  supplies the measurement.** SDL3_mixer 3.2.0's AIFF decoder reported no
  duration in the rubicon spike. The Apple builds don't include it; the
  Linux build does. W1 checks `length` for `short1.aiff` on each platform.
  If `length` is wrong, don't map `AIFF` in `sdl3` (the secondary plays
  it), and note the upstream bug in the M1 PR.
- **O3. `AVAudioPlayer.play()` off the main thread (before S3 merges).**
  Apple doesn't document `AVAudioPlayer` as thread-safe. S3 measures
  whether calling `play()` on a serial dispatch queue is reliable before
  shipping it.
