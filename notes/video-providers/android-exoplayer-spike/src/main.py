"""Android ExoPlayer spike.

Validates the plumbing the Kivy 3.0 ``video_android`` rewrite needs before
any of it is written against the real ``VideoBase`` API:

- androidx.media3 classes resolve via pyjnius ``autoclass`` (Maven channel).
- A ``PythonJavaClass`` can implement ``Player.Listener`` and receive
  ExoPlayer's callbacks.
- ExoPlayer can render into a ``SurfaceTexture`` bound to a Kivy GL texture
  id (the same zero-copy path the old ``MediaPlayer``-based provider uses),
  and that texture can be drawn through an FBO with the external-texture
  shader for rotation.
- The play / pause / stop / seek / end-of-stream lifecycle, and error
  reporting, all work when driven from Python.

Each checkpoint is printed with a ``EXOPLAYER_*`` marker (flushed, so it
reaches ``adb logcat`` promptly) for unattended verification. The app also
auto-plays the bundled clip on start, with on-screen buttons for manual
poking.

Key difference from the old ``MediaPlayer`` provider this informs: ExoPlayer
is asynchronous. ``MediaPlayer.prepare()`` blocks and
``getVideoWidth()``/``getVideoHeight()`` are available right after it
returns; ExoPlayer's ``prepare()`` returns immediately and the video size
(plus any rotation) only arrives later, via ``onVideoSizeChanged`` on the
listener. The real provider's ``load()`` can no longer create the texture
synchronously -- texture/FBO setup has to happen from that callback, which
also means ``on_load`` fires from there rather than from ``load()`` itself.

ExoPlayer is also far stricter than MediaPlayer about threading, and this is
the spike's main finding. A Player has a single "application thread", fixed
when it is built (``Builder.setLooper(...)``), and every single call into
it after that -- not just listener registration -- throws
``IllegalStateException`` if made from any other thread. Kivy's Python
thread on Android (``SDLThread``) is not, and cannot become, that thread: it
has no ``Looper`` of its own and is busy running Kivy's own loop, so it
can't run ``Looper.loop()`` either. The player is therefore built with
``Builder.setLooper(Looper.getMainLooper())``, making Android's real UI
thread (``SDLActivity``) the application thread, and every ``Player`` method
call -- ``setMediaItem``, ``prepare``, play/pause/stop, ``seekTo``,
``setVideoSurface``, ``release``, all of it -- is bounced onto that thread
with kivyforge's ``android.runnable`` (python-for-android's
``run_on_ui_thread`` machinery). Listener callbacks arrive on that same UI
thread for the same reason, and are bounced back onto Kivy's own thread with
Kivy's ``@mainthread`` before touching any Kivy state -- the reverse hop,
needed because Kivy objects aren't thread-safe either. This double hop (UI
thread for the player, Kivy thread for Kivy) is the shape the real
provider's rewrite will have to take; the old ``MediaPlayer``-based provider
never needed it because ``MediaPlayer`` has no equivalent thread check.
"""

import math
import os

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.graphics import Callback, Color, Rectangle
from kivy.graphics.fbo import Fbo
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.properties import StringProperty
from kivy.uix.widget import Widget
from kivy.utils import platform

CLIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips", "clip.mp4")

KV = """
FloatLayout:
    VideoSurface:
        id: surface
    BoxLayout:
        size_hint: (1, None)
        height: '48dp'
        pos_hint: {'x': 0, 'y': 0}
        padding: '4dp'
        spacing: '4dp'
        canvas.before:
            Color:
                rgba: (0, 0, 0, 0.5)
            Rectangle:
                pos: self.pos
                size: self.size
        Button:
            text: 'Play'
            on_release: app.player_play()
        Button:
            text: 'Pause'
            on_release: app.player_pause()
        Button:
            text: 'Stop'
            on_release: app.player_stop()
        Button:
            text: '+10s'
            on_release: app.player_seek_forward()
    Label:
        id: status
        text: app.status_text
        pos_hint: {'x': 0, 'top': 1}
        size_hint: (1, None)
        height: '96dp'
        text_size: (self.width, None)
        halign: 'left'
        valign: 'top'
        padding: ('8dp', '8dp')
        canvas.before:
            Color:
                rgba: (0, 0, 0, 0.5)
            Rectangle:
                pos: self.pos
                size: self.size
"""

# Same external-texture rotation shader the MediaPlayer-based provider uses;
# the video frame lands in an OES external texture (texture1) and is drawn
# into a regular 2D texture (the FBO) that Kivy can treat normally.
FS_EXTERNAL_OES = """
#extension GL_OES_EGL_image_external : require
#ifdef GL_ES
    precision highp float;
#endif

varying vec4 frag_color;
varying vec2 tex_coord0;

uniform sampler2D texture0;
uniform samplerExternalOES texture1;
uniform vec2 resolution;
uniform float angle;

vec2 rotate(vec2 uv, float a) {
    float s = sin(a);
    float c = cos(a);
    mat2 m = mat2(c, -s, s, c);
    return m * (uv - 0.5) + 0.5;
}

void main() {
    vec2 uv = clamp(rotate(tex_coord0, angle), 0.0, 1.0);
    gl_FragColor = texture2D(texture1, uv);
}
"""


