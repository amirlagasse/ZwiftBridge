from __future__ import annotations

from ..actions import Action
from .base import Output


class ConsoleOutput(Output):
    """Prints what would be sent. The safe default while mapping buttons."""

    name = "console"

    async def send(self, action: Action, pressed: bool) -> str | None:
        if not pressed and not action.is_hold:
            return None
        return f"{action} {'down' if pressed else 'up'}"
