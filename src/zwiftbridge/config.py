"""Config loading. One keymap in TOML, shared by every output."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, settings
from .actions import Action, parse_action
from .protocol import ALL_BUTTONS, ANALOG_BUTTONS

DEFAULT_PATH = paths.CONFIG_FILE

# Natural drivetrain feel: every "up" paddle shifts up, every "down" paddle
# shifts down. (BikeControl's stock Ride profile instead makes the whole left
# side shift down and the whole right side shift up -- change it below if you
# prefer that.)
DEFAULT_BINDINGS = {
    "shift_up_l": "shift_up",
    "shift_dn_l": "shift_down",
    "shift_up_r": "shift_up",
    "shift_dn_r": "shift_down",
    "paddle_l": "steer_left",
    "paddle_r": "steer_right",
    "left": "steer_left",
    "right": "steer_right",
    "up": "nav_up",
    "down": "nav_down",
    "a": "select",
    "b": "back",
    "y": "menu",
    "z": "emote:1",
    "onoff_l": "toggle_ui",
    "onoff_r": "camera:1",
    "powerup_l": "uturn",
    "powerup_r": "tuck",
}


# Haptics are per button, because the useful distinction is physical, not
# semantic: the analog paddles are HELD rather than clicked, so they buzz
# continuously, while the d-pad arrows share the same steering action and are
# perfectly good to buzz. Absent from the map means "use this default".
DEFAULT_BUZZ = {name: name not in ANALOG_BUTTONS for name in ALL_BUTTONS}

# Old spellings of the setting, kept working so an existing config.toml does
# not break. "shift" predates per-button control and is folded into it here.
LEGACY_VIBRATE_MODES = ("all", "shift", "off")


def _buzz_map(raw: dict, source) -> tuple[bool, dict[str, bool]]:
    """Resolve master on/off and the per-button map, legacy spellings included.

    Returns (enabled, {button: buzzes}) covering every known button, so the
    bridge never has to reason about what an absent key meant.
    """
    enabled = True
    per_button = dict(DEFAULT_BUZZ)

    mode = raw.get("vibrate")
    if isinstance(mode, bool):
        enabled = mode
    elif mode is not None:
        text = str(mode).strip().lower()
        if text not in LEGACY_VIBRATE_MODES:
            raise ValueError(
                f"{source}: vibrate must be true/false or one of "
                f"{', '.join(LEGACY_VIBRATE_MODES)}, got {mode!r}"
            )
        enabled = text != "off"
        if text == "shift":
            # The old mode meant gear changes only. Say that per button.
            per_button = {name: False for name in ALL_BUTTONS}
            for name in ("shift_up_l", "shift_dn_l", "shift_up_r", "shift_dn_r"):
                per_button[name] = True

    for name in raw.get("vibrate_except") or ():
        _require_button(name, source, "vibrate_except")
        per_button[str(name)] = False

    for name, value in (raw.get("vibrate_buttons") or {}).items():
        _require_button(name, source, "vibrate_buttons")
        per_button[str(name)] = bool(value)

    return enabled, per_button


def _require_button(name, source, field_name: str) -> None:
    if str(name) not in ALL_BUTTONS:
        raise ValueError(
            f"{source}: {field_name} names unknown button {name!r}; "
            f"known: {', '.join(ALL_BUTTONS)}"
        )


@dataclass
class Config:
    outputs: list[str] = field(default_factory=lambda: ["zwift_dircon"])
    # The Ride is two peripherals, so this is a list. A single `address` in
    # the TOML still works and is folded in here.
    addresses: list[str] = field(default_factory=list)
    scan_timeout: float = 15.0
    reconnect: bool = True
    # Haptics on the controller itself: "all", "shift" (gear changes only)
    # or "off". Duration is the raw byte the Ride takes; 0x20 is a short tick.
    # Master switch, then the per-button map. The map always covers every
    # button, so `buzzes()` never has to guess what an absent key meant.
    vibrate: bool = True
    vibrate_ms: int = 0x20
    vibrate_buttons: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_BUZZ))
    bindings: dict[str, Action] = field(default_factory=dict)
    output_settings: dict[str, dict] = field(default_factory=dict)

    def action_for(self, button: str) -> Action | None:
        return self.bindings.get(button)

    def buzzes(self, button: str) -> bool:
        """Should pressing this button buzz the controller?"""
        return self.vibrate and self.vibrate_buttons.get(button, True)


def load(path: Path | str | None = None, *, overlay: bool = True) -> Config:
    """Load config from TOML, falling back to defaults for anything absent.

    Anything changed in the control panel lives in settings.json and is layered
    on top. Pass overlay=False for the file as written, which is what the
    self-test wants: it asserts against config.toml, not against whatever the
    panel was last set to.
    """
    raw: dict = {}
    resolved = Path(path) if path else DEFAULT_PATH
    if resolved.exists():
        raw = tomllib.loads(resolved.read_text())

    saved = settings.load() if overlay else {}

    bridge = {**raw.get("bridge", {}),
              **{k: v for k, v in saved.items() if k != "bindings"}}
    bindings_raw = {**DEFAULT_BINDINGS, **raw.get("buttons", {}),
                    **saved.get("bindings", {})}

    bindings: dict[str, Action] = {}
    for button, spec in bindings_raw.items():
        if button not in ALL_BUTTONS:
            raise ValueError(
                f"{resolved}: unknown button {button!r}; "
                f"known: {', '.join(ALL_BUTTONS)}"
            )
        action = parse_action(spec)
        if action.name != "none":
            bindings[button] = action

    enabled, per_button = _buzz_map(bridge, resolved)

    addresses = [a for a in bridge.get("addresses", []) if a]
    if bridge.get("address"):
        addresses.append(bridge["address"])

    return Config(
        outputs=list(bridge.get("outputs", ["zwift_dircon"])),
        addresses=addresses,
        scan_timeout=float(bridge.get("scan_timeout", 15.0)),
        reconnect=bool(bridge.get("reconnect", True)),
        vibrate=enabled,
        vibrate_ms=int(bridge.get("vibrate_ms", 0x20)),
        vibrate_buttons=per_button,
        bindings=bindings,
        output_settings={
            key: raw[key] for key in ("zwift_dircon", "obp_dircon", "obp_mdns", "whoosh_link", "keystrokes") if key in raw
        },
    )
