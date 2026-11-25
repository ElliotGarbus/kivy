# Audio provider contracts

The API and behavior every audio provider implements in Kivy 3.0. Both
machines code against this file, so provider work on the Windows machine
doesn't wait for the shared PRs on the Mac to merge. Change a contract only
here, in its own commit, and say so in `README.md` under "Contract changes".

The base-class part (C2-C6, C9) is implemented once, in the Mac PR
`audio-sound-contracts` (`PLAN-MAC.md`, step S1). Until it merges, a
provider branch tests against it by merging `origin/audio-sound-contracts`
locally; the provider PR itself stays based on `upstream/master` and is
rebased when S1 merges.

Names used below:

- **Provider**: a `Sound` subclass registered with `SoundLoader`
  (`sdl3`, `avplayer`, `mediafoundation`, `android`, `gstplayer`).
- **Candidate**: a provider whose `extensions()` contains the file's
  extension. URLs have a single owner instead (C6).
- **Constructed**: `Sound.__init__` has returned.
- **Platform**: `kivy.utils.platform` (`'win'`, `'linux'`, `'macosx'`,
  `'ios'`, `'android'`). Never `sys.platform` (`'win32'`).

---

## C1. Provider order

`sdl3` first on every platform, then the platform's native provider, then
`gstplayer` where its runtime is importable:

| Platform | Order |
|---|---|
| macOS, iOS | `sdl3`, `avplayer` |
| Windows | `sdl3`, `mediafoundation` (after phase 3), `gstplayer` |
| Linux, Raspberry Pi | `sdl3`, `gstplayer` |
| Android | `sdl3`, `android` |

- How the order is set: `core_register_libs()` imports providers in the
  order of `kivy.kivy_options['audio_output']`, which is
  `PROVIDER_CONFIGS['audio_output']` in `kivy/core/__init__.py` unless
  `KIVY_AUDIO_OUTPUT` replaces it. The appends in
  `kivy/core/audio_output/__init__.py` only decide which modules a
  platform can import; their order has no effect. Each provider registers
  with `SoundLoader` when its module is imported, so `SoundLoader._classes`
  follows `PROVIDER_CONFIGS`.
- One global list gives every row of the table, because each platform only
  has its own subset of modules:
  - after M1 (phase 1): `sdl3, avplayer, android, gstplayer`;
  - after W3 (phase 3): `sdl3, avplayer, mediafoundation, android,
    gstplayer`.
- Android (decided 2026-11-25, O1): `sdl3` first. Sounds pause with the app
  and resume with it, start with lower latency, have no per-sound player
  limit, and `pitch` and `loop` behave as elsewhere. `android` plays what
  SDL3 doesn't claim (m4a, aac, 3gp, MIDI and ringtone formats) and
  `content://`. Background playback needs a foreground service and media
  session whatever the order; see `MOBILE-AUDIO.md`.
- `KIVY_AUDIO_OUTPUT` replaces the list; `audio_output_provider=` puts one
  provider first for one load (C2).
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
    Windows). `android`'s Media3 backend also confirms for local files, so
    it behaves like the `MediaPlayer` backend (`prepare()` is synchronous):
    `length` is known when `load()` returns and a bad file gives `None`.
  - **Is the last candidate**: it may return, and report a later failure
    through `on_error` (C3). Allowed for `gstplayer` (last everywhere), and
    for every URL, since a URL has exactly one owner (C6).
- Providers never log a load failure at warning or error level themselves.
  Use `Logger.debug`; `SoundLoader` does the user-facing warning.

`SoundLoader.load()` side:

- Try the candidates in C1 order. On `SoundLoadError` (or any other
  exception, as today), record `(provider name, message)` and try the next.
- If one succeeds, return it. Earlier failures are logged at debug level.
- If all fail, log one warning and return `None`:
  `Audio: Unable to load <source>: sdl3: <msg>; gstplayer: <msg>`.
  If no provider is a candidate, the warning says so:
  `Audio: No provider plays '<ext>' files (<source>)`.
