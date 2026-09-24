"""Human names and physical layout for the control panel.

The rest of the codebase talks in wire names: `shift_dn_r`, `emote:3`,
`toggle_ui`. Those are the right names for code and the wrong ones for someone
setting up their bike, so every user-facing string lives here, once. The panel
fetches this table rather than hard-coding a second copy in JavaScript.

`row`/`col` place a button on a 3-column grid drawn to look like the half of
the controller it sits on, so the thing you click on screen is in the same
place as the thing under your thumb.
"""

from __future__ import annotations

import sys

from . import actions as A

# --- Buttons ---------------------------------------------------------------
#
# Named by where they are, not what they do: the binding underneath can be
# changed, but "the top paddle on the left shifter" is always the same lump of
# plastic. side/row/col drive the on-screen layout.

BUTTONS = [
    # Left half: d-pad on top, then the shifter, then the paddle.
    {"id": "up",         "name": "D-pad up",        "side": "left",  "row": 0, "col": 1, "group": "dpad"},
    {"id": "left",       "name": "D-pad left",      "side": "left",  "row": 1, "col": 0, "group": "dpad"},
    {"id": "down",       "name": "D-pad down",      "side": "left",  "row": 2, "col": 1, "group": "dpad"},
    {"id": "right",      "name": "D-pad right",     "side": "left",  "row": 1, "col": 2, "group": "dpad"},
    {"id": "shift_up_l", "name": "Left shifter, top",    "side": "left", "row": 3, "col": 0, "group": "shifter", "span": 2},
    {"id": "powerup_l",  "name": "Left power-up",        "side": "left", "row": 3, "col": 2, "group": "extra"},
    {"id": "shift_dn_l", "name": "Left shifter, bottom", "side": "left", "row": 4, "col": 0, "group": "shifter", "span": 2},
    {"id": "onoff_l",    "name": "Left Zwift button",    "side": "left", "row": 4, "col": 2, "group": "extra"},
    {"id": "paddle_l",   "name": "Left brake paddle",    "side": "left", "row": 5, "col": 0, "group": "paddle", "span": 3},

    # Right half: the A/B/Y/Z diamond, then the same shifter and paddle.
    {"id": "y",          "name": "Y button",        "side": "right", "row": 0, "col": 1, "group": "face"},
    {"id": "z",          "name": "Z button",        "side": "right", "row": 1, "col": 0, "group": "face"},
    {"id": "b",          "name": "B button",        "side": "right", "row": 2, "col": 1, "group": "face"},
    {"id": "a",          "name": "A button",        "side": "right", "row": 1, "col": 2, "group": "face"},
    {"id": "shift_up_r", "name": "Right shifter, top",    "side": "right", "row": 3, "col": 1, "group": "shifter", "span": 2},
    {"id": "powerup_r",  "name": "Right power-up",        "side": "right", "row": 3, "col": 0, "group": "extra"},
    {"id": "shift_dn_r", "name": "Right shifter, bottom", "side": "right", "row": 4, "col": 1, "group": "shifter", "span": 2},
    {"id": "onoff_r",    "name": "Right Zwift button",    "side": "right", "row": 4, "col": 0, "group": "extra"},
    {"id": "paddle_r",   "name": "Right brake paddle",    "side": "right", "row": 5, "col": 0, "group": "paddle", "span": 3},
]

BUTTON_NAMES = {b["id"]: b["name"] for b in BUTTONS}

# The paddles are squeezed and held rather than clicked, so the panel warns
# before letting you put a buzz on one.
HELD_BUTTONS = ("paddle_l", "paddle_r")


# --- Actions ---------------------------------------------------------------
#
# Grouped the way someone picking one thinks about it, not the way the code
# files them. `hint` is shown under the name in the picker; keep it to the one
# thing that is not obvious from the name.

EMOTE_NAMES = {
    1: "Peace", 2: "Wave", 3: "Fist bump", 4: "Dab",
    5: "Elbow flick", 6: "Toast", 7: "Thumbs up",
}

ACTION_GROUPS = [
    {
        "name": "Gears",
        "actions": [
            {"id": A.SHIFT_UP, "name": "Shift up", "hint": "Harder gear"},
            {"id": A.SHIFT_DOWN, "name": "Shift down", "hint": "Easier gear"},
        ],
    },
    {
        "name": "Steering",
        "actions": [
            {"id": A.STEER_LEFT, "name": "Steer left", "hint": "While held"},
            {"id": A.STEER_RIGHT, "name": "Steer right", "hint": "While held"},
            {"id": A.UTURN, "name": "U-turn", "hint": "Turn around"},
            {"id": A.TUCK, "name": "Tuck", "hint": "Aero on descents"},
        ],
    },
    {
        "name": "Screen",
        "actions": [
            {"id": A.MINIMAL_UI, "name": "Minimal display", "hint": "Hide most overlays"},
            {"id": A.TOGGLE_UI, "name": "Hide display", "hint": "MyWhoosh HD only"},
            {"id": A.CAMERA, "name": "Change camera", "hint": "Next view"},
            {"id": A.FULLSCREEN, "name": "Fullscreen", "hint": "On this computer"},
        ],
    },
    {
        "name": "Menus",
        "actions": [
            {"id": A.SELECT, "name": "Select", "hint": "Confirm"},
            {"id": A.BACK, "name": "Back", "hint": "Cancel"},
            {"id": A.MENU, "name": "Menu", "hint": "Open the menu"},
            {"id": A.NAV_UP, "name": "Move up", "hint": ""},
            {"id": A.NAV_DOWN, "name": "Move down", "hint": ""},
            {"id": A.NAV_LEFT, "name": "Move left", "hint": ""},
            {"id": A.NAV_RIGHT, "name": "Move right", "hint": ""},
        ],
    },
    {
        "name": "Emotes",
        "actions": [
            {"id": f"{A.EMOTE}:{n}", "name": name, "hint": ""}
            for n, name in EMOTE_NAMES.items()
        ],
    },
    {
        "name": "Off",
        "actions": [
            {"id": A.NONE, "name": "Do nothing", "hint": "Leave this button unused"},
        ],
    },
]


