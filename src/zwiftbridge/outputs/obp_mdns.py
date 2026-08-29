"""OpenBikeProtocol over mDNS -- the iPad path that MyWhoosh can actually find.

We run a TCP server AND advertise it as `_openbikecontrol._tcp`. MyWhoosh
browses for that service, so zwiftbridge turns up in its own device scan and
the user pairs it like any other accessory -- no companion app, no dialling,
no guessing an IP.

The trainer is untouched: your KICKR still pairs to MyWhoosh directly over
Bluetooth. This advertises a *controller*, not a trainer, so the two are
completely independent.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from .. import actions as A
from .. import obp
from ..actions import Action
from .base import Output

log = logging.getLogger("zwiftbridge")


def lan_address() -> str | None:
    """Pick the real LAN IPv4, skipping loopback and VPN/virtual interfaces.

    Advertising an unreachable address is the classic way to be discovered and
    then never connected to.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 1))  # no packets sent; just routes
        address = sock.getsockname()[0]
        return None if address.startswith("127.") else address
    except OSError:
        return None
    finally:
        sock.close()


def _host_label(name: str) -> str:
    """A DNS-safe host label derived from the display name."""
    safe = "".join(c if c.isalnum() or c == "-" else "-" for c in name)
    return safe.strip("-").lower() or "zwiftbridge"


class ObpMdnsOutput(Output):
    name = "obp_mdns"

    def __init__(self, port: int = obp.DEFAULT_PORT,
                 device_name: str = "Zwift Ride (zwiftbridge)",
                 device_id: str = "1337") -> None:
        self.port = port
        self.device_name = device_name
        self.device_id = device_id
        self.bound_port: int | None = None
        self.address: str | None = None
        self.app: obp.AppInfo | None = None
        self.on_client_change = None
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._zeroconf = None
        self._service_info = None

    # --- status ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self._clients and self.app)

    def status(self) -> str:
        if not self._server:
            return "not started"
        where = f"{self.address or '?'}:{self.bound_port}"
        if not self._clients:
            return f"advertising on {where} — pair '{self.device_name}' in MyWhoosh"
        if not self.app:
            return f"client connected from {where}, waiting for app info"
        return f"paired with {self.app.app_id} {self.app.app_version}"

    def _notify(self, message: str) -> None:
        if self.on_client_change:
            self.on_client_change(message)

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        # The port walks under contention and we advertise whichever we got,
        # so a busy 36867 is survivable -- clients read the port from mDNS.
        last_error: OSError | None = None
        for port in range(self.port, self.port + 10):
            try:
                self._server = await asyncio.start_server(
                    self._handle_client, "0.0.0.0", port
                )
                self.bound_port = port
                break
            except OSError as exc:
                last_error = exc
        if self._server is None:
            raise last_error or OSError("could not bind an OBP port")

        self.address = lan_address()
        await self._advertise()

    async def _advertise(self) -> None:
        if not self.address:
            log.warning("no LAN address found -- MyWhoosh will not discover "
                        "zwiftbridge; is Wi-Fi up?")
            return
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            log.error("zeroconf not installed -- run: pip install -r requirements.txt")
            return

        info = ServiceInfo(
            obp.MDNS_SERVICE_TYPE,
            f"{self.device_name}.{obp.MDNS_SERVICE_TYPE}",
            addresses=[socket.inet_aton(self.address)],
            port=self.bound_port,
            properties={
                "version": bytes([0x01]),
                "id": self.device_id.encode(),
                "name": self.device_name.encode(),
                "service-uuids": obp.SERVICE_UUID.encode(),
                "manufacturer": b"OpenBikeControl",
                "model": b"zwiftbridge",
            },
            # The instance name may contain spaces and parens; the host label
            # may not, so it gets its own sanitised form.
            server=f"{_host_label(self.device_name)}.local.",
        )
        self._zeroconf = AsyncZeroconf()
        await self._zeroconf.async_register_service(info)
        self._service_info = info
        log.info("advertising '%s' at %s:%d — pair it in MyWhoosh",
                 self.device_name, self.address, self.bound_port)

    async def stop(self) -> None:
        if self._zeroconf and self._service_info:
            try:
                await self._zeroconf.async_unregister_service(self._service_info)
            except Exception:  # noqa: BLE001 -- teardown must not raise
                pass
        if self._zeroconf:
            await self._zeroconf.async_close()
            self._zeroconf = None
        self._service_info = None

        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.app = None

    # --- client ------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        reassembler = obp.AppInfoReassembler()
        log.info("OBP client connected from %s", peer[0] if peer else "?")
        self._notify("client connected")

        # Announce ourselves as present and healthy.
        try:
            writer.write(obp.encode_device_status(connected=True))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

        try:
            while True:
                chunk = await reader.read(512)
                if not chunk:
                    break
                await self._on_data(chunk, reassembler)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            if not self._clients:
                self.app = None
            log.info("OBP client disconnected")
            self._notify("client disconnected")

    async def _on_data(self, chunk: bytes,
                       reassembler: obp.AppInfoReassembler) -> None:
        kind = chunk[0]
        if kind == obp.MSG_APP_INFO:
            info = reassembler.offer(chunk)
            if info is None:
                return  # split across writes; wait for the rest
            self.app = info
            log.info("paired with %s %s — supports: %s", info.app_id,
                     info.app_version, ", ".join(info.button_names))
            self._notify("app info received")
        elif kind == obp.MSG_HAPTIC_FEEDBACK:
            try:
                pattern, _duration, _intensity = obp.parse_haptic(chunk)
                log.debug("haptic request pattern %d (the Ride can buzz; "
                          "not wired up)", pattern)
            except obp.ProtocolError:
                pass
        else:
            log.debug("unhandled OBP message type 0x%02x", kind)

    # --- sending -----------------------------------------------------------

    async def send(self, action: Action, pressed: bool) -> str | None:
        button_id = obp.ACTION_BUTTONS.get(action.name)
        if button_id is None:
            return None
        if not self._clients:
            return f"{action}: dropped, MyWhoosh not paired"
        if self.app is None:
            return f"{action}: dropped, waiting for app info"
        if not self.app.supports(button_id):
            return f"{action}: {self.app.app_id} does not support this button"

        data = obp.encode_button_state([(button_id, 1 if pressed else 0)])
        dead = []
        for writer in self._clients:
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)

        label = obp.BUTTON_NAMES.get(button_id, f"0x{button_id:02x}")
        return f"{action} -> {label} {'pressed' if pressed else 'released'}"
