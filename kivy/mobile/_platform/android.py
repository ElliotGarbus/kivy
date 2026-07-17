"""Android implementation of the kivy.mobile platform API.

Reads runtime window/display geometry from the running Android activity using
``jnius`` (https://github.com/kivy/pyjnius) — a standalone Kivy-org package
present in every Kivy Android build.  No compiled extension and no
python-for-android module changes are required: every value is obtained by
reflection against ``PythonActivity.mActivity`` and the Android framework.

All lengths are returned in **pixels**, which is Kivy's layout coordinate
system on Android (``density`` is folded into :class:`kivy.metrics.Metrics`,
not into window coordinates).

Window-insets and display-cutout reads must run on the Android UI thread —
Kivy/SDL runs on a separate thread — so those calls are marshalled onto the UI
thread via ``Activity.runOnUiThread`` and block briefly for the result.
``DisplayMetrics`` is thread-safe to read directly.

Requires an API 30+ (Android 11+) device: ``WindowInsets.getInsets(type)`` and
the ``ime()`` inset type were added in API 30.  Method resolution happens at
runtime via reflection, so the build/compile API level is irrelevant.

This module is imported automatically by ``kivy.mobile`` when
``kivy.utils.platform == 'android'``.  Do not import it directly.
"""

from __future__ import annotations

import threading

from jnius import autoclass, PythonJavaClass, java_method

PythonActivity = autoclass("org.kivy.android.PythonActivity")
DisplayMetrics = autoclass("android.util.DisplayMetrics")
WindowInsetsType = autoclass("android.view.WindowInsets$Type")

# Strong references to Runnables until the UI thread has executed them.
_runnable_refs: list = []


def _activity():
    return PythonActivity.mActivity


class _Runnable(PythonJavaClass):
    __javainterfaces__ = ["java/lang/Runnable"]
    __javacontext__ = "app"

    def __init__(self, func):
        super().__init__()
        self._func = func

    @java_method("()V")
    def run(self):
        self._func()


def _on_ui_thread(func, timeout: float = 2.0):
    """Run *func* on the Android UI thread and return its result (blocking)."""
    box: dict = {}
    done = threading.Event()

    def wrapper():
        try:
            box["value"] = func()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            done.set()

    runnable = _Runnable(wrapper)
    _runnable_refs.append(runnable)
    try:
        _activity().runOnUiThread(runnable)
        if not done.wait(timeout=timeout):
            raise TimeoutError("kivy.mobile: UI-thread geometry read timed out")
        if "error" in box:
            raise box["error"]
        return box.get("value")
    finally:
        try:
            _runnable_refs.remove(runnable)
        except ValueError:
            pass


def _metrics():
    metrics = DisplayMetrics()
    _activity().getWindowManager().getDefaultDisplay().getMetrics(metrics)
    return metrics


def _root_insets():
    """WindowInsets for the decor view (call only on the UI thread)."""
    return _activity().getWindow().getDecorView().getRootWindowInsets()


# ---------------------------------------------------------------------------
# Tier-1 API
# ---------------------------------------------------------------------------


def get_dpi() -> float:
    """Physical screen DPI (Android ``densityDpi``; matches ``Metrics.dpi``)."""
    try:
        return float(_metrics().densityDpi)
    except Exception:
        return 96.0


def get_scale() -> float:
    """Display scale factor (Android ``scaledDensity``)."""
    try:
        return float(_metrics().scaledDensity)
    except Exception:
        return 1.0


def get_density() -> float:
    """Logical pixel density.  Alias for :func:`get_scale`."""
    return get_scale()


def get_keyboard_height() -> float:
    """Current soft-keyboard (IME) height in pixels; 0 when hidden."""

    def work():
        insets = _root_insets()
        if insets is None:
            return 0.0
        return float(insets.getInsets(WindowInsetsType.ime()).bottom)

    try:
        return _on_ui_thread(work)
    except Exception:
        return 0.0


