"""Whatever you changed in the panel, remembered across launches.

`config.toml` stays the hand-written defaults and is never rewritten -- its
comments are worth more than the convenience of round-tripping them. This is a
thin JSON overlay on top: only keys you actually changed are stored, so a key
absent here means "whatever config.toml says", not "off".

Delete settings.json and you are back to the file you wrote by hand.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import paths

log = logging.getLogger("zwiftbridge.settings")

DEFAULT_PATH = paths.SETTINGS_FILE

# Only these keys are persisted. Anything else in the file is ignored rather
# than trusted, so a hand-edited settings.json cannot inject arbitrary config.
KEYS = ("outputs", "bindings", "vibrate", "vibrate_ms", "vibrate_buttons")


def load(path: Path | str | None = None) -> dict:
    resolved = Path(path) if path else DEFAULT_PATH
    if not resolved.exists():
        return {}
    try:
        raw = json.loads(resolved.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt overlay must not stop the bridge from starting.
        log.warning("ignoring unreadable %s: %s", resolved.name, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in KEYS}


def save(values: dict, path: Path | str | None = None) -> None:
    resolved = Path(path) if path else DEFAULT_PATH
    keep = {k: v for k, v in values.items() if k in KEYS}
    # Write-and-rename: a crash mid-write leaves the old file, not half a file.
    tmp = resolved.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep, indent=2, sort_keys=True) + "\n")
    tmp.replace(resolved)