class VideoSurface(Widget):
    """Draws whatever texture ``app`` hands it, full-size."""

    def set_texture(self, texture):
        self.canvas.clear()
        if texture is None:
            return
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=texture, pos=self.pos, size=self.size)


class ExoPlayerCheckApp(App):
    # A plain instance attribute wouldn't be observable by the `text:
    # app.status_text` KV binding -- it needs a real Kivy property to
    # push updates into the UI.
    status_text = StringProperty("starting...")

    player = None
    _listener = None
    _surface_texture = None
    _surface = None
    _video_texture = None
    _fbo = None
    _texture_cb = None
    _rotation = 0
    _resolution = (0, 0)
    _frame_count = 0
    _reported_first_frame = False

    def build(self):
        self.status_text = "starting..."
        self._root = Builder.load_string(KV)
        return self._root

    def on_start(self):
        if platform != "android":
            self.status_text = "ExoPlayer check needs Android; nothing to do here."
            Logger.warning("ExoPlayerCheck: not running on Android, skipping")
            return
        self._setup_player()

    # -- setup ---------------------------------------------------------

    def _setup_player(self):
        from android.runnable import Runnable
        from jnius import PythonJavaClass, autoclass, java_method

        self._Runnable = Runnable

        # Builder is a static nested class; pyjnius needs its own autoclass
        # (the Java `$` nested-class name), it isn't an attribute of
        # ExoPlayer the way it would be from Java source.
        ExoPlayerBuilder = autoclass("androidx.media3.exoplayer.ExoPlayer$Builder")
        MediaItem = autoclass("androidx.media3.common.MediaItem")
        Uri = autoclass("android.net.Uri")
        File = autoclass("java.io.File")
        Looper = autoclass("android.os.Looper")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        self._Player = autoclass("androidx.media3.common.Player")
        self._C = autoclass("androidx.media3.common.C")

        print("EXOPLAYER_AUTOCLASS_OK", flush=True)

        app = self

        class PlayerListener(PythonJavaClass):
            __javainterfaces__ = ["androidx/media3/common/Player$Listener"]
            __javacontext__ = "app"

            @java_method("(I)V")
            def onPlaybackStateChanged(self, state):
                app._on_playback_state_changed(state)

            @java_method("(Landroidx/media3/common/PlaybackException;)V")
            def onPlayerError(self, error):
                app._on_player_error(error)

            @java_method("(Landroidx/media3/common/VideoSize;)V")
            def onVideoSizeChanged(self, video_size):
                # Spike finding: never observed firing (see module docstring
                # / findings doc) even though onTracksChanged, on the same
                # interface, does. Kept in case a pyjnius/media3 update
                # fixes it; _on_tracks_changed below is the path that
                # actually works and is what drives texture setup.
                app._on_video_size_changed(video_size)

            @java_method("(Z)V")
            def onIsPlayingChanged(self, is_playing):
                app._on_is_playing_changed(is_playing)

            @java_method("(Landroidx/media3/common/Tracks;)V")
            def onTracksChanged(self, tracks):
                app._on_tracks_changed(tracks)

        self._listener = PlayerListener()
        print("EXOPLAYER_LISTENER_CREATED", flush=True)

        context = PythonActivity.mActivity
        builder = ExoPlayerBuilder(context)
        # Fix the player's application thread to Android's real UI thread
        # (see module docstring): every Player method call from here on,
        # including the ones a few lines down, must be bounced there.
        builder.setLooper(Looper.getMainLooper())
        self.player = builder.build()
        self.player.addListener(self._listener)

        media_item = MediaItem.fromUri(Uri.fromFile(File(CLIP_PATH)))

        def _load_and_play():
            self.player.setMediaItem(media_item)
            self.player.prepare()
            self.player.setPlayWhenReady(True)

        self._ui(_load_and_play)

        self.status_text = f"loading {os.path.basename(CLIP_PATH)}..."
        Clock.schedule_interval(self._update, 1 / 30.0)

    def _ui(self, fn):
        """Run fn on Android's UI thread (the player's application thread).

        Fire-and-forget: python-for-android's Runnable doesn't return a
        value back. Good enough for the mutating calls this spike makes;
        the real provider will need a synchronous variant for anything
        that reads the player back (position, duration).
        """
        self._Runnable(fn)()

    # -- listener callbacks (arrive on Android's main thread) ----------

    @mainthread
    def _on_tracks_changed(self, tracks):
        """The callback that actually works for sizing the texture.

        ``onVideoSizeChanged`` never fired in testing (see docstring), so
        the video track's ``Format`` -- same width/height/rotation, read
        from ``onTracksChanged`` instead -- is what drives surface setup.
        """
        for group in tracks.getGroups():
            if group.getType() != self._C.TRACK_TYPE_VIDEO:
                continue
            for i in range(group.length):
                fmt = group.getTrackFormat(i)
                width, height, rotation = fmt.width, fmt.height, fmt.rotationDegrees
                if width <= 0 or height <= 0 or (width, height) == self._resolution:
                    continue
                self._resolution = (width, height)
                self._rotation = rotation
                Logger.info(f"ExoPlayerCheck: video format {width}x{height} rot={rotation}")
                self._build_surface(width, height, rotation)
                return

    @mainthread
    def _on_video_size_changed(self, video_size):
        width = video_size.width
        height = video_size.height
        rotation = video_size.unappliedRotationDegrees
        if width <= 0 or height <= 0:
            return
        if (width, height) == self._resolution:
            return
        self._resolution = (width, height)
        self._rotation = rotation
        Logger.info(f"ExoPlayerCheck: video size {width}x{height} rot={rotation}")
        self._build_surface(width, height, rotation)

    def _build_surface(self, width, height, rotation):
        from jnius import autoclass

        SurfaceTexture = autoclass("android.graphics.SurfaceTexture")
        Surface = autoclass("android.view.Surface")

        self._video_texture = Texture.create(size=(width, height), colorfmt="rgba")
        self._surface_texture = SurfaceTexture(int(self._video_texture.id))
        self._surface_texture.setDefaultBufferSize(width, height)
        self._surface = Surface(self._surface_texture)
        self._ui(lambda: self.player.setVideoSurface(self._surface))
        print("EXOPLAYER_SURFACE_OK", flush=True)

        self._fbo = Fbo(size=(width, height))
        self._fbo["resolution"] = (float(width), float(height))
        self._fbo["angle"] = float(math.radians(rotation))
        self._fbo.shader.fs = FS_EXTERNAL_OES
        with self._fbo:
            self._texture_cb = Callback(lambda instr: self._video_texture.bind)
            Rectangle(size=(width, height))

        self._root.ids.surface.set_texture(self._fbo.texture)

    @mainthread
    def _on_playback_state_changed(self, state):
        Player = self._Player
        names = {
            Player.STATE_IDLE: "IDLE",
            Player.STATE_BUFFERING: "BUFFERING",
            Player.STATE_READY: "READY",
            Player.STATE_ENDED: "ENDED",
        }
        name = names.get(state, str(state))
        Logger.info(f"ExoPlayerCheck: state -> {name}")
        if state == Player.STATE_READY:
            print("EXOPLAYER_READY_OK", flush=True)
        elif state == Player.STATE_ENDED:
            print("EXOPLAYER_EOS_OK", flush=True)
        self._refresh_status(f"state={name}")

    @mainthread
    def _on_is_playing_changed(self, is_playing):
        self._refresh_status("playing" if is_playing else "not playing")

    @mainthread
    def _on_player_error(self, error):
        message = error.getMessage()
        Logger.error(f"ExoPlayerCheck: player error: {message}")
        print(f"EXOPLAYER_ERROR {message}", flush=True)
        self._refresh_status(f"ERROR: {message}")

    def _refresh_status(self, extra=""):
        w, h = self._resolution
        self.status_text = (
            f"{os.path.basename(CLIP_PATH)} {w}x{h} rot={self._rotation}\n"
            f"frames drawn: {self._frame_count}\n{extra}"
        )

    # -- per-frame update ------------------------------------------------

    def _update(self, dt):
        if not self._surface_texture:
            return
        self._surface_texture.updateTexImage()
        self._texture_cb.ask_update()
        self._fbo.draw()
        self._frame_count += 1
        if not self._reported_first_frame:
            self._reported_first_frame = True
            print("EXOPLAYER_FRAME_OK", flush=True)
        if self._frame_count % 30 == 0:
            self._refresh_status()

    # -- controls, for manual poking --------------------------------

    def player_play(self):
        if self.player:
            self._ui(lambda: self.player.setPlayWhenReady(True))

    def player_pause(self):
        if self.player:
            self._ui(lambda: self.player.setPlayWhenReady(False))

    def player_stop(self):
        if not self.player:
            return

        def _stop_and_reload():
            self.player.stop()
            self.player.setMediaItem(self.player.getCurrentMediaItem())
            self.player.prepare()

        self._ui(_stop_and_reload)

    def player_seek_forward(self):
        if self.player:
            self._ui(lambda: self.player.seekTo(self.player.getCurrentPosition() + 10000))

    def on_stop(self):
        if self.player:
            player, self.player = self.player, None
            self._ui(player.release)


# Runs the same way with or without a __main__ guard -- kivyforge runs
# entry_point as __main__ on every platform.
ExoPlayerCheckApp().run()
