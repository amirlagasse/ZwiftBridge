"""MyWhoosh Link output -- talks to MyWhoosh over the local network.

This is the path that makes the iPad work. MyWhoosh ships a "Link" companion
protocol: *we* listen on TCP 21587 and the MyWhoosh app dials in as the
client, then we push newline-delimited JSON control messages at it.

Because it is a network protocol there is no Accessibility permission, no
synthetic keystrokes, and no requirement that MyWhoosh be the frontmost app --
it does not even have to be on this machine.
"""

from __future__ import annotations

import asyncio
import json

from .. import actions as A
from ..actions import Action
from .base import Output

# Fixed-port contract: MyWhoosh dials this exact port. Never fall back to
# another one -- a blocked port has to fail loudly or you get a silent no-op.
LINK_PORT = 21587


def _control(payload: dict[str, str]) -> bytes:
    return (json.dumps({"MessageType": "Controls", "InGameControls": payload})
            + "\n").encode()


class WhooshLinkOutput(Output):
    name = "whoosh_link"

    def __init__(self, host: str = "0.0.0.0", port: int = LINK_PORT) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.on_client_change = None  # optional callback(count)

    @property
    def ready(self) -> bool:
        return bool(self._clients)

    def status(self) -> str:
        if not self._server:
            return "not listening"
        if not self._clients:
            return f"listening on :{self.port}, waiting for MyWhoosh"
        return f"connected ({len(self._clients)} client)"

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

    async def stop(self) -> None:
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        self._notify(f"MyWhoosh connected from {peer[0]}")
        try:
            # MyWhoosh chats at us; we have no use for it, but the read keeps
            # the connection healthy and detects a clean disconnect.
            while await reader.readline():
                pass
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            self._notify("MyWhoosh disconnected")

    def _notify(self, message: str) -> None:
        if self.on_client_change:
            self.on_client_change(message)

    async def send(self, action: Action, pressed: bool) -> str | None:
        payload = self._payload(action, pressed)
        if payload is None:
            return None
        if not self._clients:
            return f"{action}: dropped, MyWhoosh not connected"
        data = _control(payload)
        dead = []
        for writer in self._clients:
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)
        return f"{action} -> {json.dumps(payload)}"

    def _payload(self, action: Action, pressed: bool) -> dict[str, str] | None:
        # Steering is the only stateful one: it needs the release to re-centre.
        if action.name == A.STEER_LEFT:
            return {"Steering": "-1" if pressed else "0"}
        if action.name == A.STEER_RIGHT:
            return {"Steering": "1" if pressed else "0"}

        if not pressed:
            return None

        if action.name == A.SHIFT_UP:
            return {"GearShifting": "1"}
        if action.name == A.SHIFT_DOWN:
            return {"GearShifting": "-1"}
        if action.name == A.EMOTE:
            return {"Emote": str(action.arg or 1)}
        if action.name == A.CAMERA:
            return {"CameraAngle": str(action.arg or 1)}
        if action.name == A.UTURN:
            return {"UTurn": "true"}
        if action.name == A.TUCK:
            return {"Tuck": "true"}
        # nav_*, select, back, menu and the UI toggles have no Link equivalent.
        return None
