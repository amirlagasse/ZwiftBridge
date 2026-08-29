"""DirCon (Wahoo Direct Connect) framing.

DirCon carries a BLE service over TCP: a client discovers services and
characteristics, subscribes to notifications, and reads/writes -- exactly the
GATT operations, tunnelled. MyWhoosh browses for `_wahoo-fitness-tnp._tcp` and
speaks this, which is why the OpenBikeProtocol-over-plain-TCP output was never
dialled: the app was asking a different question.

Packet layout (6-byte header, big-endian length):

    0  message version (0x01)
    1  identifier / message type
    2  sequence number
    3  response code
    4  payload length, high byte
    5  payload length, low byte
    6+ payload

UUIDs travel as 16 raw bytes (128-bit, big-endian).
"""

from __future__ import annotations

import struct
import uuid as uuid_module
from dataclasses import dataclass

VERSION = 0x01
HEADER_LEN = 6

# --- message types ---------------------------------------------------------

MSG_DISCOVER_SERVICES = 0x01
MSG_DISCOVER_CHARACTERISTICS = 0x02
MSG_READ_CHARACTERISTIC = 0x03
MSG_WRITE_CHARACTERISTIC = 0x04
MSG_ENABLE_NOTIFICATIONS = 0x05
MSG_CHARACTERISTIC_NOTIFICATION = 0x06

MESSAGE_NAMES = {
    0x01: "DISCOVER_SERVICES",
    0x02: "DISCOVER_CHARACTERISTICS",
    0x03: "READ_CHARACTERISTIC",
    0x04: "WRITE_CHARACTERISTIC",
    0x05: "ENABLE_NOTIFICATIONS",
    0x06: "CHARACTERISTIC_NOTIFICATION",
}

# --- response codes --------------------------------------------------------

RC_SUCCESS = 0
RC_UNKNOWN_MESSAGE_TYPE = 1
RC_UNEXPECTED_ERROR = 2
RC_SERVICE_NOT_FOUND = 3
RC_CHARACTERISTIC_NOT_FOUND = 4
RC_OPERATION_NOT_SUPPORTED = 5
RC_WRITE_FAILED_INVALID_SIZE = 6
RC_UNKNOWN_PROTOCOL_VERSION = 7

# --- BLE characteristic properties (standard GATT bitmask) -----------------

PROP_READ = 0x02
PROP_WRITE_NO_RESPONSE = 0x04
PROP_WRITE = 0x08
PROP_NOTIFY = 0x10
PROP_INDICATE = 0x20

# The DirCon service type MyWhoosh browses for.
MDNS_SERVICE_TYPE = "_wahoo-fitness-tnp._tcp.local."
DEFAULT_PORT = 36866


def uuid_bytes(value: str) -> bytes:
    """128-bit UUID as 16 big-endian bytes."""
    return uuid_module.UUID(value).bytes


def bytes_uuid(raw: bytes) -> str:
    return str(uuid_module.UUID(bytes=raw))


@dataclass
class Packet:
    identifier: int
    sequence: int = 0
    response_code: int = RC_SUCCESS
    payload: bytes = b""
    version: int = VERSION

    def encode(self) -> bytes:
        return struct.pack(
            "!BBBBH", self.version, self.identifier, self.sequence,
            self.response_code, len(self.payload),
        ) + self.payload

    @property
    def name(self) -> str:
        return MESSAGE_NAMES.get(self.identifier, f"0x{self.identifier:02x}")


def decode(buffer: bytes) -> tuple[Packet | None, bytes]:
    """Pull one packet off the front of `buffer`.

    Returns (packet, rest). packet is None when more bytes are needed --
    TCP gives no message boundaries, so the caller keeps the remainder.
    """
    if len(buffer) < HEADER_LEN:
        return None, buffer
    version, identifier, sequence, code, length = struct.unpack(
        "!BBBBH", buffer[:HEADER_LEN]
    )
    if len(buffer) < HEADER_LEN + length:
        return None, buffer
    payload = buffer[HEADER_LEN:HEADER_LEN + length]
    packet = Packet(identifier, sequence, code, payload, version)
    return packet, buffer[HEADER_LEN + length:]


def response(request: Packet, payload: bytes = b"",
             code: int = RC_SUCCESS) -> Packet:
    """A reply carrying the request's identifier and sequence number."""
    return Packet(request.identifier, request.sequence, code, payload)


def notification(char_uuid: str, value: bytes) -> Packet:
    """An unsolicited characteristic notification."""
    return Packet(
        MSG_CHARACTERISTIC_NOTIFICATION, 0, RC_SUCCESS,
        uuid_bytes(char_uuid) + value,
    )


# --- a reusable DirCon server ----------------------------------------------

import asyncio  # noqa: E402
import logging  # noqa: E402
from typing import Callable  # noqa: E402

log = logging.getLogger("zwiftbridge")


class Characteristic:
    def __init__(self, uuid: str, properties: int,
                 on_write: Callable[[bytes], None] | None = None,
                 on_read: Callable[[], bytes] | None = None) -> None:
        self.uuid = uuid.lower()
        self.properties = properties
        self.on_write = on_write
        self.on_read = on_read


