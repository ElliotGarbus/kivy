# Kivy Provider Architecture

## Overview

Kivy uses a **provider architecture** for core functionality like image loading, audio playback, and text rendering. This pluggable system allows Kivy to use different backend implementations based on platform availability, user configuration, and specific requirements.

## Key Concepts

A **provider** is a pluggable implementation of core functionality. For example:
- **Image providers**: PIL/Pillow, SDL3, imageio, FFmpeg
- **Audio output providers**: SDL3, GStreamer, FFmpeg, AVPlayer (iOS/macOS)
- **Text providers**: PIL, SDL3, Pango

This architecture enables:
- Platform-specific optimizations
- Fallback to alternative implementations
- User choice and customization
- Support for different file formats and features

---

## Core Files and Components

### 1. Environment Variable Processing

**File**: `kivy/__init__.py` (lines 208-236)

This file defines `kivy_options`, a dictionary containing default provider lists for each category:

```python
kivy_options = {
    'window': ('egl_rpi', 'sdl3', 'sdl', 'x11'),
    'text': ('pil', 'sdl3', 'sdlttf'),
    'video': ('gstplayer', 'ffmpeg', 'ffpyplayer', 'null'),
    'audio_output': ('gstplayer', 'ffpyplayer', 'sdl3', 'avplayer'),
    'image': ('tex', 'imageio', 'dds', 'sdl3', 'pil', 'ffpy', 'gif'),
    'camera': ('opencv', 'gi', 'avfoundation', 'android', 'picamera'),
    'spelling': ('enchant', 'osxappkit'),
    'clipboard': ('android', 'winctypes', 'xsel', 'xclip', 'dbusklipper', 
                  'nspaste', 'sdl3', 'dummy', 'gtk3')
}

# Read environment variables and override defaults
for option, value in kivy_options.items():
    key = 'KIVY_%s' % option.upper()
    if key in environ:
        if type(value) in {list, tuple}:
            kivy_options[option] = environ[key].split(',')
```

**Environment Variables**:
- `KIVY_IMAGE`: Controls image providers (e.g., `export KIVY_IMAGE=pil,sdl3`)
- `KIVY_AUDIO`: Controls audio output providers
- `KIVY_TEXT`: Controls text providers

These must be set **before** importing Kivy and act as an allowlist - only listed providers will be tried.

### 2. Core Provider Selection Functions

**File**: `kivy/core/__init__.py`

#### `core_select_lib(category, llist, create_instance=False, base='kivy.core', basemodule=None)`

The main function for selecting and loading a single provider (lines 37-103).

**Process**:
1. Iterates through provider list `llist` (tuples of `option, modulename, classname`)
2. Checks if provider is in `kivy_options[category]` (environment variable filtering)
3. Attempts to import the provider module: `kivy.core.{category}.{modulename}`
4. Returns the first successfully loaded provider class
5. Logs all import errors and failures

**Example usage** (from `kivy/core/text/__init__.py`):
```python
label_libs = [('sdl3', 'text_sdl3', 'LabelSDL3'),
              ('pil', 'text_pil', 'LabelPIL')]
Label = core_select_lib('text', label_libs)
```

#### `core_register_libs(category, libs, base='kivy.core')`

Registers multiple providers that can coexist (lines 106-150). Used when you want to load ALL available providers, not just the first one.

**Process**:
1. Filters providers by `kivy_options[category]`
2. Imports each provider module
3. Returns list of successfully loaded providers
4. Logs which providers loaded and which were ignored

**Example usage** (from `kivy/core/image/__init__.py`):
```python
image_libs = [('tex', 'img_tex'), ('dds', 'img_dds'), ...]
libs_loaded = core_register_libs('image', image_libs)
```

### 3. Image Provider Configuration

**File**: `kivy/core/image/__init__.py` (lines 969-984)

**Platform-Specific Decisions**:
```python
image_libs = []

# macOS/iOS specific: Use native ImageIO first
if platform in ('macosx', 'ios'):
    image_libs += [('imageio', 'img_imageio')]

# Universal providers
image_libs += [
    ('tex', 'img_tex'),      # Raw texture format
    ('dds', 'img_dds')]      # DirectDraw Surface

# SDL3 if available (compile-time decision)
if USE_SDL3:
    image_libs += [('sdl3', 'img_sdl3')]

image_libs += [
    ('ffpy', 'img_ffpyplayer'),  # FFmpeg for video frames/animations
    ('pil', 'img_pil')]          # PIL/Pillow

# Register all image loaders
libs_loaded = core_register_libs('image', image_libs)
```

**Available Image Providers**:
- `tex` - Raw texture files (`.tex`)
- `dds` - DirectDraw Surface (`.dds`)
- `sdl3` - SDL3_image (most formats)
- `pil` - PIL/Pillow (extensive format support)
- `imageio` - Native iOS/macOS ImageIO
- `ffpy` - FFmpeg for animated images/videos

