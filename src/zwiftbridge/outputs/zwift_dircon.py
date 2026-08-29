"""Emulate a Zwift Ride over DirCon -- a controller MyWhoosh already supports.

Your KICKR advertises `ble-service-uuids=0x1818,0x1826,0xFC82` over
`_wahoo-fitness-tnp._tcp`. 0xFC82 is the Zwift Ride controller service, so
that is the shape MyWhoosh understands. Rather than teaching it a new device,
we present the Ride's own service over DirCon and re-emit the very frames we
decoded from the real controller.

Still a controller, never a trainer: the KICKR's power does not pass through.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from .. import dircon
from .. import protocol as P
from ..actions import Action
from .base import Output
from .obp_mdns import lan_address, _host_label

log = logging.getLogger("zwiftbridge")

# A trainer app drops an idle wired controller after ~30s, so re-assert the
# neutral state well inside that window.
KEEPALIVE_SECONDS = 20.0

# Which physical Ride button expresses each action. Mirrors the stock Ride:
# the right-hand up paddle shifts up, the left-hand up paddle shifts down.
ACTION_BUTTONS = {
    "shift_up": "shift_up_r",
    "shift_down": "shift_up_l",
    "steer_left": "left",
    "steer_right": "right",
    "nav_up": "up",
    "nav_down": "down",
    "nav_left": "left",
    "nav_right": "right",
    "select": "a",
    "back": "b",
    "menu": "y",
    "emote": "z",
    "uturn": "down",
    "toggle_ui": "onoff_r",
}


class ZwiftDirconOutput(Output):
    name = "zwift_dircon"

    def __init__(self, port: int = dircon.DEFAULT_PORT,
                 device_name: str = "Zwift Ride (zwiftbridge)",
                 serial: str = "1337000002") -> None:
        self.port = port
        self.device_name = device_name
        self.serial = serial
        self.bound_port: int | None = None
        self.address: str | None = None
        self.on_client_change = None
        self._held: set[str] = set()
        self._server: asyncio.AbstractServer | None = None
        self._zeroconf = None
        self._service_info = None
        self._keepalive: asyncio.Task | None = None

        self.dircon = dircon.DirconServer(
            dircon.Service(P.RIDE_SERVICE_UUID, [
                dircon.Characteristic(
                    P.SYNC_RX_CHAR_UUID,
                    dircon.PROP_WRITE | dircon.PROP_WRITE_NO_RESPONSE,
                    on_write=self._on_sync_write,
                ),
                dircon.Characteristic(
                    P.ASYNC_CHAR_UUID, dircon.PROP_NOTIFY | dircon.PROP_READ,
                    on_read=lambda: P.RELEASED_FRAME,
                ),
                dircon.Characteristic(
                    P.SYNC_TX_CHAR_UUID, dircon.PROP_NOTIFY | dircon.PROP_INDICATE,
                ),
            ]),
            on_client_change=self._on_change,
        )

    # --- status ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self.dircon.subscribers(P.ASYNC_CHAR_UUID))

    def status(self) -> str:
        if not self._server:
            return "not started"
        where = f"{self.address or '?'}:{self.bound_port}"
        if not self.dircon.clients:
            return f"advertising as a Zwift Ride on {where} — pair '{self.device_name}'"
        if not self.ready:
            return "client connected, waiting for it to subscribe"
        return "paired — shifting live"

    def _on_change(self, message: str) -> None:
        if self.on_client_change:
            self.on_client_change(message)

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        last_error: OSError | None = None
        for port in range(self.port, self.port + 10):
            try:
                self._server = await asyncio.start_server(
                    self.dircon.handle_client, "0.0.0.0", port
                )
                self.bound_port = port
                break
            except OSError as exc:
                last_error = exc
        if self._server is None:
            raise last_error or OSError("could not bind a DirCon port")

        self.address = lan_address()
        await self._advertise()
        self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def _advertise(self) -> None:
        if not self.address:
            log.warning("no LAN address -- MyWhoosh cannot discover us")
            return
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            log.error("zeroconf missing -- pip install -r requirements.txt")
            return

        # Match the KICKR's TXT shape exactly: short 0x-form service ids,
        # a hyphenated MAC, and an all-digit serial (DirCon parses it as an
        # integer). Our earlier colon-separated MAC and 128-bit uuid were the
        # likely reason MyWhoosh listed us but never dialled.
        info = ServiceInfo(
            dircon.MDNS_SERVICE_TYPE,
            f"{self.device_name}.{dircon.MDNS_SERVICE_TYPE}",
            addresses=[socket.inet_aton(self.address)],
            port=self.bound_port,
            properties={
                "ble-service-uuids": b"0xFC82",
                "mac-address": b"02-13-37-00-00-02",
                "serial-number": self.serial.encode(),
            },
            server=f"{_host_label(self.device_name)}-zr.local.",
        )
        self._zeroconf = AsyncZeroconf()
        await self._zeroconf.async_register_service(info, strict=False)
        self._service_info = info
        log.info("advertising Zwift Ride '%s' at %s:%d — pair it in MyWhoosh",
                 self.device_name, self.address, self.bound_port)

    async def stop(self) -> None:
        if self._keepalive:
            self._keepalive.cancel()
            try:
                await self._keepalive
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
            self._keepalive = None
        if self._zeroconf and self._service_info:
            try:
                await self._zeroconf.async_unregister_service(self._service_info)
            except Exception:  # noqa: BLE001 -- teardown must not raise
                pass
        if self._zeroconf:
            await self._zeroconf.async_close()
            self._zeroconf = None
        self._service_info = None
        await self.dircon.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # --- handshake ---------------------------------------------------------

    def _on_sync_write(self, value: bytes) -> None:
        """The client's end of the RideOn handshake."""
        if value.startswith(P.RIDE_ON):
            log.info("RideOn handshake from the app — answering")
            # A real Ride answers with RideOn + two bytes (then a key we do not
            # use). The app only logs the remainder, so this is enough.
            asyncio.create_task(
                self.dircon.notify(P.SYNC_TX_CHAR_UUID, P.RIDE_ON + b"\x01\x03")
            )

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            if self.ready and not self._held:
                await self.dircon.notify(P.ASYNC_CHAR_UUID, P.RELEASED_FRAME)

    # --- sending -----------------------------------------------------------

    async def send(self, action: Action, pressed: bool) -> str | None:
        button = ACTION_BUTTONS.get(action.name)
        if button is None:
            return None
        if not self.dircon.clients:
            return f"{action}: dropped, MyWhoosh not connected"
        if not self.ready:
            return f"{action}: dropped, MyWhoosh has not subscribed yet"

        if pressed:
            self._held.add(button)
        else:
            self._held.discard(button)

        frame = P.encode_ride_keypad(self._held)
        sent = await self.dircon.notify(P.ASYNC_CHAR_UUID, frame)
        if not sent:
            return f"{action}: no subscriber took it"
        return (f"{action} -> Ride {button} "
                f"{'pressed' if pressed else 'released'}")
