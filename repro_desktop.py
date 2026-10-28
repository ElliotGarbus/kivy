"""Desktop reproduction of kivy/kivy#9363 (softinput_mode='below_target').

Fakes an Android system keyboard by patching ``platform`` in
``kivy.core.window`` and installing a stub ``kivy.mobile`` whose keyboard
height we control, then drives real touches through the event loop.
"""
import os
import sys
import time
import types

os.environ.setdefault('KIVY_NO_ARGS', '1')

fake_kb = {'height': 0.0}
stub = types.ModuleType('kivy.mobile')
stub.get_keyboard_height = lambda: fake_kb['height']
stub.get_safe_area = lambda: {'top': 0.0, 'left': 0.0,
                              'bottom': 0.0, 'right': 0.0}
stub.subscribe_keyboard_height = lambda cb: None
sys.modules['kivy.mobile'] = stub

from kivy.config import Config  # noqa: E402
Config.set('kivy', 'keyboard_mode', '')

from kivy.base import EventLoop  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
import kivy.core.window as core_window  # noqa: E402
from kivy.lang import Builder  # noqa: E402
from kivy.tests.common import UnitTestTouch  # noqa: E402

core_window.platform = 'android'
Window.softinput_mode = 'below_target'

root = Builder.load_string('''
BoxLayout:
    orientation: "vertical"
    padding: "20dp"
    spacing: "20dp"
    Widget:
    TextInput:
        id: ti
        size_hint_y: None
        height: "48dp"
        text: "Tap here, then tap between these characters"
        multiline: False
    Widget:
''')
ti = root.ids.ti
EventLoop.ensure_window()
Window.add_widget(root)


def pump(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        EventLoop.idle()
        time.sleep(1 / 120.)


def view_shift():
    """Upward translation actually applied to the drawn content."""
    return Window.render_context['modelview_mat'].get()[13]


def tap(x, y):
    t = UnitTestTouch(x, y)
    t.touch_down()
    t.touch_up()
    pump(0.05)


pump(0.3)
kb = round(Window.height * 0.35)
cx, cy = ti.center
print(f'window={Window.size} textinput y={ti.y:.0f}..{ti.top:.0f} '
      f'fake keyboard={kb}px (below the TextInput, so no pan is needed)')

# --- Issue 2 -----------------------------------------------------------
tap(cx, cy)
print(f'\n[1] tap TextInput           -> focus={ti.focus}')
fake_kb['height'] = kb
Window.trigger_keyboard_height()
pump(1.2)
shift = view_shift()
print(f'[2] keyboard shown          -> keyboard_height={Window.keyboard_height}'
      f'  content drawn shifted up by {shift:.0f}px')
tap(cx, cy + shift)
print(f'[3] tap where TextInput is DRAWN ({cx:.0f},{cy + shift:.0f})'
      f' -> focus={ti.focus}   <-- issue 2 if False')

# --- Issue 1 -----------------------------------------------------------
ti.focus = True
pump(0.1)
print(f'\n[4] refocus                 -> focus={ti.focus}, '
      f'shift={view_shift():.0f}px')
ti.focus = False          # what a tap outside does
fake_kb['height'] = 0.0   # the OS hides the IME
Clock.schedule_once(lambda dt: Window.trigger_keyboard_height(), 0.1)
samples = []
start = time.perf_counter()
while time.perf_counter() - start < 1.5:
    EventLoop.idle()
    samples.append((time.perf_counter() - start, view_shift()))
    time.sleep(1 / 120.)
peak_t, peak = max(samples, key=lambda s: s[1])
print(f'[5] keyboard dismissed      -> content shifted UP by up to {peak:.0f}px'
      f' (peak at t={peak_t:.2f}s), final {samples[-1][1]:.0f}px'
      f'   <-- issue 1 if peak > 0')
for t, s in samples[::12]:
    print(f'      t={t:.2f}s shift={s:6.0f}px')

Window.close()