- Strict mode (`KIVY_PROVIDER_STRICT=1`): raise `SoundLoadError` with the
  same text instead of returning `None`.

`audio_output_provider=name` is a preference, as it is today in
`load_with_provider_selection()`, not an exclusive choice:

- `name` is tried first. If it's unknown, isn't a candidate for the file,
  or fails to load, `SoundLoader` logs a warning naming the reason and
  continues with the default order, skipping `name` (today's fallback may
  try it a second time; S1 fixes that). The aggregated warning, if every
  candidate fails, includes `name`'s failure.
- Strict mode: an unknown or incompatible `name` raises `ValueError`, and a
  failure raises `SoundLoadError`, with no fallback (as today).
- For a URL, a `name` whose `schemes()` contains the scheme becomes the
  URL's owner for that load (C6); otherwise the platform's owner is used.
- Tests that need one specific provider construct its class directly
  (C8), not through `audio_output_provider=`, because the preference falls
  back.

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

- `on_error` is never dispatched for a failure inside `SoundLoader.load()`
  by a provider that confirms before returning. That path returns `None`
  (C2). The last candidate may dispatch it later (C2).
- A provider calls `_do_error` at most once per failure, and never after
  `unload()` (C9).
- No handler bound: the failure is only logged, nothing is raised.

## C4. State transitions, end of stream

```python
def on_eos(self):
    '''Fired when the sound reaches its end and isn't looping, just
    before on_stop. .. versionadded:: 3.0.0'''

def _do_eos(self):
    '''For providers: call when playback reached the end with loop False.
    Sets state to 'stop', dispatches on_eos, then on_stop.'''
```

Every transition, with the events it dispatches:

| From | Call or event | To | Events, in order | Native action |
|---|---|---|---|---|
| stop | `play()` | play | `on_play` | start from position 0 |
| play | `play()` | play | `on_stop`, `on_play` | rewind to 0 and start; never continue from the current position |
| pause | `play()` | play | `on_stop`, `on_play` | rewind to 0 and start; `resume()` is the call that continues |
| play | `pause()` | pause | none | pause, keep position (C5) |
| pause | `resume()` | play | none | continue from the paused position (C5) |
| play, pause | `stop()` | stop | `on_stop` | stop and rewind |
| stop | `stop()` | stop | none | nothing |
| play | end, `loop` False | stop | `on_eos`, `on_stop` | `_do_eos()` |
| play | end, `loop` True | play | none | wrap to 0 with no gap where the framework allows it |
| play, pause | failure | stop | `on_error` | `_do_error()` (C3) |
| any | `unload()` | stop | `on_stop` if it was play or pause | C9 |

- Base class (S1): `Sound.stop()` returns without dispatching when `state`
  is already `'stop'`; otherwise it sets `'stop'` and dispatches
  `on_stop`. `Sound.play()` sets `'play'` and dispatches `on_play`.
- Providers: `play()` calls `self.stop()` first when `state` is `'play'`
  or `'pause'` (which dispatches `on_stop` through the base), then starts
  natively from position 0, then calls `super().play()`. `stop()` stops
  natively, then calls `super().stop()`. A provider whose framework
  resumes on "start" (`MediaPlayer.start()`, `AVAudioPlayer.play`,
  GStreamer `PLAYING`, ExoPlayer `play()`) seeks to 0 first.
- Turning `loop` off while playing lets the current pass finish, then
  `on_eos` and `on_stop`.
- `on_stop` semantics match #9533 for `sdl3` and Kivy 2.x.

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
- A provider that can't pause natively doesn't exist after phase 4. The
  shared test suite fails such a provider.

## C6. Sources: local paths, `file://` URLs, other URLs

```python
@staticmethod
def schemes():
    '''URL schemes this provider opens, besides local files.
    Default: (). .. versionadded:: 3.0.0'''
    return ()
```

`SoundLoader.load()` classifies the source before anything else, including
`resource_find()`:

