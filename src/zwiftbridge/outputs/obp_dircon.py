"""OpenBikeProtocol tunnelled over DirCon -- what MyWhoosh actually browses for.

Sniffing the iPad showed MyWhoosh querying `_wahoo-fitness-tnp._tcp` (DirCon)
repeatedly and `_openbikecontrol._tcp` only incidentally. So we advertise a
DirCon device whose GATT table is the OpenBikeProtocol service, and push
button state as characteristic notifications. Same OBP payloads as the plain
output; different envelope.

Still a CONTROLLER, not a trainer: your KICKR pairs to MyWhoosh directly and
its power never passes through here.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from .. import dircon
from .. import obp
from ..actions import Action
from .base import Output
from .obp_mdns import lan_address, _host_label

log = logging.getLogger("zwiftbridge")

# The GATT table we expose over DirCon: the OpenBikeProtocol service.
CHARACTERISTICS = {
    obp.BUTTON_STATE_CHAR_UUID: dircon.PROP_NOTIFY | dircon.PROP_READ,
    obp.HAPTIC_CHAR_UUID: dircon.PROP_WRITE | dircon.PROP_WRITE_NO_RESPONSE,
    obp.APPINFO_CHAR_UUID: dircon.PROP_WRITE | dircon.PROP_WRITE_NO_RESPONSE,
}


class ObpDirconOutput(Output):
    name = "obp_dircon"

    def __init__(self, port: int = dircon.DEFAULT_PORT,
                 device_name: str = "Zwift Ride (zwiftbridge)",
                 serial: str = "1337000001") -> None:
        self.port = port
        self.device_name = device_name
        self.serial = serial
        self.bound_port: int | None = None
        self.address: str | None = None
        self.app: obp.AppInfo | None = None
        self.on_client_change = None
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._subscribed: set[asyncio.StreamWriter] = set()
        self._zeroconf = None
        self._service_info = None

    # --- status ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self._subscribed)

    def status(self) -> str:
        if not self._server:
            return "not started"
        where = f"{self.address or '?'}:{self.bound_port}"
        if not self._clients:
            return f"advertising DirCon on {where} — pair '{self.device_name}'"
        if not self._subscribed:
            return f"client connected from {where}, negotiating"
        if self.app:
            return f"paired with {self.app.app_id} {self.app.app_version}"
        return "subscribed — shifting live"

    def _notify(self, message: str) -> None:
        if self.on_client_change:
            self.on_client_change(message)

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
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
            raise last_error or OSError("could not bind a DirCon port")

        self.address = lan_address()
        await self._advertise()

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

        # DirCon clients parse serial-number as a 64-bit integer, so it must be
        # all digits -- not a sliced MAC or UUID.
        info = ServiceInfo(
            dircon.MDNS_SERVICE_TYPE,
            f"{self.device_name}.{dircon.MDNS_SERVICE_TYPE}",
            addresses=[socket.inet_aton(self.address)],
            port=self.bound_port,
            properties={
                "ble-service-uuids": obp.SERVICE_UUID.encode(),
                "mac-address": b"02:13:37:00:00:01",
                "serial-number": self.serial.encode(),
                "manufacturer-data": b"zwiftbridge",
            },
            server=f"{_host_label(self.device_name)}-dc.local.",
        )
        self._zeroconf = AsyncZeroconf()
        # strict=False: "_wahoo-fitness-tnp" is 17 bytes and RFC 6763 caps the
        # service name at 15, but that is the name Wahoo actually ships and
        # Apple's responder accepts it -- which is what MyWhoosh browses for.
        await self._zeroconf.async_register_service(info, strict=False)
        self._service_info = info
        log.info("advertising DirCon '%s' at %s:%d — pair it in MyWhoosh",
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
        self._subscribed.clear()
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
        log.info("DirCon client connected from %s", peer[0] if peer else "?")
        self._notify("client connected")

        buffer = b""
        reassembler = obp.AppInfoReassembler()
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    packet, buffer = dircon.decode(buffer)
                    if packet is None:
                        break
                    reply = self._dispatch(packet, writer, reassembler)
                    if reply is not None:
                        writer.write(reply.encode())
                        await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            self._subscribed.discard(writer)
            writer.close()
            if not self._clients:
                self.app = None
            log.info("DirCon client disconnected")
            self._notify("client disconnected")

    def _dispatch(self, packet: dircon.Packet, writer: asyncio.StreamWriter,
                  reassembler: obp.AppInfoReassembler) -> dircon.Packet | None:
        log.debug("DirCon <- %s seq=%d len=%d", packet.name, packet.sequence,
                  len(packet.payload))

        if packet.identifier == dircon.MSG_DISCOVER_SERVICES:
            return dircon.response(packet, dircon.uuid_bytes(obp.SERVICE_UUID))

        if packet.identifier == dircon.MSG_DISCOVER_CHARACTERISTICS:
            if len(packet.payload) < 16:
                return dircon.response(packet, code=dircon.RC_UNEXPECTED_ERROR)
            requested = dircon.bytes_uuid(packet.payload[:16])
            if requested.lower() != obp.SERVICE_UUID.lower():
                return dircon.response(packet, code=dircon.RC_SERVICE_NOT_FOUND)
            body = dircon.uuid_bytes(obp.SERVICE_UUID)
            for char_uuid, properties in CHARACTERISTICS.items():
                body += dircon.uuid_bytes(char_uuid) + bytes([properties])
            return dircon.response(packet, body)

        if packet.identifier == dircon.MSG_ENABLE_NOTIFICATIONS:
            if len(packet.payload) < 16:
                return dircon.response(packet, code=dircon.RC_UNEXPECTED_ERROR)
            char_uuid = dircon.bytes_uuid(packet.payload[:16])
            if char_uuid.lower() != obp.BUTTON_STATE_CHAR_UUID.lower():
                return dircon.response(
                    packet, code=dircon.RC_CHARACTERISTIC_NOT_FOUND
                )
            enable = packet.payload[16] if len(packet.payload) > 16 else 1
            if enable:
                self._subscribed.add(writer)
                log.info("MyWhoosh subscribed to button state — shifting live")
                self._notify("subscribed")
            else:
                self._subscribed.discard(writer)
            return dircon.response(packet, packet.payload[:16])

        if packet.identifier == dircon.MSG_WRITE_CHARACTERISTIC:
            if len(packet.payload) < 16:
                return dircon.response(packet, code=dircon.RC_UNEXPECTED_ERROR)
            char_uuid = dircon.bytes_uuid(packet.payload[:16])
            value = packet.payload[16:]
            if char_uuid.lower() == obp.APPINFO_CHAR_UUID.lower():
                info = reassembler.offer(value)
                if info is not None:
                    self.app = info
                    log.info("paired with %s %s — supports: %s", info.app_id,
                             info.app_version, ", ".join(info.button_names))
                    self._notify("app info received")
            elif char_uuid.lower() == obp.HAPTIC_CHAR_UUID.lower():
                log.debug("haptic request (not wired up)")
            else:
                return dircon.response(
                    packet, code=dircon.RC_CHARACTERISTIC_NOT_FOUND
                )
            return dircon.response(packet, packet.payload[:16])

        if packet.identifier == dircon.MSG_READ_CHARACTERISTIC:
            if len(packet.payload) < 16:
                return dircon.response(packet, code=dircon.RC_UNEXPECTED_ERROR)
            char_uuid = dircon.bytes_uuid(packet.payload[:16])
            if char_uuid.lower() == obp.BUTTON_STATE_CHAR_UUID.lower():
                # Nothing held is the honest resting state.
                return dircon.response(
                    packet, packet.payload[:16] + obp.encode_button_state([])
                )
            return dircon.response(packet, code=dircon.RC_CHARACTERISTIC_NOT_FOUND)

        return dircon.response(packet, code=dircon.RC_UNKNOWN_MESSAGE_TYPE)

    # --- sending -----------------------------------------------------------

    async def send(self, action: Action, pressed: bool) -> str | None:
        button_id = obp.ACTION_BUTTONS.get(action.name)
        if button_id is None:
            return None
        if not self._clients:
            return f"{action}: dropped, MyWhoosh not connected"
        if not self._subscribed:
            return f"{action}: dropped, MyWhoosh has not subscribed yet"
        # The app only advertises its button set on some transports; when it
        # has, respect it. When it hasn't, send anyway rather than sit mute.
        if self.app and not self.app.supports(button_id):
            return f"{action}: {self.app.app_id} does not support this button"

        value = obp.encode_button_state([(button_id, 1 if pressed else 0)])
        data = dircon.notification(obp.BUTTON_STATE_CHAR_UUID, value).encode()
        dead = []
        for writer in self._subscribed:
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                dead.append(writer)
        for writer in dead:
            self._subscribed.discard(writer)
            self._clients.discard(writer)

        label = obp.BUTTON_NAMES.get(button_id, f"0x{button_id:02x}")
        return f"{action} -> {label} {'pressed' if pressed else 'released'}"