def action_name(spec: str | None) -> str:
    """Friendly name for a binding like "emote:3", or "Do nothing"."""
    if not spec or spec == A.NONE:
        return "Do nothing"
    for group in ACTION_GROUPS:
        for action in group["actions"]:
            if action["id"] == spec:
                return action["name"]
    # camera:2 and friends: the group only lists camera:1, so fall back to the
    # base name rather than showing a raw wire string.
    base = spec.split(":")[0]
    for group in ACTION_GROUPS:
        for action in group["actions"]:
            if action["id"] == base:
                return action["name"]
    return spec


# --- Outputs ---------------------------------------------------------------
#
# The keystrokes route has a different way of failing silently on each
# platform, and the hint is the only place the rider ever sees it.
KEYSTROKE_CAVEAT = {
    "darwin": " Needs Accessibility permission.",
    "win32": " If MyWhoosh runs as administrator, start zwiftbridge the same way.",
}.get(sys.platform, "")

# The panel puts the recommended outputs up top and folds the rest away. Which
# one deserves that depends on where the game is: on Windows MyWhoosh runs on
# this same box, so keystrokes needs no network and no pairing at all. On macOS
# the Wi-Fi route to a tablet is the one that was built for.
KEYSTROKES_FIRST = sys.platform == "win32"


OUTPUTS = [
    {
        "id": "zwift_dircon",
        "name": "Over Wi-Fi",
        "hint": "MyWhoosh on a tablet or another computer. Pairs like a real "
                "Zwift Ride. Both devices must be on the same network.",
        "recommended": not KEYSTROKES_FIRST,
    },
    {
        "id": "keystrokes",
        "name": "On this computer",
        "hint": "Types into MyWhoosh running here. MyWhoosh has to be the "
                "front window." + KEYSTROKE_CAVEAT,
        "recommended": KEYSTROKES_FIRST,
    },
    {
        "id": "obp_dircon",
        "name": "Wi-Fi (OpenBikeProtocol)",
        "hint": "Alternative Wi-Fi protocol. Try this only if the first one "
                "will not pair.",
        "recommended": False,
    },
    {
        "id": "obp_mdns",
        "name": "Wi-Fi (plain OBP)",
        "hint": "Same protocol without the Wahoo wrapper. Rarely what you want.",
        "recommended": False,
    },
    {
        "id": "whoosh_link",
        "name": "MyWhoosh Link",
        "hint": "Needs the MyWhoosh Link companion app to have run once. It "
                "has never connected here.",
        "recommended": False,
    },
    {
        "id": "console",
        "name": "Print only",
        "hint": "Sends nothing anywhere. Use it to check which button is which.",
        "recommended": False,
    },
]

# Which actions each output can actually carry, so the panel can say "this
# button will do nothing" instead of letting you bind something inert.
KEYSTROKE_ACTIONS = frozenset({
    A.SHIFT_UP, A.SHIFT_DOWN, A.STEER_LEFT, A.STEER_RIGHT, A.TOGGLE_UI,
    A.MINIMAL_UI, A.FULLSCREEN, A.NAV_LEFT, A.NAV_RIGHT, A.NAV_UP,
    A.NAV_DOWN, A.SELECT, A.BACK, A.MENU, A.EMOTE,
})
NETWORK_ACTIONS = frozenset({
    A.SHIFT_UP, A.SHIFT_DOWN, A.STEER_LEFT, A.STEER_RIGHT, A.NAV_UP,
    A.NAV_DOWN, A.NAV_LEFT, A.NAV_RIGHT, A.SELECT, A.BACK, A.MENU,
    A.TOGGLE_UI, A.EMOTE, A.CAMERA, A.UTURN, A.TUCK,
})


def vocabulary() -> dict:
    """Everything the panel needs to render itself, in one payload."""
    return {
        "buttons": BUTTONS,
        "held_buttons": list(HELD_BUTTONS),
        "action_groups": ACTION_GROUPS,
        "outputs": OUTPUTS,
        "keystroke_actions": sorted(KEYSTROKE_ACTIONS),
        "network_actions": sorted(NETWORK_ACTIONS),
    }