1. **`file://` URLs** become local paths, then are treated exactly like a
   path the app passed (`resource_find()`, extension, candidates):
   - Split with `urllib.parse.urlsplit`; percent-decode the path
     (`urllib.parse.unquote`).
   - Host empty or `localhost`: the path is local. `file:///home/a/b.wav`
     becomes `/home/a/b.wav`; `file://localhost/home/a%20b.wav` becomes
     `/home/a b.wav`.
   - On Windows (platform `'win'`): `file:///C:/Music/a.wav` becomes
     `C:\Music\a.wav` (drop the slash before the drive letter, use
     backslashes); `file://server/share/a.wav` becomes
     `\\server\share\a.wav` (UNC).
   - Any other host on other platforms: warning
     (`Audio: Unsupported file URL host '<host>' (<source>)`) and `None`.
   - The extension comes from the converted path.
   - `urllib.request.url2pathname` does most of this; the tests (S1) pin
     each case above on every platform by patching `platform`, so the
     result doesn't depend on the OS running the test.
2. **Other URLs**: a source matching `^([A-Za-z][A-Za-z0-9+.-]*)://`. The
   scheme (lower-cased) selects the **owner**: the first provider in C1
   order whose `schemes()` contains it, or the `audio_output_provider=`
   provider when it declares the scheme (C2). Only the owner is tried; there
   is no fallback among providers for URLs. The extension is not checked,
   and `resource_find()` is skipped.
3. **Everything else** is a local path, as today.

- No owner for the scheme: one warning
  (`Audio: No provider opens '<scheme>' URLs (<source>)`) and `None`.
- Owners per platform after phase 6:

  | Scheme | macOS, iOS | Windows | Linux, Pi | Android |
  |---|---|---|---|---|
  | `content` | | | | `android` |
  | `http`, `https` | `avplayer` | `mediafoundation` | `gstplayer` | `android` (Media3 backend) |
  | `rtsp` | | `gstplayer` | `gstplayer` | |

  On Windows, `gstplayer` also declares `http`/`https` but isn't their
  owner, because `mediafoundation` comes first; apps that want GStreamer
  for a stream pass `audio_output_provider='gstplayer'` or reorder with
  `KIVY_AUDIO_OUTPUT`.
- Today's `content://` special case becomes `android`'s
  `schemes() == ('content',)`. `sdl3` never declares a scheme.

## C7. Properties every provider honors