def get_safe_area() -> dict[str, float]:
    """Safe-area insets in pixels (system bars unioned with the display cutout).

    Returns ``{"top", "left", "bottom", "right"}``.
    """

    def work():
        insets = _root_insets()
        if insets is None:
            return {"top": 0.0, "left": 0.0, "bottom": 0.0, "right": 0.0}
        bars = insets.getInsets(WindowInsetsType.systemBars())
        cut = insets.getInsets(WindowInsetsType.displayCutout())
        return {
            "top": float(max(bars.top, cut.top)),
            "left": float(max(bars.left, cut.left)),
            "bottom": float(max(bars.bottom, cut.bottom)),
            "right": float(max(bars.right, cut.right)),
        }

    try:
        return _on_ui_thread(work)
    except Exception:
        return {"top": 0.0, "left": 0.0, "bottom": 0.0, "right": 0.0}


# ---------------------------------------------------------------------------
# Keyboard-height subscription
#
# Driven by polling the IME inset from a Kivy Clock tick, scheduled lazily on
# the first subscription.  Subscribers are notified only when the height
# changes (including back to 0 on hide).
# ---------------------------------------------------------------------------

_kb_subscribers: list = []
_kb_last: float = 0.0
_kb_poll_scheduled: bool = False


def _poll_keyboard(_dt) -> None:
    global _kb_last
    height = get_keyboard_height()
    if height != _kb_last:
        _kb_last = height
        for cb in list(_kb_subscribers):
            try:
                cb(height)
            except Exception:
                pass


def subscribe_keyboard_height(callback) -> None:
    """Register *callback(height: float)* for keyboard-height changes.

    The callback runs on the Kivy main thread, so it is safe to update Kivy
    properties directly.  It is invoked with 0.0 when the keyboard hides.
    """
    global _kb_poll_scheduled
    _kb_subscribers.append(callback)
    if not _kb_poll_scheduled:
        from kivy.clock import Clock

        Clock.schedule_interval(_poll_keyboard, 1 / 10.0)
        _kb_poll_scheduled = True


# ---------------------------------------------------------------------------
# Tier-2 API — Android extras
# ---------------------------------------------------------------------------


def get_display_cutout():
    """Physical display-cutout regions, or ``None`` when the window has none.

    Returns a list of ``{"left", "top", "right", "bottom"}`` pixel rects (one
    per cutout).  Returns ``None`` when the current window does not overlap any
    cutout (e.g. when Android letterboxes the app away from it in landscape
    under the default cutout mode).
    """

    def work():
        insets = _root_insets()
        if insets is None:
            return None
        cutout = insets.getDisplayCutout()
        if cutout is None:
            return None
        rects = cutout.getBoundingRects()
        out = []
        for i in range(rects.size()):
            r = rects.get(i)
            out.append(
                {
                    "left": int(r.left),
                    "top": int(r.top),
                    "right": int(r.right),
                    "bottom": int(r.bottom),
                }
            )
        return out or None

    try:
        return _on_ui_thread(work)
    except Exception:
        return None


def get_system_bar_insets():
    """Status-bar and navigation-bar insets separated, in pixels, or ``None``.

    Returns ``{"status_bar": {...}, "nav_bar": {...}}`` where each value is a
    ``{"left", "top", "right", "bottom"}`` dict.
    """

    def work():
        insets = _root_insets()
        if insets is None:
            return None
        status = insets.getInsets(WindowInsetsType.statusBars())
        nav = insets.getInsets(WindowInsetsType.navigationBars())
        return {
            "status_bar": {
                "top": int(status.top),
                "left": int(status.left),
                "bottom": int(status.bottom),
                "right": int(status.right),
            },
            "nav_bar": {
                "top": int(nav.top),
                "left": int(nav.left),
                "bottom": int(nav.bottom),
                "right": int(nav.right),
            },
        }

    try:
        return _on_ui_thread(work)
    except Exception:
        return None
