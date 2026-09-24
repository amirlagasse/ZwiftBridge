"""Synthetic keystrokes -- for MyWhoosh running on this same computer.

Two backends behind one output, picked at start():

  * macOS -- CGEvent via Quartz. Higher-level injectors get ignored by games
    far more often, so this posts at the HID event tap.
  * Windows -- SendInput via ctypes, sending *scan codes* rather than virtual
    keys. Unity games (MyWhoosh is one) read raw input, and a scan code is what
    a real keyboard puts on the wire.

Four things bite here:
  * macOS Accessibility permission fails SILENTLY. Without it CGEventPost is a
    no-op with no error, so we check AXIsProcessTrusted up front.
  * Windows has no such permission, but UIPI is the same trap under another
    name: if MyWhoosh runs as administrator and zwiftbridge does not, SendInput
    is dropped with no error. Start both the same way.
  * MyWhoosh has to be the front window to receive the events.
  * Anything else in front EATS them. Pressing the d-pad while the control
    panel is in front used to arrow-key and space-bar the browser, opening
    disclosure triangles and toggling whatever had focus. So keys only go out
    while MyWhoosh is in front -- see _whoosh_is_front.
"""

from __future__ import annotations

import sys

from .. import actions as A
from ..actions import Action
from .base import Output

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# macOS ANSI virtual keycodes.
MAC_KEYCODES = {
    "a": 0, "d": 2, "f": 3, "h": 4, "i": 34, "k": 40, "u": 32,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26,
    "return": 36, "escape": 53, "tab": 48, "space": 49,
    "left": 123, "right": 124, "down": 125, "up": 126,
}

# Windows set-1 scan codes -- the byte a real keyboard puts on the wire, not
# the virtual key. Games reading raw input see these; a VK-only SendInput they
# often ignore. The arrows and the Windows key are "extended": a real keyboard
# prefixes them with 0xE0, which is the EXTENDEDKEY flag below.
WINDOWS_SCANCODES = {
    "a": 0x1E, "d": 0x20, "f": 0x21, "h": 0x23, "i": 0x17, "k": 0x25,
    "u": 0x16,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "7": 0x08,
    "return": 0x1C, "escape": 0x01, "tab": 0x0F, "space": 0x39,
    "left": 0x4B, "right": 0x4D, "down": 0x50, "up": 0x48,
    "f11": 0x57,
    # Modifiers, so _post can hold them down around the key.
    "ctrl": 0x1D, "shift": 0x2A, "alt": 0x38, "cmd": 0x5B,
}

# Keys a real keyboard prefixes with 0xE0.
WINDOWS_EXTENDED = frozenset({"left", "right", "up", "down", "cmd"})

# The table for the platform we are actually on, so a key with no code shows up
# as a missing entry here rather than as a silent no-op on the bike.
KEYCODES = WINDOWS_SCANCODES if IS_WINDOWS else MAC_KEYCODES

# Modifier names usable in a key spec like "ctrl+cmd+f".
MODIFIERS = ("cmd", "ctrl", "alt", "shift")

# MyWhoosh's keyboard bindings, per its own docs and the community shortcut
# list (mywhooshinfo.com/blog/mywhoosh-keyboard-shortcuts, Jul 2025):
#
#   K / I            shift up / shift down   <- confirmed on the bike
#   A / left arrow   steer left  (hold)
#   D / right arrow  steer right (hold)
#   U                toggle minimal UI
#   H                hide all controls (HD build only)
#   1..7             emotes, in the order MyWhoosh lists them
#   ctrl+cmd+F       fullscreen <-> windowed on macOS; F11 on Windows
#
# K is up and I is down. The community shortcut lists say the opposite and it
# looks wrong next to where the keys sit -- but this is what the game actually
# does, checked against a running MyWhoosh. BikeControl has it this way too.
# Do not "fix" it back to match the docs.
FULLSCREEN_KEY = "f11" if IS_WINDOWS else "ctrl+cmd+f"

ACTION_KEYS = {
    A.SHIFT_UP: "k",
    A.SHIFT_DOWN: "i",
    A.STEER_LEFT: "a",
    A.STEER_RIGHT: "d",
    A.TOGGLE_UI: "h",
    A.MINIMAL_UI: "u",
    A.FULLSCREEN: FULLSCREEN_KEY,
    # MyWhoosh steers on the arrow keys; it has no separate menu navigation,
    # so these are aliases for steering rather than a second axis.
    A.NAV_LEFT: "left",
    A.NAV_RIGHT: "right",
    # Undocumented -- MyWhoosh's menus are touch/click driven and it publishes
    # no shortcuts for them. Harmless to send; do not expect a response.
    A.NAV_UP: "up",
    A.NAV_DOWN: "down",
    A.SELECT: "return",
    A.BACK: "escape",
    A.MENU: "tab",
}