| Property | Rule |
|---|---|
| `volume` | 0 to 1, applied while playing and before play |
| `pitch` | Speed factor. Document per provider what happens to pitch: `sdl3` and `gstplayer` change speed and pitch together; `avplayer`, `mediafoundation` and Media3 change speed and keep pitch; `MediaPlayer` changes speed and keeps pitch (`PlaybackParams.setSpeed`, API 23+). Values outside a framework's range are clamped (for example `AVAudioPlayer` 0.5 to 2), logged at debug |
| `length` | Seconds, exact to 50 ms for the fixtures (C8), as soon as `load()` returns for providers that confirm before returning. Providers allowed to return early (C2) report 0 until known. 0 for live streams, and if the format has no duration (raw ADTS `.aac` is about 1 s off on Apple; record such cases in the provider's docstring) |
| `get_pos()` | Seconds while playing or paused, 0 when stopped |
| `seek(s)` | Works while playing and while paused. While stopped, providers may ignore it (unchanged from today; documented). Ignored for live streams (debug log) |
| `loop` | C4 |

## C8. Tests

Two files, so contract tests run on every CI job and only playback needs a
device.

### Contract tests (no audio device): `kivy/tests/test_audio_contracts.py` (S1)

Fake providers (subclasses of `Sound` whose `load()` raises, succeeds, or
reports later through `_do_error`/`_do_eos`) registered on a saved and
restored `SoundLoader._classes`. Never skipped. Covers:

- C1: `PROVIDER_CONFIGS['audio_output']` order, and that
  `core_register_libs()` registers in `kivy_options` order whatever the
  append order (patch `kivy_options` and the module list).
- C2: fallback order; one aggregated warning naming every candidate;
  `None` versus strict-mode raise; "no provider plays" warning; the
  `audio_output_provider=` preference (tried first, then fallback without
  retrying it; unknown and incompatible names; strict mode); an
  asynchronous last candidate returns a `Sound` and its later failure
  dispatches `on_error`, not a warning from `SoundLoader`; `on_source`
  after construction dispatches `on_error`.
- C3, C4: every row of the C4 table with a fake provider: events, their
  order, and `state`.
- C6: each `file://` case from C6 under patched platforms; URL owner
  selection, including Windows `http` going to the first declaring
  provider and the preference overriding it; no fallback among URL
  providers; unknown scheme warning; `content://` goes to `android`
  (extend `TestContentUriRouting`).
- C9: callbacks posted after `unload()` are dropped; `unload()` twice is
  harmless; `unload()` during an asynchronous load drops the late result.
- Real providers, where no device is needed: each provider's `load()`
  raises `SoundLoadError` for a missing file (`sdl3` only if its mixer
  opens; S2 checks whether `SDL_AUDIO_DRIVER=dummy` makes it open on CI).

### Behavior tests (audio device): `kivy/tests/test_audio_behavior.py` (S2)

- Fixtures in `kivy/tests/audio_fixtures/` (generated by
  `notes/audio-providers/tools/make_fixtures.py`, step M0): `short1.<ext>`
  (1.0 s, 440 Hz, 48 kHz mono), `notaudio.<ext>` (random bytes from the
  first byte) and `damaged.<ext>` (a valid header, then random bytes), for
  `wav flac mp3 ogg opus aiff m4a aac caf`. `wma` is added on Windows if a
  generator is at hand.
- Parametrized over `SoundLoader._classes` and the fixtures each provider
  claims. Each test constructs the provider class directly
  (`cls(source=path)`), so a failing provider can't fall back to another.
- Skipped per provider when `KIVY_TEST_AUDIO=0` or when the provider can't
  open an audio device (detect once per provider; skip with the reason).
  CI must still run the contract tests above; S2 checks the skip count on
  each CI job so a whole provider doesn't silently disappear.
- Tests, with tolerances for polling providers (end-of-sound events within
  0.3 s of `length`):
  1. Load returns a `Sound`; `length` is within 50 ms of 1.0 (`aac` within
     1.1 s, see C7; early-returning providers: once known, within 2 s).
  2. `play()`: `state == 'play'`, one `on_play`. Reaching the end: one
     `on_eos`, then one `on_stop`, `state == 'stop'`.
  3. `loop = True` for 2.5 s: no events per pass, `get_pos()` wraps;
     `stop()`: one `on_stop`, no `on_eos`.
  4. `pause()` at 0.3 s: `state == 'pause'`, `get_pos()` stays within
     50 ms for 0.3 s; `resume()`: position advances; reaches the end
     with one `on_eos`.
  5. `seek(0.5)` while playing and while paused: `get_pos()` within 0.1 s.
  6. `volume` and `pitch` set before and during play: no error.
  7. `notaudio.<ext>`:
     - providers that confirm before returning: `cls(source=...)` raises
       `SoundLoadError`;
     - providers allowed to return early (`ASYNC_LOAD_PROVIDERS =
       {'gstplayer'}` at the top of the file, matching C2): construction
       returns; within 5 s, one `on_error`, `state == 'stop'`, no
       `on_stop`, and no `on_eos`.
  8. Setting `source` of a loaded, stopped sound to `notaudio.<ext>`: one
     `on_error`, `state == 'stop'`, no `on_stop`, and `play()` afterwards
     does nothing. (On a playing sound, `on_source` unloads first, which
     dispatches `on_stop` before `on_error`.)
  9. Restart (C4): at 0.5 s into play, `play()` again: one `on_stop`, then
     one `on_play`, and `get_pos()` below 0.2 s. The same from `'pause'`.
     `stop()` while stopped: no event.
  10. Lifecycle (C9): `unload()` while playing, then wait 1.5 s: one
      `on_stop` at most, no other event, `state == 'stop'`. `unload()`
      immediately after construction, then wait 1.5 s: no event. 50 cycles
      of construct, `play()`, `unload()`: no exception, and no event after
      each `unload()`.
  11. `damaged.<ext>`: defined after SP1 (O4).
- Known failures: `_KNOWN_FAILURES` at the top of the file is a set of
  `(provider, platform, ext, test)` tuples, where `platform` and `ext` may
  be `'*'`; for example `('gstplayer', 'win', '*', 'test_loop')`. A match
  is marked `xfail(strict=True)`, so a fix that makes it pass fails the run
  until the entry is removed. Each provider PR removes its own entries.
  That set is the only shared line both machines edit; rebase conflicts
  there are expected and trivial.
- The suite drives the Kivy clock with the `kivy_clock` fixture pattern
  already used in `kivy/tests/test_audio.py`, polling with a deadline
  rather than fixed sleeps.

## C9. Lifecycle and native cleanup

- `unload()` (also called by `on_source` before loading a new source; a
  provider holding native objects also releases them from `__dealloc__` or
  `__del__`, without dispatching events) does, in order:
  1. If `state` is `'play'` or `'pause'`, stops natively and calls
     `Sound.stop()` (one `on_stop`).
  2. Cancels every Clock event the provider scheduled (polls, deferred
     callbacks).
  3. Detaches native callbacks: listeners, delegates, notification
     observers, stopped-callbacks.
  4. Releases native objects (players, engines, pipelines, tracks), on the
     thread the framework requires.
  5. Increments `self._generation`.
- After `unload()`, no event is dispatched for the old source, whatever
  arrives late from a native thread. Base helper (S1):
  `self._post(callback, *args)` schedules `callback` on Kivy's thread and
  drops it if `_generation` changed in between. Providers deliver every
  native callback through it.
- `unload()` is idempotent, and safe during an asynchronous load (the
  late "ready" or "error" is dropped).
- Repeated construct/`unload()` must not grow native resources: the
  provider's step checks a count it can observe (open tracks, live
  players, pipeline objects) after 50 cycles.

