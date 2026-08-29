"""BLE transport for the Zwift Ride: scan, connect, handshake, notifications."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from . import protocol as P

log = logging.getLogger("zwiftbridge.ble")


@dataclass
class Discovered:
    device: BLEDevice
    type_byte: int | None
    type_name: str
    rssi: int
    raw_manufacturer: bytes

    @property
    def address(self) -> str:
        return self.device.address

    @property
    def name(self) -> str:
        return self.device.name or "(unnamed)"

    @property
    def is_ride(self) -> bool:
        return self.type_byte in P.RIDE_TYPE_BYTES


def _classify(device: BLEDevice, adv: AdvertisementData) -> Discovered | None:
    raw = adv.manufacturer_data.get(P.ZWIFT_MANUFACTURER_ID)
    has_service = any(
        uuid.lower() in P.SERVICE_UUIDS for uuid in (adv.service_uuids or [])
    )
    if raw is None and not has_service:
        return None
    type_byte = raw[0] if raw else None
    return Discovered(
        device=device,
        type_byte=type_byte,
        type_name=P.DEVICE_TYPES.get(type_byte, "unknown Zwift device"),
        rssi=adv.rssi,
        raw_manufacturer=bytes(raw or b""),
    )


async def scan(timeout: float = 8.0) -> list[Discovered]:
    """Return every Zwift device seen during `timeout` seconds."""
    found: dict[str, Discovered] = {}

    def on_detect(device: BLEDevice, adv: AdvertisementData) -> None:
        hit = _classify(device, adv)
        if hit:
            found[device.address] = hit

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()
    return sorted(found.values(), key=lambda d: -d.rssi)


async def find_rides(timeout: float = 15.0, want: int = 2) -> list[Discovered]:
    """Scan for Zwift Ride halves, returning early once `want` are seen.

    The Ride presents as TWO independent BLE peripherals -- left (type 0x08)
    and right (0x07) -- each reporting only its own buttons. Connect to one
    and half the bike is dead, which is exactly what it looks like.
    """
    found: dict[str, Discovered] = {}
    enough = asyncio.Event()

    def on_detect(device: BLEDevice, adv: AdvertisementData) -> None:
        hit = _classify(device, adv)
        if hit and hit.is_ride:
            found.setdefault(device.address, hit)
            if len(found) >= want:
                enough.set()

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.wait_for(enough.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return sorted(found.values(), key=lambda d: -d.rssi)


async def find_ride(timeout: float = 15.0) -> Discovered | None:
    """First Ride half seen, or None."""
    hits = await find_rides(timeout, want=1)
    return hits[0] if hits else None


FrameHandler = Callable[[int, bytes], Awaitable[None] | None]


class RideLink:
    """One connected Zwift Ride. Handles the handshake and frame dispatch."""

    def __init__(self, address: str, on_frame: FrameHandler) -> None:
        self.address = address
        self.on_frame = on_frame
        self.battery: int | None = None
        self._client: BleakClient | None = None
        self._service_uuid: str | None = None

    async def connect(self, disconnect_cb=None) -> None:
        client = BleakClient(self.address, disconnected_callback=disconnect_cb)
        await client.connect()
        self._client = client

        service = None
        for uuid in P.SERVICE_UUIDS:
            service = client.services.get_service(uuid)
            if service:
                break
        if service is None:
            available = ", ".join(s.uuid for s in client.services)
            raise RuntimeError(
                "Zwift custom service not found -- the Ride's firmware is "
                f"probably too old; update it in Zwift Companion. Saw: {available}"
            )
        self._service_uuid = service.uuid
        log.info("using service %s", service.uuid)

        chars = {c.uuid.lower(): c for c in service.characteristics}
        for uuid in (P.ASYNC_CHAR_UUID, P.SYNC_RX_CHAR_UUID, P.SYNC_TX_CHAR_UUID):
            if uuid not in chars:
                raise RuntimeError(f"characteristic {uuid} missing from service")

        await client.start_notify(P.ASYNC_CHAR_UUID, self._on_notify)
        await client.start_notify(P.SYNC_TX_CHAR_UUID, self._on_notify)
        await self.handshake()

    async def handshake(self) -> None:
        """The entire handshake: six ASCII bytes. No crypto, no pairing dance."""
        assert self._client is not None
        await self._client.write_gatt_char(
            P.SYNC_RX_CHAR_UUID, P.RIDE_ON, response=False
        )
        log.info("sent RideOn handshake")

    async def vibrate(self, duration: int = P.VIBRATE_DEFAULT_DURATION) -> None:
        """Buzz this half's haptic motor once.

        Fire-and-forget: written without response, and a failure here must
        never take down a ride, so it is logged at debug and swallowed.
        """
        client = self._client
        if client is None or not client.is_connected:
            return
        try:
            await client.write_gatt_char(
                P.SYNC_RX_CHAR_UUID, P.vibrate_command(duration), response=False
            )
        except Exception:  # noqa: BLE001 -- haptics are cosmetic
            log.debug("vibrate failed", exc_info=True)

    def _on_notify(self, _sender, data: bytearray) -> None:
        if not data:
            return
        # The handshake reply is "RideOn" + 2 bytes, not an opcode frame.
        if bytes(data).startswith(P.RIDE_ON):
            log.info("handshake acknowledged")
            return
        result = self.on_frame(data[0], bytes(data[1:]))
        if asyncio.iscoroutine(result):
            asyncio.get_running_loop().create_task(result)

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001 -- teardown must not raise
                pass
            self._client = None
