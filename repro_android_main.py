"""On-device test for kivy/kivy#9363 (softinput_mode = "below_target").

Layout is the issue's reproduction (spacer / TextInput / spacer) plus a
second TextInput at the bottom that the keyboard covers, to see how much
the content is moved when a pan is actually needed.

Everything interesting is logged with the "R9363" prefix:
  * STATE  - on every change: focus, keyboard heights, drawn content offset
  * TOUCH  - every touch down: normalized screen position and the window
             position Kivy mapped it to, plus which widget it hit
  * LAYOUT - widget rectangles in window coordinates
"""
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.logger import Logger
from kivy.properties import StringProperty

Window.softinput_mode = "below_target"

KV = """
BoxLayout:
    orientation: "vertical"
    padding: "20dp"
    spacing: "20dp"

    BoxLayout:
        size_hint_y: None
        height: "120dp"
        Label:
            id: status
            font_size: "13sp"
            text_size: self.width, None
            color: 1, 1, 0, 1
        Button:
            id: mode
            size_hint_x: None
            width: "110dp"
            text: app.mode
            on_release: app.toggle_mode()

    Widget:

    TextInput:
        id: mid
        size_hint_y: None
        height: "48dp"
        text: "Tap here, then tap between these characters"
        multiline: False

    Widget:

    TextInput:
        id: low
        size_hint_y: None
        height: "48dp"
        text: "Low field - the keyboard covers this one"
        multiline: False
"""

T0 = time.perf_counter()


def log(kind, msg):
    Logger.info(f"R9363: {kind} t={time.perf_counter() - T0:7.3f} {msg}")


def drawn_offset():
    return round(Window.render_context["modelview_mat"].get()[13], 1)


def rect(w):
    x, y = w.to_window(w.x, w.y)
    return f"x={x:.0f} y={y:.0f} w={w.width:.0f} h={w.height:.0f}"


class TestApp(App):
    mode = StringProperty(Window.softinput_mode)

    def toggle_mode(self):
        self.mode = "pan" if self.mode == "below_target" else "below_target"
        Window.softinput_mode = self.mode
        log("MODE", self.mode)

    def build(self):
        self.root_widget = Builder.load_string(KV)
        self._last = None
        Window.bind(on_touch_down=self._on_touch_down)
        try:
            from kivy.mobile import subscribe_keyboard_height
            subscribe_keyboard_height(
                lambda h: log("IME", f"raw kivy.mobile height={h}"))
        except Exception as exc:  # noqa: BLE001
            log("IME", f"subscribe failed: {exc!r}")
        Clock.schedule_interval(self._sample, 0)
        Clock.schedule_once(self._layout, 1)
        return self.root_widget

    def _layout(self, *_):
        ids = self.root_widget.ids
        log("LAYOUT", f"window={tuple(Window.size)} "
                      f"system_size={tuple(Window.system_size)}")
        log("LAYOUT", f"mid {rect(ids.mid)}")
        log("LAYOUT", f"low {rect(ids.low)}")

    def _state(self):
        ids = self.root_widget.ids
        target = Window._system_keyboard.target
        name = {ids.mid: "mid", ids.low: "low"}.get(target, repr(target))
        return (
            f"focus mid={ids.mid.focus} low={ids.low.focus} target={name} "
            f"keyboard_height={Window.keyboard_height:.0f} "
            f"_kheight={Window._kheight:.0f} offset={drawn_offset():.0f}"
        )

    def _sample(self, *_):
        state = self._state()
        if state != self._last:
            self._last = state
            log("STATE", state)
            self.root_widget.ids.status.text = state.replace(" k", "\nk", 1)

    def _on_touch_down(self, win, touch):
        ids = self.root_widget.ids
        hit = [n for n, w in (("mid", ids.mid), ("low", ids.low))
               if w.collide_point(*w.to_widget(*touch.pos))]
        log("TOUCH", f"spos=({touch.sx:.4f},{touch.sy:.4f}) "
                     f"pos=({touch.x:.0f},{touch.y:.0f}) "
                     f"hit={hit or 'none'} offset={drawn_offset():.0f}")


TestApp().run()