## Open questions

Decide these before the PR named next to each merges.

- **O1. Android order. Decided (2026-11-25): `sdl3` first** (C1). The
  first decision (2026-11-24, `android` first) assumed `android` was first
  on Android in Kivy 2.x and on master. It wasn't: in 2.x it was never
  loaded by default, and on master `sdl3` is ahead of it. The comparison
  that settled it is `MOBILE-AUDIO.md`.
- **O2. AIFF through SDL3. Decided (2026-11-24): the rule below; W1
  supplies the measurement.** SDL3_mixer 3.2.0's AIFF decoder reported no
  duration in the rubicon spike. The Apple builds don't include it; the
  Linux build does. W1 checks `length` for `short1.aiff` on each platform.
  If `length` is wrong, don't map `AIFF` in `sdl3` (the secondary plays
  it), and note the upstream bug in the M1 PR.
- **O4. Files with a valid header and damaged data (before S1 starts).**
  `sdl3` decodes during playback (`predecode=False`), and other frameworks
  may read only the header when opening, so C2's "`load()` raises" may only
  hold for files that aren't audio at all. SP1 (Mac) and W-SP1 (Windows)
  record what each provider does with `damaged.<ext>`. Then decide: is it
  enough that `notaudio` falls back and `damaged` ends in `on_error` (or
  plays to an early end), or should some providers check more at load (for
  example, `predecode=True` for short files)? Update C2 and C8 test 11.
- **O3. `AVAudioPlayer.play()` off the main thread (before S3 starts).**
  Apple doesn't document `AVAudioPlayer` as thread-safe. SP3 measures
  whether calling `play()` on a serial dispatch queue is reliable before
  shipping it.
