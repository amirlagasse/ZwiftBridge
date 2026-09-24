"""Does SendInput actually land on this machine? (Windows only.)

The keystrokes output fails the same way on Windows as Accessibility does on
macOS: silently. SendInput returns 0 if the INPUT struct is the wrong size, and
returns 1 while landing nowhere if a higher-integrity window has focus. This
checks both without typing anything you can see.

F13 is the probe key. No keyboard has one, nothing is bound to it, and it
produces no character -- so it cannot corrupt whatever you have open.

    .venv\\Scripts\\python.exe scripts\\check_sendinput.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zwiftbridge.outputs import keystrokes as K   # noqa: E402

F13_SCANCODE = 0x64
VK_F13 = 0x7C


def main() -> int:
    if not K.IS_WINDOWS:
        print(f"Windows only; this is {sys.platform}.")
        return 0

    backend = K._WindowsBackend()
    backend.start()
    print(f"sizeof(INPUT)        {ctypes.sizeof(backend._input_type)} bytes "
          f"(expect 40 on 64-bit, 28 on 32-bit)")

    front = backend.front_app()
    print(f"front window         {front}")

    # Borrow the real post() path by naming a key it can look up.
    K.WINDOWS_SCANCODES["f13"] = F13_SCANCODE
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAsyncKeyState(VK_F13)      # clear any stale edge

        backend.post("f13", True)
        time.sleep(0.02)
        held = bool(user32.GetAsyncKeyState(VK_F13) & 0x8000)
        backend.post("f13", False)
        time.sleep(0.02)
        released = not (user32.GetAsyncKeyState(VK_F13) & 0x8000)
    finally:
        K.WINDOWS_SCANCODES.pop("f13", None)

    print(f"key down registered  {held}")
    print(f"key up registered    {released}")

    if held and released:
        print("\nPASS  SendInput reaches the input queue.")
        print("If MyWhoosh still ignores keys, it is running as administrator")
        print("and zwiftbridge is not. Start them the same way.")
        return 0
    print("\nFAIL  the injected key never reached the input queue.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
