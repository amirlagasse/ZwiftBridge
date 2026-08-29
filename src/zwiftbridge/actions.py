"""The semantic action vocabulary that sits between buttons and outputs.

One keymap, many sinks: a binding names an action, and each output decides
how to express it (a keystroke on the Mac, a JSON message to MyWhoosh on an
iPad). Adding an output never means re-writing the keymap.
"""

from __future__ import annotations

from dataclasses import dataclass

SHIFT_UP = "shift_up"
SHIFT_DOWN = "shift_down"
STEER_LEFT = "steer_left"
STEER_RIGHT = "steer_right"
NAV_UP = "nav_up"
NAV_DOWN = "nav_down"
NAV_LEFT = "nav_left"
NAV_RIGHT = "nav_right"
SELECT = "select"
BACK = "back"
MENU = "menu"
TOGGLE_UI = "toggle_ui"
MINIMAL_UI = "minimal_ui"
FULLSCREEN = "fullscreen"
EMOTE = "emote"
CAMERA = "camera"
UTURN = "uturn"
TUCK = "tuck"
NONE = "none"

KNOWN_ACTIONS = frozenset({
    SHIFT_UP, SHIFT_DOWN, STEER_LEFT, STEER_RIGHT,
    NAV_UP, NAV_DOWN, NAV_LEFT, NAV_RIGHT,
    SELECT, BACK, MENU, TOGGLE_UI, MINIMAL_UI, FULLSCREEN,
    EMOTE, CAMERA, UTURN, TUCK, NONE,
})

# Actions that mean "while held" rather than "once, on press".
HOLD_ACTIONS = frozenset({STEER_LEFT, STEER_RIGHT})


@dataclass(frozen=True)
class Action:
    name: str
    arg: int | None = None

    @property
    def is_hold(self) -> bool:
        return self.name in HOLD_ACTIONS

    def __str__(self) -> str:
        return f"{self.name}:{self.arg}" if self.arg is not None else self.name


def parse_action(spec: str) -> Action:
    """Parse a binding value like "shift_up" or "emote:3"."""
    spec = spec.strip()
    name, _, raw_arg = spec.partition(":")
    name = name.strip().lower()
    if name not in KNOWN_ACTIONS:
        raise ValueError(
            f"unknown action {name!r}; known: {', '.join(sorted(KNOWN_ACTIONS))}"
        )
    if not raw_arg:
        return Action(name)
    try:
        return Action(name, int(raw_arg))
    except ValueError as exc:
        raise ValueError(f"action {spec!r} has a non-numeric argument") from exc
