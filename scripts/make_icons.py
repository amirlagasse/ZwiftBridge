"""Draws zwiftbridge's icons -- no image library, no design tool.

Two files come out of this, both from the same shapes:

  src/zwiftbridge/assets/icons/icon.png      full-bleed, the panel's favicon
  src/zwiftbridge/assets/icons/icon_app.png  the Dock icon: an Apple-shaped
      squircle inset in a transparent 1024 canvas, the way macOS expects one

Run it after changing anything here, then re-run scripts/install_app.sh so
the bundle picks up the new .icns.  Nothing else imports this file.
"""

from __future__ import annotations

import struct
import zlib

# --- the artwork, in a 64x64 space -------------------------------------
# A lightning Z (Zwift's orange, the controller's spark) crossing a bridge
# deck on two piers (the "bridge" half of the name).
BOLT = [(39, 5), (17, 35), (29, 35), (25, 59), (47, 27), (35, 27)]
DECK = [(8, 48, 56, 48), (19, 48, 19, 56), (45, 48, 45, 56)]
DECK_WIDTH = 4.0
DECK_ALPHA = 0.45

TOP = (255, 128, 66)      # gradient, light at the top
BOTTOM = (226, 84, 22)
FLAT = (242, 101, 34)     # the panel's --accent, used for the favicon


def _in_poly(x: float, y: float, pts) -> bool:
    inside = False
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) / (y2 - y1) * (x2 - x1):
            inside = not inside
    return inside


def _near_deck(x: float, y: float) -> bool:
    for x1, y1, x2, y2 in DECK:
        vx, vy = x2 - x1, y2 - y1
        t = ((x - x1) * vx + (y - y1) * vy) / (vx * vx + vy * vy or 1.0)
        t = min(max(t, 0.0), 1.0)
        if (x - x1 - t * vx) ** 2 + (y - y1 - t * vy) ** 2 <= (DECK_WIDTH / 2) ** 2:
            return True
    return False


def _in_tile(x: float, y: float, squircle: bool) -> bool:
    """Is this point inside the orange tile? 64x64 space, origin at 0,0."""
    if squircle:
        # Apple's icon shape is a superellipse, not a rounded rectangle --
        # the corners of a rounded rect read as visibly "sharper" beside it.
        u = abs(x - 32) / 32
        v = abs(y - 32) / 32
        return u ** 5 + v ** 5 <= 1.0
    r = 14.0
    cx = min(max(x, r), 64 - r)
    cy = min(max(y, r), 64 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def render(size: int, *, squircle: bool, inset: float, gradient: bool,
           samples: int = 2) -> bytes:
    """RGBA PNG bytes. `inset` is the transparent margin, 0..0.5 of a side."""
    art = size * (1 - 2 * inset)          # pixels the tile itself covers
    origin = size * inset
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            tile = deck = bolt = 0
            for sy in range(samples):
                for sx in range(samples):
                    x = ((px + (sx + 0.5) / samples) - origin) * 64 / art
                    y = ((py + (sy + 0.5) / samples) - origin) * 64 / art
                    if not (0 <= x <= 64 and 0 <= y <= 64):
                        continue
                    if not _in_tile(x, y, squircle):
                        continue
                    tile += 1
                    if _in_poly(x, y, BOLT):
                        bolt += 1
                    elif _near_deck(x, y):
                        deck += 1
            n = samples * samples
            if not tile:
                row += b"\0\0\0\0"
                continue
            if gradient:
                t = min(max((py - origin) / art, 0.0), 1.0)
                base = tuple(TOP[i] + (BOTTOM[i] - TOP[i]) * t for i in range(3))
            else:
                base = FLAT
            white = bolt / n + (deck / n) * DECK_ALPHA
            row += bytes(int(c * (1 - white) + 255 * white) for c in base)
            row += bytes((int(255 * tile / n),))
        rows.append(bytes(row))

    raw = b"".join(b"\0" + r for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    from pathlib import Path

    icons = (Path(__file__).resolve().parents[1]
             / "src" / "zwiftbridge" / "assets" / "icons")
    icons.mkdir(parents=True, exist_ok=True)
    (icons / "icon.png").write_bytes(
        render(512, squircle=False, inset=0.0, gradient=False, samples=3))
    print("icon.png      512px, full bleed")
    # 824/1024 is the size Apple's own icons draw their shape at; the rest of
    # the canvas is the margin the Dock expects to be transparent.
    (icons / "icon_app.png").write_bytes(
        render(1024, squircle=True, inset=(1024 - 824) / 2 / 1024,
               gradient=True, samples=2))
    print("icon_app.png  1024px, squircle inset for the Dock")