**Provider Modules**:
- `kivy/core/image/img_tex.py`
- `kivy/core/image/img_dds.py`
- `kivy/core/image/img_sdl3.py`
- `kivy/core/image/img_pil.py`
- `kivy/core/image/img_imageio.pyx` (Cython)
- `kivy/core/image/img_ffpyplayer.py`

### 4. Audio Output Provider Configuration

**File**: `kivy/core/audio_output/__init__.py` (lines 207-221)

**Platform-Specific Decisions**:
```python
audio_libs = []

# Android: Use native Android audio
if platform == 'android':
    audio_libs += [('android', 'audio_android')]

# macOS/iOS: Use AVPlayer
elif platform in ('macosx', 'ios'):
    audio_libs += [('avplayer', 'audio_avplayer')]

# GStreamer (if available at runtime)
try:
    from kivy.lib.gstplayer import GstPlayer
    audio_libs += [('gstplayer', 'audio_gstplayer')]
except ImportError:
    pass

# FFmpeg audio
audio_libs += [('ffpyplayer', 'audio_ffpyplayer')]

# SDL3 if available (compile-time decision)
if USE_SDL3:
    audio_libs += [('sdl3', 'audio_sdl3')]

libs_loaded = core_register_libs('audio_output', audio_libs)
```

**Available Audio Providers**:
- `android` - Android MediaPlayer API (Android only)
- `avplayer` - AVFoundation (iOS/macOS)
- `gstplayer` - GStreamer (Linux, requires GStreamer installed)
- `ffpyplayer` - FFmpeg (cross-platform)
- `sdl3` - SDL3_mixer (cross-platform)

**Provider Modules**:
- `kivy/core/audio_output/audio_android.py`
- `kivy/core/audio_output/audio_avplayer.py`
- `kivy/core/audio_output/audio_gstplayer.py`
- `kivy/core/audio_output/audio_ffpyplayer.py`
- `kivy/core/audio_output/audio_sdl3.pyx` (Cython)

### 5. Text Provider Configuration

**File**: `kivy/core/text/__init__.py` (lines 1066-1075)

**Platform-Specific Decisions**:
```python
label_libs = []

# Pango (if available at compile-time)
if USE_PANGOFT2:
    label_libs += [('pango', 'text_pango', 'LabelPango')]

# SDL3 TTF (if available at compile-time)
if USE_SDL3:
    label_libs += [('sdl3', 'text_sdl3', 'LabelSDL3')]

# PIL/Pillow (fallback, always available)
label_libs += [('pil', 'text_pil', 'LabelPIL')]

# Select the first available provider
Label = core_select_lib('text', label_libs)
```

**Available Text Providers**:
- `pango` - Pango/FontConfig (advanced text layout, i18n)
- `sdl3` - SDL3_ttf (TrueType fonts via FreeType)
- `pil` - PIL/Pillow (basic text rendering)

**Provider Modules**:
- `kivy/core/text/text_pango.py` (wraps `_text_pango.pyx`)
- `kivy/core/text/text_sdl3.py` (wraps `_text_sdl3.pyx`)
- `kivy/core/text/text_pil.py`

---

## Provider Registration Process

### How Image and Audio Providers Register

Image and audio use `core_register_libs()` because **multiple providers coexist**:

1. **Build platform-specific provider list** based on:
   - Current platform (`platform` variable)
   - Compile-time flags (`USE_SDL3`, `USE_PANGOFT2`)
   - Runtime availability (e.g., GStreamer import check)

2. **Apply environment variable filter**:
   - If `KIVY_IMAGE` set: only those providers are allowed
   - If not set: all importable providers are available

3. **Import each provider module**:
   - Each provider module registers itself with the loader class
   - Example: `ImageLoaderSDL3.register()` adds itself to `ImageLoader._loaders`

4. **Multiple providers remain active**:
   - Image: All providers stay loaded, selected per-file based on extension
   - Audio: All providers stay loaded, selected per-file based on extension

### How Text Providers Are Selected

Text uses `core_select_lib()` because **only one provider is active** at a time:

1. **Build priority list** based on compile-time flags
2. **Try each provider in order** until one succeeds
3. **Return the first working provider class**
4. **That provider handles ALL text rendering** for the application

---

## Platform-Specific Decisions

Platform decisions are made at three levels:

### 1. Compile-Time Decisions

**File**: `kivy/setupconfig.py`

Flags like `USE_SDL3` and `USE_PANGOFT2` are set during Kivy compilation based on:
- Available system libraries
- Build configuration
- Platform requirements

```python
# Example from various __init__.py files
if USE_SDL3:
    libs += [('sdl3', 'provider_sdl3')]
```

