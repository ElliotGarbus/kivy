"""Reproduce / verify iOS keyboard-height and safe-area units in Kivy.

Env (pass through ``SIMCTL_CHILD_<NAME>``):
  KBD_SOFTINPUT  Window.softinput_mode to use ("" by default).
  KBD_FIELD_Y    y of the TextInput in px (default 40: near the bottom).

Green band: 0 .. Window.keyboard_height (where Kivy thinks the keyboard top is).
Red bands:  Window.safe_area top / bottom insets.
"""

import ctypes
import os
import sys

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

TAG = "kbd-units"


def log(msg):
    Logger.info(f"{TAG}: {msg}")
    print(f"{TAG}: {msg}", flush=True)
    sys.stdout.flush()


def uikit_geometry():
    """UIScreen.scale / nativeScale / bounds and the SDL view's scales."""
    from kivy.mobile._platform import ios as _ios

    rt = _ios._objc_runtime()
    addr = ctypes.cast(rt.lib.objc_msgSend, ctypes.c_void_p).value
    send_rect = ctypes.CFUNCTYPE(
        _ios._CGRect, ctypes.c_void_p, ctypes.c_void_p)(addr)
    win = _ios._key_window(rt)
    screen = rt.send_id(rt.send_id(win, rt.sel(b"windowScene")),
                        rt.sel(b"screen"))
    bounds = send_rect(screen, rt.sel(b"bounds"))
    out = {
        "UIScreen.scale": rt.send_f64(screen, rt.sel(b"scale")),
        "UIScreen.nativeScale": rt.send_f64(screen, rt.sel(b"nativeScale")),
        "UIScreen.bounds": (bounds.size.width, bounds.size.height),
    }
    root_view = rt.send_id(rt.send_id(win, rt.sel(b"rootViewController")),
                           rt.sel(b"view"))
    out["rootView.contentScaleFactor"] = rt.send_f64(
        root_view, rt.sel(b"contentScaleFactor"))
    subviews = rt.send_id(root_view, rt.sel(b"subviews"))
    count = ctypes.CFUNCTYPE(
        ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(addr)(
        subviews, rt.sel(b"count"))
    at = ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
        addr)
    for i in range(count):
        sub = at(subviews, rt.sel(b"objectAtIndex:"), i)
        layer = rt.send_id(sub, rt.sel(b"layer"))
        out[f"subview[{i}].contentScaleFactor"] = rt.send_f64(
            sub, rt.sel(b"contentScaleFactor"))
        out[f"subview[{i}].layer.contentsScale"] = rt.send_f64(
            layer, rt.sel(b"contentsScale"))
    return out


class Root(FloatLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(0.1, 0.1, 0.12, 1)
            self._bg = Rectangle()
            Color(0.1, 0.8, 0.2, 0.55)
            self._kb = Rectangle()
            Color(0.9, 0.1, 0.1, 0.6)
            self._sa_top = Rectangle()
            self._sa_bottom = Rectangle()
        Window.bind(keyboard_height=self._redraw, safe_area=self._redraw,
                    size=self._redraw)
        self.bind(size=self._redraw, pos=self._redraw)

        self.status = Label(
            text="", size_hint=(1, None), height=300, halign="center",
            font_size="15sp", pos_hint={"center_y": 0.6})
        self.status.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(self.status)

        field_y = float(os.environ.get("KBD_FIELD_Y", "40"))
        self.field = TextInput(
            text="focus me", multiline=False, size_hint=(0.8, None),
            height=150, font_size="20sp", pos_hint={"center_x": 0.5},
            y=field_y)
        self.add_widget(self.field)
        self._redraw()

    def _redraw(self, *_):
        w, h = Window.size
        self._bg.size = (w, h)
        kh = Window.keyboard_height
        self._kb.size = (w, kh)
        sa = Window.safe_area
        self._sa_top.pos = (0, h - sa["top"])
        self._sa_top.size = (w, sa["top"])
        self._sa_bottom.size = (w, sa["bottom"])
        self.status.text = (
            f"Window.size={tuple(Window.size)}\n"
            f"keyboard_height={kh}\n"
            f"safe_area={dict(sa)}\n"
            f"softinput_mode={Window.softinput_mode!r}")


class KbdUnitsApp(App):
    def build(self):
        Window.softinput_mode = os.environ.get("KBD_SOFTINPUT", "")
        self.root_w = Root()
        Clock.schedule_once(self.report_startup, 1.0)
        Clock.schedule_once(self.focus, 2.0)
        return self.root_w

    def report_startup(self, *_):
        try:
            for k, v in uikit_geometry().items():
                log(f"{k}={v}")
        except Exception as exc:
            log(f"uikit_geometry failed: {exc!r}")
        log(f"Window._density={Window._density} "
            f"Window.system_size={tuple(Window.system_size)}")

    def focus(self, *_):
        log("focusing TextInput")
        self.root_w.field.focus = True
        Clock.schedule_once(self.report_keyboard, 2.5)

    def report_keyboard(self, *_):
        from kivy.mobile import (
            get_keyboard_height, get_safe_area, get_scale)
        log(f"Window.size={tuple(Window.size)} get_scale()={get_scale()} "
            f"get_keyboard_height()={get_keyboard_height()} "
            f"Window.keyboard_height={Window.keyboard_height} "
            f"get_safe_area()={get_safe_area()}")
        log(f"Window.safe_area={dict(Window.safe_area)} "
            f"softinput_mode={Window.softinput_mode!r} "
            f"field.y={self.root_w.field.y} field.top={self.root_w.field.top} "
            f"field.to_window(0,0)={self.root_w.field.to_window(0, 0)} "
            f"_kheight={Window._kheight}")
        log("READY_FOR_SCREENSHOT")


if __name__ == "__main__":
    KbdUnitsApp().run()
