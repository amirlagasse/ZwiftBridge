"""OpenBikeProtocol -- the open controller protocol MyWhoosh supports natively.

Unlike MyWhoosh Link, an OBP device *advertises itself over mDNS*, so it shows
up in MyWhoosh's own device scan instead of waiting to be dialled. That is the
whole reason this exists: Link requires MyWhoosh to already know to look for
us, and on this setup it never did.

Wire format (see the public PROTOCOL.md; implemented fresh here):

    BUTTON_STATE     0x01  [0x01, id, state, id, state, ...]
    DEVICE_STATUS    0x02  [0x02, battery|0xFF, connected]
    HAPTIC_FEEDBACK  0x03  [0x03, pattern, duration, intensity]
    APP_INFO         0x04  [0x04, ver, idLen, id.., verLen, ver.., n, ids..]

state is 0 released, 1 pressed, 2..255 analog.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import actions as A

# --- message types ---------------------------------------------------------

MSG_BUTTON_STATE = 0x01
MSG_DEVICE_STATUS = 0x02
MSG_HAPTIC_FEEDBACK = 0x03
MSG_APP_INFO = 0x04

# --- BLE identifiers, also carried in the mDNS TXT record ------------------

SERVICE_UUID = "d273f680-d548-419d-b9d1-fa0472345229"
BUTTON_STATE_CHAR_UUID = "d273f681-d548-419d-b9d1-fa0472345229"
HAPTIC_CHAR_UUID = "d273f682-d548-419d-b9d1-fa0472345229"
APPINFO_CHAR_UUID = "d273f683-d548-419d-b9d1-fa0472345229"

MDNS_SERVICE_TYPE = "_openbikecontrol._tcp.local."
DEFAULT_PORT = 36867

# --- button ids ------------------------------------------------------------

BUTTON_NAMES = {
    0x01: "Shift Up",
    0x02: "Shift Down",
    0x03: "Gear Set",
    0x10: "Up",
    0x11: "Down",
    0x12: "Left/Look Left",
    0x13: "Right/Look Right",
    0x14: "Select/Confirm",
    0x15: "Back/Cancel",
    0x16: "Menu",
    0x17: "Home",
    0x18: "Steer Left",
    0x19: "Steer Right",
    0x20: "Emote",
    0x21: "Push to Talk",
    0x30: "Increase Difficulty",
    0x31: "Decrease Difficulty",
    0x32: "Skip Interval",
    0x33: "Pause",
    0x34: "Resume",
    0x35: "Lap",
    0x36: "Previous Interval",
    0x37: "U-Turn",
    0x38: "Change Mode",
    0x39: "Take a Break",
    0x3A: "Join Rider",
    0x3B: "Change Route",
    0x40: "Camera View",
    0x41: "Camera 1",
    0x42: "Camera 2",
    0x43: "Camera 3",
    0x44: "HUD Toggle",
    0x45: "Map Toggle",
    0x46: "Spectate Rider",
    0x50: "Power-up 1",
    0x51: "Power-up 2",
    0x52: "Power-up 3",
}

# Our action vocabulary -> OBP button id. The app tells us at runtime which of
# these it actually honours, so anything unlisted is reported, not guessed.
ACTION_BUTTONS = {
    A.SHIFT_UP: 0x01,
    A.SHIFT_DOWN: 0x02,
    A.NAV_UP: 0x10,
    A.NAV_DOWN: 0x11,
    A.NAV_LEFT: 0x12,
    A.NAV_RIGHT: 0x13,
    A.SELECT: 0x14,
    A.BACK: 0x15,
    A.MENU: 0x16,
    A.STEER_LEFT: 0x18,
    A.STEER_RIGHT: 0x19,
    A.EMOTE: 0x20,
    A.UTURN: 0x37,
    A.CAMERA: 0x40,
    A.TOGGLE_UI: 0x44,
}


class ProtocolError(ValueError):
    """Malformed OpenBikeProtocol message."""


@dataclass
class AppInfo:
    app_id: str
    app_version: str
    buttons: list[int]

    @property
    def button_names(self) -> list[str]:
        return [BUTTON_NAMES.get(b, f"0x{b:02x}") for b in self.buttons]

    def supports(self, button_id: int) -> bool:
        return button_id in self.buttons


# --- encode ----------------------------------------------------------------


def encode_button_state(states: list[tuple[int, int]]) -> bytes:
    """states is [(button_id, state)]; state 0 released, 1 pressed, 2+ analog."""
    out = bytearray([MSG_BUTTON_STATE])
    for button_id, state in states:
        out += bytes([button_id & 0xFF, state & 0xFF])
    return bytes(out)


def encode_device_status(battery: int | None = None,
                         connected: bool = True) -> bytes:
    return bytes([
        MSG_DEVICE_STATUS,
        0xFF if battery is None else battery & 0xFF,
        0x01 if connected else 0x00,
    ])


def encode_app_info(app_id: str, app_version: str,
                    buttons: list[int]) -> bytes:
    """Only needed to talk to another OBP device -- and by the self-test."""
    id_bytes = app_id.encode()[:32]
    version_bytes = app_version.encode()[:32]
    out = bytearray([MSG_APP_INFO, 0x01, len(id_bytes)])
    out += id_bytes
    out.append(len(version_bytes))
    out += version_bytes
    out.append(len(buttons))
    out += bytes(b & 0xFF for b in buttons)
    return bytes(out)


# --- decode ----------------------------------------------------------------


def parse_app_info(data: bytes) -> AppInfo:
    """Parse an APP_INFO message. Raises ProtocolError if incomplete.

    A central may split this across several writes, so callers should treat a
    ProtocolError as "not yet complete" and retry with more bytes appended.
    """
    if not data or data[0] != MSG_APP_INFO:
        raise ProtocolError("not an app-info message")
    if len(data) < 3:
        raise ProtocolError("app info too short")

    index = 1
    version = data[index]
    index += 1
    if version != 0x01:
        raise ProtocolError(f"unsupported app-info version {version}")

    def take_string() -> str:
        nonlocal index
        if index >= len(data):
            raise ProtocolError("truncated app info")
        length = data[index]
        index += 1
        if index + length > len(data):
            raise ProtocolError("string length exceeds buffer")
        value = data[index:index + length].decode("utf-8", "replace")
        index += length
        return value

    app_id = take_string()
    app_version = take_string()

    if index >= len(data):
        raise ProtocolError("missing button count")
    count = data[index]
    index += 1
    if index + count > len(data):
        raise ProtocolError("button count exceeds buffer")

    return AppInfo(app_id, app_version, list(data[index:index + count]))


def parse_button_state(data: bytes) -> list[tuple[int, int]]:
    if not data or data[0] != MSG_BUTTON_STATE:
        raise ProtocolError("not a button-state message")
    return [(data[i], data[i + 1]) for i in range(1, len(data) - 1, 2)]


def parse_haptic(data: bytes) -> tuple[int, int, int]:
    if len(data) < 4 or data[0] != MSG_HAPTIC_FEEDBACK:
        raise ProtocolError("bad haptic message")
    return data[1], data[2], data[3]


class AppInfoReassembler:
    """Accumulates fragments until an APP_INFO parses.

    A central may split the message across writes; every fragment is kept and
    re-parsed with the next one appended.
    """

    MAX_BYTES = 512

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def offer(self, chunk: bytes) -> AppInfo | None:
        self._buffer += chunk
        try:
            info = parse_app_info(bytes(self._buffer))
        except ProtocolError:
            # Bound the buffer so a corrupt stream can't poison every parse.
            if len(self._buffer) > self.MAX_BYTES:
                self._buffer = bytearray(chunk)
            return None
        self._buffer.clear()
        return info