### 2. Import-Time Decisions (Platform Detection)

**File**: `kivy/utils.py` (defines `platform` variable)

Platform is detected at import time:
- `'android'` - Android devices
- `'ios'` - iOS devices
- `'macosx'` - macOS
- `'linux'` - Linux
- `'win'` - Windows

```python
from kivy.utils import platform

if platform == 'android':
    audio_libs += [('android', 'audio_android')]
elif platform in ('macosx', 'ios'):
    audio_libs += [('avplayer', 'audio_avplayer')]
```

### 3. Runtime Availability Checks

Some providers are added only if their dependencies are importable:

```python
try:
    from kivy.lib.gstplayer import GstPlayer
    audio_libs += [('gstplayer', 'audio_gstplayer')]
except ImportError:
    pass  # GStreamer not available
```

---

## Provider Selection Priority

The selection priority follows this hierarchy:

### 1. Environment Variables (Highest Priority)
```bash
export KIVY_IMAGE=pil,sdl3
export KIVY_AUDIO=sdl3,ffpyplayer
export KIVY_TEXT=pil
python myapp.py
```

Environment variables **completely replace** the default provider list.

### 2. Platform Defaults (If No Environment Variable Set)

Each category has platform-optimized defaults:

**Image** (macOS/iOS):
1. imageio (native)
2. tex, dds
3. sdl3 (if compiled)
4. ffpy
5. pil

**Image** (other platforms):
1. tex, dds
2. sdl3 (if compiled)
3. ffpy
4. pil

**Audio** (Android):
1. android (native)
2. gstplayer (if available)
3. ffpyplayer
4. sdl3 (if compiled)

**Audio** (macOS/iOS):
1. avplayer (native)
2. gstplayer (if available)
3. ffpyplayer
4. sdl3 (if compiled)

**Audio** (other platforms):
1. gstplayer (if available)
2. ffpyplayer
3. sdl3 (if compiled)

**Text** (any platform):
1. pango (if compiled)
2. sdl3 (if compiled)
3. pil (fallback)

### 3. Per-File Selection (Lowest Level)

For image and audio, the final provider is selected based on:
- File extension matching
- Provider capability for that format
- First provider in the filtered list that can handle the file

---

## Example: Image Loading Flow

```python
from kivy.core.image import Image

img = Image.load('photo.jpg')
```

**Step-by-step process**:

1. **Environment Variable Check** (`kivy/__init__.py`):
   - If `KIVY_IMAGE=pil,sdl3`: only PIL and SDL3 available
   - If not set: all compiled providers available

2. **Provider Registration** (`kivy/core/image/__init__.py`):
   - `core_register_libs('image', image_libs)` loads all available providers
   - Each provider registers itself with `ImageLoader`

3. **File Loading** (`ImageLoader.load()`):
   - Determines file extension: `.jpg`
   - Iterates through registered loaders
   - Asks each: "Can you load `.jpg` files?"
   - First provider that says "yes" loads the file

4. **Provider Selection**:
   - SDL3 provider: "Yes, I can load JPEG" → Tries to load
   - If SDL3 fails: Falls back to next provider (PIL)
   - PIL provider: "Yes, I can load JPEG" → Successfully loads

---

## Summary Table

| Aspect | Image | Audio Output | Text |
|--------|-------|--------------|------|
| **Config File** | `kivy/core/image/__init__.py` | `kivy/core/audio_output/__init__.py` | `kivy/core/text/__init__.py` |
| **Registration Function** | `core_register_libs()` | `core_register_libs()` | `core_select_lib()` |
| **Multiple Providers?** | Yes (per-file selection) | Yes (per-file selection) | No (one active provider) |
| **Environment Variable** | `KIVY_IMAGE` | `KIVY_AUDIO` | `KIVY_TEXT` |
| **Default Providers (Desktop)** | tex, dds, sdl3, ffpy, pil | gstplayer, ffpyplayer, sdl3 | pango, sdl3, pil |
| **Platform-Specific Providers** | imageio (macOS/iOS) | android, avplayer | None |
| **Provider Modules Location** | `kivy/core/image/img_*.py` | `kivy/core/audio_output/audio_*.py` | `kivy/core/text/text_*.py` |

---

## Key Takeaways

1. **Environment variables** (`KIVY_IMAGE`, `KIVY_AUDIO`, `KIVY_TEXT`) processed in `kivy/__init__.py`
2. **Platform decisions** made in provider `__init__.py` files using `platform` variable and compile flags
3. **Provider registration** happens via `core_register_libs()` (image/audio) or `core_select_lib()` (text)
4. **Provider modules** live in `kivy/core/{category}/{category}_{name}.py`
5. **Selection priority**: Environment variables → Platform defaults → Per-file capability
6. **Extensibility**: New providers can be added by creating modules and adding them to the provider lists

