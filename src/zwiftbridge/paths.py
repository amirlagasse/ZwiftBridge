r"""Where zwiftbridge keeps its files.

Two situations, one rule: hand-editable files sit beside the checkout when
there is one, and in the usual per-user directory when there isn't.

  * Run from a clone -- how this runs on both of Amir's machines, and how the
    zwiftbridge.app launcher and the Windows shortcut run it -- config.toml and
    settings.json are the ones in the repo root, right where you would look for
    them.
  * Installed as a package there is no repo, so they fall back to the usual
    per-user directory: %LOCALAPPDATA%\zwiftbridge on Windows,
    ~/Library/Application Support/zwiftbridge on macOS, ~/.config/zwiftbridge
    elsewhere.

Assets (ui.html, the icons) always come from inside the package, because they
ship with the code rather than belonging to the user.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ASSETS = PACKAGE / "assets"
ICONS = ASSETS / "icons"
UI_HTML = ASSETS / "ui.html"


def _checkout_root() -> Path | None:
    """The repo root if we are running out of one, else None."""
    for parent in PACKAGE.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


CHECKOUT = _checkout_root()


def user_dir() -> Path:
    """Directory for files the rider edits or the panel writes."""
    if CHECKOUT is not None:
        return CHECKOUT
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        home = (Path(base) if base else Path.home() / "AppData" / "Local") / "zwiftbridge"
    elif sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support" / "zwiftbridge"
    else:
        home = Path.home() / ".config" / "zwiftbridge"
    home.mkdir(parents=True, exist_ok=True)
    return home


CONFIG_FILE = user_dir() / "config.toml"
SETTINGS_FILE = user_dir() / "settings.json"
