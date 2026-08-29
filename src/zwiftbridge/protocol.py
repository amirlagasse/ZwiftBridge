"""Zwift Ride BLE protocol: UUIDs, opcodes, button masks, frame decoding.

Protocol facts describe Zwift's hardware. Implemented fresh from the wire
format -- no protobuf library needed, the payloads we care about are a
handful of varints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- BLE identifiers -------------------------------------------------------

RIDE_SERVICE_UUID = "0000fc82-0000-1000-8000-00805f9b34fb"
LEGACY_SERVICE_UUID = "00000001-19ca-4651-86e5-fa29dcdd09d1"
SERVICE_UUIDS = (RIDE_SERVICE_UUID, LEGACY_SERVICE_UUID)

ASYNC_CHAR_UUID = "00000002-19ca-4651-86e5-fa29dcdd09d1"   # notify: button data
SYNC_RX_CHAR_UUID = "00000003-19ca-4651-86e5-fa29dcdd09d1"  # write w/o response
SYNC_TX_CHAR_UUID = "00000004-19ca-4651-86e5-fa29dcdd09d1"  # indicate: responses

ZWIFT_MANUFACTURER_ID = 2378  # 0x094A, Zwift Inc

# Manufacturer-data type byte -> human name.
DEVICE_TYPES = {
    0x02: "Zwift Play (right)",
    0x03: "Zwift Play (left)",
    0x07: "Zwift Ride (right)",
    0x08: "Zwift Ride (left)",
    0x09: "Zwift Click",
    0x0A: "Zwift Click v2 (right)",
    0x0B: "Zwift Click v2 (left)",
    0x0E: "Zwift Play (fw2)",
}
RIDE_TYPE_BYTES = (0x07, 0x08)

# The entire handshake: six ASCII bytes, written without response.
RIDE_ON = b"RideOn"

# --- Haptics ---------------------------------------------------------------
#
# The Ride buzzes on command: write this to SYNC_RX (no response). It is
# opcode 0x12 wrapping two nested protobuf submessages, the innermost being
#   { 1: 2 (pattern), 2: 0 (repeat), 3: <duration> }
# so the only knob worth exposing is the duration byte. 0x20 (32) is the short
# tick the real Zwift app uses for a gear change.
#
# Zwift Play buzzes too; Click and Click v2 do not, and silently ignore this.
OP_VIBRATE = 0x12
VIBRATE_DEFAULT_DURATION = 0x20


def vibrate_command(duration: int = VIBRATE_DEFAULT_DURATION) -> bytes:
    """Bytes to write to SYNC_RX to make the controller buzz once."""
    duration = max(1, min(int(duration), 0x7F))
    inner = bytes((0x08, 0x02, 0x10, 0x00, 0x18, duration))
    return bytes((OP_VIBRATE, 0x12, len(inner) + 2, 0x0A, len(inner))) + inner

# --- Opcodes (first byte of every notification frame) ----------------------

OP_EMPTY = 0x15            # keepalive, no payload of interest
OP_BATTERY_NOTIF = 0x19
OP_BATTERY_STATUS = 0x1A
OP_RIDE_KEYPAD = 0x23      # Zwift Ride controller/button update
OP_CLICK_KEYPAD = 0x37
OP_PLAY_KEYPAD = 0x07
OP_DISCONNECT = 0xFE

OPCODE_NAMES = {
    0x15: "EMPTY",
    0x19: "BATTERY_NOTIF",
    0x1A: "BATTERY_STATUS",
    0x23: "RIDE_KEYPAD",
    0x37: "CLICK_KEYPAD",
    0x07: "PLAY_KEYPAD",
    0xFE: "DISCONNECT",
}

# --- Button masks ----------------------------------------------------------
#
# WARNING: buttons are ACTIVE LOW. A button is pressed when its bit is CLEAR.

BUTTON_MASKS: dict[str, int] = {
    "left":       0x00001,
    "up":         0x00002,
    "right":      0x00004,
    "down":       0x00008,
    "a":          0x00010,
    "b":          0x00020,
    "y":          0x00040,
    "z":          0x00080,
    "shift_up_l": 0x00100,
    "shift_dn_l": 0x00200,
    "powerup_l":  0x00400,
    "onoff_l":    0x00800,
    "shift_up_r": 0x01000,
    "shift_dn_r": 0x02000,
    "powerup_r":  0x04000,
    "onoff_r":    0x08000,
}

# Analog brake paddles arrive in their own protobuf field, not the bitfield.
ANALOG_BUTTONS = ("paddle_l", "paddle_r")
ALL_BUTTONS = tuple(BUTTON_MASKS) + ANALOG_BUTTONS

# Below this magnitude an analog paddle reading is drift, not a squeeze.
ANALOG_THRESHOLD = 25


def is_pressed(button_map: int, mask: int) -> bool:
    """Active low: the bit is CLEAR while the button is held."""
    return (button_map & mask) == 0


# --- Minimal protobuf reader ----------------------------------------------


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("truncated varint")


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a protobuf message.

    value is an int for varint/fixed fields and bytes for length-delimited.
    """
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        field_no, wire = key >> 3, key & 0x07
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 2:
            length, i = _read_varint(buf, i)
            value = buf[i:i + length]
            i += length
        elif wire == 5:
            value = int.from_bytes(buf[i:i + 4], "little")
            i += 4
        elif wire == 1:
            value = int.from_bytes(buf[i:i + 8], "little")
            i += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field_no, wire, value