class Service:
    def __init__(self, uuid: str, characteristics: list[Characteristic]) -> None:
        self.uuid = uuid.lower()
        self.characteristics = characteristics

    def find(self, uuid: str) -> Characteristic | None:
        uuid = uuid.lower()
        return next((c for c in self.characteristics if c.uuid == uuid), None)


class DirconServer:
    """Serves one BLE service over DirCon: discovery, subscribe, read, write.

    The transport is deliberately separate from what is being served, so the
    OpenBikeProtocol service and the Zwift Ride service share this code.
    """

    def __init__(self, service: Service, on_client_change=None) -> None:
        self.service = service
        self.on_client_change = on_client_change
        self.clients: set[asyncio.StreamWriter] = set()
        # writer -> set of characteristic uuids it has subscribed to
        self.subscriptions: dict[asyncio.StreamWriter, set[str]] = {}

    def subscribers(self, char_uuid: str) -> list[asyncio.StreamWriter]:
        char_uuid = char_uuid.lower()
        return [w for w, subs in self.subscriptions.items() if char_uuid in subs]

    def _notify_change(self, message: str) -> None:
        if self.on_client_change:
            self.on_client_change(message)

    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self.clients.add(writer)
        self.subscriptions[writer] = set()
        log.info("DirCon client connected from %s", peer[0] if peer else "?")
        self._notify_change("client connected")

        buffer = b""
        try:
            while True:
                chunk = await reader.read(2048)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    packet, buffer = decode(buffer)
                    if packet is None:
                        break
                    reply = self._dispatch(packet, writer)
                    if reply is not None:
                        writer.write(reply.encode())
                        await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(writer)
            self.subscriptions.pop(writer, None)
            writer.close()
            log.info("DirCon client disconnected")
            self._notify_change("client disconnected")

    def _dispatch(self, packet: Packet, writer: asyncio.StreamWriter):
        log.debug("DirCon <- %s seq=%d payload=%s", packet.name, packet.sequence,
                  packet.payload.hex(" ")[:64])

        if packet.identifier == MSG_DISCOVER_SERVICES:
            return response(packet, uuid_bytes(self.service.uuid))

        if packet.identifier == MSG_DISCOVER_CHARACTERISTICS:
            if len(packet.payload) < 16:
                return response(packet, code=RC_UNEXPECTED_ERROR)
            if bytes_uuid(packet.payload[:16]).lower() != self.service.uuid:
                return response(packet, code=RC_SERVICE_NOT_FOUND)
            body = uuid_bytes(self.service.uuid)
            for char in self.service.characteristics:
                body += uuid_bytes(char.uuid) + bytes([char.properties])
            return response(packet, body)

        if packet.identifier == MSG_ENABLE_NOTIFICATIONS:
            if len(packet.payload) < 16:
                return response(packet, code=RC_UNEXPECTED_ERROR)
            uuid_str = bytes_uuid(packet.payload[:16]).lower()
            char = self.service.find(uuid_str)
            if char is None:
                return response(packet, code=RC_CHARACTERISTIC_NOT_FOUND)
            enable = packet.payload[16] if len(packet.payload) > 16 else 1
            if enable:
                self.subscriptions.setdefault(writer, set()).add(uuid_str)
                log.info("client subscribed to %s", uuid_str[:8])
                self._notify_change("subscribed")
            else:
                self.subscriptions.get(writer, set()).discard(uuid_str)
            return response(packet, packet.payload[:16])

        if packet.identifier == MSG_WRITE_CHARACTERISTIC:
            if len(packet.payload) < 16:
                return response(packet, code=RC_UNEXPECTED_ERROR)
            uuid_str = bytes_uuid(packet.payload[:16]).lower()
            char = self.service.find(uuid_str)
            if char is None:
                return response(packet, code=RC_CHARACTERISTIC_NOT_FOUND)
            if char.on_write:
                try:
                    char.on_write(packet.payload[16:])
                except Exception:  # noqa: BLE001 -- a bad write must not kill the link
                    log.debug("write handler raised", exc_info=True)
            return response(packet, packet.payload[:16])

        if packet.identifier == MSG_READ_CHARACTERISTIC:
            if len(packet.payload) < 16:
                return response(packet, code=RC_UNEXPECTED_ERROR)
            uuid_str = bytes_uuid(packet.payload[:16]).lower()
            char = self.service.find(uuid_str)
            if char is None:
                return response(packet, code=RC_CHARACTERISTIC_NOT_FOUND)
            value = char.on_read() if char.on_read else b""
            return response(packet, packet.payload[:16] + value)

        return response(packet, code=RC_UNKNOWN_MESSAGE_TYPE)

    async def notify(self, char_uuid: str, value: bytes) -> int:
        """Push a characteristic notification. Returns how many clients got it."""
        data = notification(char_uuid, value).encode()
        sent = 0
        dead = []
        for writer in self.subscribers(char_uuid):
            try:
                writer.write(data)
                await writer.drain()
                sent += 1
            except (ConnectionResetError, BrokenPipeError):
                dead.append(writer)
        for writer in dead:
            self.clients.discard(writer)
            self.subscriptions.pop(writer, None)
        return sent

    async def close(self) -> None:
        for writer in list(self.clients):
            writer.close()
        self.clients.clear()
        self.subscriptions.clear()