# Actions MyWhoosh has no keyboard shortcut for at all. Reported once rather
# than dropped silently, so a binding that can never fire is visible.
NO_KEY = {A.CAMERA: "camera views", A.UTURN: "U-turn", A.TUCK: "tuck"}


def _split_key(spec: str) -> tuple[list[str], str]:
    parts = spec.split("+")
    return [p for p in parts[:-1] if p in MODIFIERS], parts[-1]


# Matched against the front app's identifier and its name, both lowered.
# Substring rather than an exact bundle id or executable name: MyWhoosh ships
# under more than one (com.mywhoosh.*, the App Store build, the beta,
# MyWhoosh.exe), and they all say "whoosh".
WHOOSH_MARK = "whoosh"


# --- macOS -----------------------------------------------------------------


class _MacBackend:
    """CGEvent. Needs Accessibility, which fails silently without it."""

    def __init__(self) -> None:
        self._quartz = None
        self._workspace = None
        self.trusted = False

    def start(self) -> None:
        try:
            import Quartz  # type: ignore
        except ImportError:
            return
        self._quartz = Quartz
        try:
            from AppKit import NSWorkspace
            self._workspace = NSWorkspace.sharedWorkspace()
        except ImportError:
            # No AppKit: can't tell what is in front, so don't gate at all.
            self._workspace = None
        # Silent no-op without Accessibility, so surface it as a real status.
        # AXIsProcessTrusted lives in ApplicationServices, not Quartz.
        try:
            from ApplicationServices import AXIsProcessTrusted
            self.trusted = bool(AXIsProcessTrusted())
        except (ImportError, AttributeError):
            # Can't tell -- assume yes rather than refusing to send at all.
            self.trusted = True

    @property
    def available(self) -> bool:
        return self._quartz is not None

    @staticmethod
    def unavailable_reason() -> str:
        return "pyobjc-framework-Quartz not installed"

    def permission_problem(self) -> str | None:
        if self.trusted:
            return None
        return ("NO Accessibility permission — System Settings > Privacy & "
                "Security > Accessibility, add your terminal app, then "
                "restart it")

    def front_app(self) -> tuple[str, str] | None:
        if self._workspace is None:
            return None
        try:
            app = self._workspace.frontmostApplication()
            if app is None:
                return None
            return (app.localizedName() or "", app.bundleIdentifier() or "")
        except Exception:  # noqa: BLE001 -- never let focus checking kill input
            return None

    def post(self, spec: str, down: bool) -> None:
        Quartz = self._quartz
        mods, key = _split_key(spec)
        flags = 0
        for mod in mods:
            flags |= {
                "cmd": Quartz.kCGEventFlagMaskCommand,
                "ctrl": Quartz.kCGEventFlagMaskControl,
                "alt": Quartz.kCGEventFlagMaskAlternate,
                "shift": Quartz.kCGEventFlagMaskShift,
            }[mod]
        event = Quartz.CGEventCreateKeyboardEvent(None, MAC_KEYCODES[key], down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


# --- Windows ---------------------------------------------------------------


def _windows_input_types():
    """The SendInput structs.

    Sizes matter: SendInput rejects the call outright if cbSize is not exactly
    sizeof(INPUT), so MOUSEINPUT and HARDWAREINPUT are spelled out rather than
    padded by hand. The union has to come out the size the OS expects.
    """
    import ctypes
    from ctypes import wintypes

    ulong_ptr = (ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8
                 else ctypes.c_uint32)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT),
                    ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    return INPUT, KEYBDINPUT


class _WindowsBackend:
    """SendInput with scan codes -- what a real keyboard puts on the wire."""

    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None
        self._input_type = None
        self._keybd_type = None
        self._front_unreadable = False

    def start(self) -> None:
        import ctypes

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._input_type, self._keybd_type = _windows_input_types()

    @property
    def available(self) -> bool:
        return self._user32 is not None

    @staticmethod
    def unavailable_reason() -> str:
        return "user32 is not available"

    def permission_problem(self) -> str | None:
        # Windows has no Accessibility gate. The equivalent trap is UIPI: a
        # MyWhoosh running as administrator will not take input from a
        # zwiftbridge that is not. Nothing can test that short of a key
        # actually landing, so this is a hint, not a check -- a front window we
        # cannot read at all is usually one at a higher integrity level.
        if self._front_unreadable:
            return ("cannot read the front window — if MyWhoosh is running as "
                    "administrator, start zwiftbridge the same way or keys are "
                    "dropped with no error")
        return None

    def front_app(self) -> tuple[str, str] | None:
        """(window title, executable name) of the front window."""
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = self._user32, self._kernel32
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None

            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # QUERY_LIMITED_INFORMATION, not QUERY_INFORMATION: it is the one
            # that works across integrity levels without elevation.
            handle = kernel32.OpenProcess(
                self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            exe = ""
            if handle:
                try:
                    size = wintypes.DWORD(260)
                    buf = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buf, ctypes.byref(size)):
                        exe = buf.value.rsplit("\\", 1)[-1]
                finally:
                    kernel32.CloseHandle(handle)
            self._front_unreadable = not exe and not title.value
            return (title.value or "", exe)
        except Exception:  # noqa: BLE001 -- never let focus checking kill input
            return None

    def post(self, spec: str, down: bool) -> None:
        import ctypes

        mods, key = _split_key(spec)
        # Modifiers bracket the key: all down, then the key. On release only
        # the key goes up first, then the modifiers in the opposite order.
        if down:
            sequence = [(m, True) for m in mods] + [(key, True)]
        else:
            sequence = [(key, False)] + [(m, False) for m in reversed(mods)]
        events = [self._event(name, is_down) for name, is_down in sequence]
        array = (self._input_type * len(events))(*events)
        self._user32.SendInput(len(events), ctypes.byref(array),
                               ctypes.sizeof(self._input_type))

    def _event(self, name: str, down: bool):
        flags = self.KEYEVENTF_SCANCODE
        if name in WINDOWS_EXTENDED:
            flags |= self.KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= self.KEYEVENTF_KEYUP
        event = self._input_type()
        event.type = self.INPUT_KEYBOARD
        event.ki = self._keybd_type(wVk=0, wScan=WINDOWS_SCANCODES[name],
                                    dwFlags=flags, time=0, dwExtraInfo=0)
        return event