@dataclass
class KeypadStatus:
    """Decoded RideKeyPadStatus frame."""

    button_map: int = 0xFFFFFFFF  # all bits set == nothing pressed (active low)
    analog: dict[int, int] = field(default_factory=dict)  # location -> -100..100


def parse_ride_keypad(payload: bytes) -> KeypadStatus:
    """Decode the protobuf body of an opcode-0x23 frame.

    field 1 (varint)          -> ButtonMap
    field 3 (repeated message) -> AnalogPaddles{1: Location, 2: sint32 Value}
    """
    status = KeypadStatus()
    for field_no, wire, value in _iter_fields(payload):
        if field_no == 1 and wire == 0:
            status.button_map = value
        elif field_no == 3 and wire == 2:
            location = 0
            analog_value = 0
            for sub_no, sub_wire, sub_value in _iter_fields(value):
                if sub_no == 1 and sub_wire == 0:
                    location = sub_value
                elif sub_no == 2 and sub_wire == 0:
                    analog_value = _zigzag(sub_value)
            status.analog[location] = analog_value
    return status


def parse_battery(payload: bytes) -> int | None:
    """Battery percentage from an 0x19 frame, or None if unreadable."""
    try:
        for field_no, wire, value in _iter_fields(payload):
            if field_no == 1 and wire == 0:
                return value
    except ValueError:
        pass
    return None


# --- Edge detection --------------------------------------------------------


class ButtonTracker:
    """Turns repeated state snapshots into press/release events.

    buttonMap is a full snapshot resent continuously, not an event stream.
    Without this one press becomes dozens of keystrokes.
    """

    def __init__(self) -> None:
        self._held: set[str] = set()

    @property
    def held(self) -> frozenset[str]:
        return frozenset(self._held)

    def update(self, status: KeypadStatus) -> list[tuple[str, bool]]:
        """Return [(button_name, is_press)] for everything that changed."""
        now: set[str] = set()

        for name, mask in BUTTON_MASKS.items():
            if is_pressed(status.button_map, mask):
                now.add(name)

        for location, value in status.analog.items():
            if location < len(ANALOG_BUTTONS) and abs(value) >= ANALOG_THRESHOLD:
                now.add(ANALOG_BUTTONS[location])

        events = [(name, True) for name in sorted(now - self._held)]
        events += [(name, False) for name in sorted(self._held - now)]
        self._held = now
        return events

    def reset(self) -> list[tuple[str, bool]]:
        """Release everything -- call on disconnect so nothing sticks down."""
        events = [(name, False) for name in sorted(self._held)]
        self._held = set()
        return events


# --- encoding (for emulating a Ride toward a trainer app) ------------------


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


def encode_ride_keypad(held: set[str]) -> bytes:
    """Build a RideKeyPadStatus frame for the buttons in `held`.

    Active low, so we start from all bits set and CLEAR the ones pressed.
    Returns the full frame including the leading opcode byte.
    """
    button_map = 0xFFFFFFFF
    for name in held:
        mask = BUTTON_MASKS.get(name)
        if mask:
            button_map &= ~mask
    return bytes([OP_RIDE_KEYPAD, 0x08]) + _encode_varint(button_map)


# Nothing pressed: every bit set. Sent as a keepalive so a trainer app does
# not drop an idle controller.
RELEASED_FRAME = bytes([OP_RIDE_KEYPAD]) + bytes([0x08, 0xFF, 0xFF, 0xFF, 0xFF, 0x0F])
