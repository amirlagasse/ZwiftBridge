"""Synthetic keystrokes via CoreGraphics -- for MyWhoosh on this same Mac.

Uses CGEvent rather than a higher-level injector: games ignore higher-level
synthetic input far more often.

Three things bite here:
  * Accessibility permission fails SILENTLY. Without it CGEventPost is a
    no-op with no error, so we check AXIsProcessTrusted up front.
  * MyWhoosh has to be the frontmost app to receive the events.
  * Anything else frontmost EATS them. Pressing the d-pad while the control
    panel is in front used to arrow-key and space-bar the browser, opening
    disclosure triangles and toggling whatever had focus. So keys only go out
    while MyWhoosh is frontmost -- see _whoosh_is_front.
"""

from __future__ import annotations

from .. import actions as A
from ..actions import Action
from .base import Output

# ANSI virtual keycodes.
KEYCODES = {
    "a": 0, "d": 2, "f": 3, "h": 4, "i": 34, "k": 40, "u": 32,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26,
    "return": 36, "escape": 53, "tab": 48, "space": 49,
    "left": 123, "right": 124, "down": 125, "up": 126,
}

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
#   ctrl+cmd+F       fullscreen <-> windowed (macOS; F11 on Windows)
#
# K is up and I is down. The community shortcut lists say the opposite and it
# looks wrong next to where the keys sit -- but this is what the game actually
# does, checked against a running MyWhoosh. BikeControl has it this way too.
# Do not "fix" it back to match the docs.
ACTION_KEYS = {
    A.SHIFT_UP: "k",
    A.SHIFT_DOWN: "i",
    A.STEER_LEFT: "a",
    A.STEER_RIGHT: "d",
    A.TOGGLE_UI: "h",
    A.MINIMAL_UI: "u",
    A.FULLSCREEN: "ctrl+cmd+f",
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


# Matched against the frontmost app's bundle id and its name, both lowered.
# Substring rather than an exact bundle id: MyWhoosh ships under more than one
# (com.mywhoosh.*, the App Store build, the beta), and they all say "whoosh".
WHOOSH_MARK = "whoosh"


class KeystrokeOutput(Output):
    name = "keystrokes"

    def __init__(self, hold_ms: int = 40) -> None:
        self.hold_ms = hold_ms
        self._trusted = False
        self._quartz = None
        self._workspace = None
        self._down: set[str] = set()   # keys we posted down and still owe an up
        self._blocked_by: str | None = None   # app we last dropped keys into

    def _front_app(self) -> tuple[str, str] | None:
        """(name, bundle id) of the frontmost app, or None if we cannot tell."""
        if self._workspace is None:
            return None
        try:
            app = self._workspace.frontmostApplication()
            if app is None:
                return None
            return (app.localizedName() or "", app.bundleIdentifier() or "")
        except Exception:  # noqa: BLE001 -- never let focus checking kill input
            return None

    def _whoosh_is_front(self) -> bool | None:
        """True/False, or None when the frontmost app cannot be determined."""
        front = self._front_app()
        if front is None:
            return None
        return any(WHOOSH_MARK in part.lower() for part in front)

    @property
    def ready(self) -> bool:
        return self._trusted

    def status(self) -> str:
        if self._quartz is None:
            return "pyobjc-framework-Quartz not installed"
        if not self._trusted:
            return ("NO Accessibility permission — System Settings > Privacy & "
                    "Security > Accessibility, add your terminal app, then "
                    "restart it")
        if self._whoosh_is_front() is False:
            front = self._front_app()
            who = front[0] if front and front[0] else "another app"
            return f"waiting — {who} is in front, bring MyWhoosh forward"
        return "ready — MyWhoosh must be the frontmost app"

    async def start(self) -> None:
        try:
            import Quartz  # type: ignore
        except ImportError:
            return
        self._quartz = Quartz
        try:
            from AppKit import NSWorkspace
            self._workspace = NSWorkspace.sharedWorkspace()
        except ImportError:
            # No AppKit: can't tell what is frontmost, so don't gate at all.
            self._workspace = None
        # Silent no-op without Accessibility, so surface it as a real status.
        # AXIsProcessTrusted lives in ApplicationServices, not Quartz.
        try:
            from ApplicationServices import AXIsProcessTrusted
            self._trusted = bool(AXIsProcessTrusted())
        except (ImportError, AttributeError):
            # Can't tell -- assume yes rather than refusing to send at all.
            self._trusted = True

    def _post(self, spec: str, down: bool) -> None:
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
        event = Quartz.CGEventCreateKeyboardEvent(None, KEYCODES[key], down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
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
        if self._quartz is None or not self._trusted:
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