# --- the output ------------------------------------------------------------


def _make_backend():
    if IS_MAC:
        return _MacBackend()
    if IS_WINDOWS:
        return _WindowsBackend()
    return None


class KeystrokeOutput(Output):
    name = "keystrokes"

    def __init__(self, hold_ms: int = 40) -> None:
        self.hold_ms = hold_ms
        self._backend = None
        self._problem: str | None = None
        self._down: set[str] = set()   # keys we posted down and still owe an up
        self._blocked_by: str | None = None   # app we last dropped keys into

    def _front_app(self) -> tuple[str, str] | None:
        """(name, identifier) of the front app, or None if we cannot tell."""
        if self._backend is None:
            return None
        return self._backend.front_app()

    def _whoosh_is_front(self) -> bool | None:
        """True/False, or None when the front app cannot be determined."""
        front = self._front_app()
        if front is None:
            return None
        return any(WHOOSH_MARK in part.lower() for part in front)

    @property
    def ready(self) -> bool:
        return self._backend is not None and self._problem is None

    def status(self) -> str:
        if self._backend is None:
            backend = _make_backend()
            if backend is None:
                return f"no keystroke backend for {sys.platform}"
            return backend.unavailable_reason()
        if self._problem:
            return self._problem
        if self._whoosh_is_front() is False:
            front = self._front_app()
            who = front[0] if front and front[0] else "another app"
            return f"waiting — {who} is in front, bring MyWhoosh forward"
        return "ready — MyWhoosh must be the front window"

    async def start(self) -> None:
        backend = _make_backend()
        if backend is None:
            return
        try:
            backend.start()
        except Exception:  # noqa: BLE001 -- a broken backend is a status, not a crash
            return
        if not backend.available:
            return
        self._backend = backend
        self._problem = backend.permission_problem()

    def _post(self, spec: str, down: bool) -> None:
        self._backend.post(spec, down)
        if down:
            self._down.add(spec)
        else:
            self._down.discard(spec)

    async def send(self, action: Action, pressed: bool) -> str | None:
        if action.name == A.EMOTE:
            key = str(action.arg or 1)
            if key not in KEYCODES:
                return f"{action}: no key for emote {action.arg}"
        else:
            key = ACTION_KEYS.get(action.name)
        if key is None:
            if pressed and action.name in NO_KEY:
                return (f"{action}: MyWhoosh has no keyboard shortcut for "
                        f"{NO_KEY[action.name]} — use a network output for it")
            return None
        if not pressed and not action.is_hold:
            return None
        if self._backend is None or self._problem:
            return f"{action}: dropped, {self.status()}"

        # A key we already pressed always gets its release, whatever is in
        # front now -- otherwise alt-tabbing mid-corner leaves it steering.
        owed = action.is_hold and not pressed and key in self._down
        if not owed and self._whoosh_is_front() is False:
            front = self._front_app()
            who = (front[0] if front and front[0] else "another app")
            if who == self._blocked_by:
                return None            # already said so; don't spam the log
            self._blocked_by = who
            return f"{action}: not sent — {who} is in front, not MyWhoosh"
        self._blocked_by = None

        if action.is_hold:
            # Held actions mirror the physical button: down on press, up on
            # release, so steering keeps steering while you hold the paddle.
            self._post(key, pressed)
            return f"{action} -> key {key} {'down' if pressed else 'up'}"

        import asyncio
        self._post(key, True)
        await asyncio.sleep(self.hold_ms / 1000)
        self._post(key, False)
        return f"{action} -> key {key} tap"
