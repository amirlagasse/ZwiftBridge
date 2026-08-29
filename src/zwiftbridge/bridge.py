"""The bridge: BLE frames in, actions out, reconnecting forever.

The Zwift Ride is TWO BLE peripherals -- left and right -- each reporting only
its own buttons. Each side gets its own connection, its own edge detector and
its own reconnect loop; the held-button sets are merged before dispatch so the
rest of the pipeline never has to care how many radios are involved.

Emits structured events so a UI can watch without scraping logs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from . import protocol as P
from . import ride
from .config import Config
from .outputs import Output, build_outputs
from .outputs.obp_dircon import ObpDirconOutput
from .outputs.zwift_dircon import ZwiftDirconOutput
from .outputs.obp_mdns import ObpMdnsOutput
from .outputs.whoosh_link import WhooshLinkOutput

log = logging.getLogger("zwiftbridge")

# Coarse lifecycle states, for the UI's status pill.
IDLE = "idle"
SCANNING = "scanning"
CONNECTING = "connecting"
CONNECTED = "connected"
RETRYING = "retrying"

# How often to re-scan while a half is still missing.
RESCAN_SECONDS = 20.0


class Side:
    """One half of the Ride."""

    def __init__(self, address: str, name: str) -> None:
        self.address = address
        self.name = name
        self.state = CONNECTING
        self.battery: int | None = None
        self.tracker = P.ButtonTracker()


class Bridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.outputs: list[Output] = []
        self.state = IDLE
        self.detail = ""
        self.sides: dict[str, Side] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._links: dict[str, ride.RideLink] = {}
        self._held: set[str] = set()  # merged across both sides

    # --- events ------------------------------------------------------------

    def emit(self, kind: str, **data) -> None:
        event = {"kind": kind, **data}
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 -- a bad listener must not kill the ride
                log.debug("listener raised", exc_info=True)

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.detail = detail
        self.emit("state", **self.snapshot())

    def _refresh_state(self) -> None:
        """Overall state is the best state any side is in."""
        if not self.sides:
            return
        states = [side.state for side in self.sides.values()]
        if CONNECTED in states:
            connected = states.count(CONNECTED)
            detail = "both halves" if connected >= 2 else "1 of 2 halves"
            self._set_state(CONNECTED, detail)
        else:
            self._set_state(states[0], self.detail)

    @property
    def battery(self) -> int | None:
        levels = [s.battery for s in self.sides.values() if s.battery is not None]
        return min(levels) if levels else None

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "detail": self.detail,
            "battery": self.battery,
            "held": sorted(self._held),
            "sides": [
                {"address": s.address, "name": s.name, "state": s.state,
                 "battery": s.battery}
                for s in sorted(self.sides.values(), key=lambda s: s.name)
            ],
            "outputs": [
                {"name": o.name, "status": o.status(), "ready": o.ready}
                for o in self.outputs
            ],
            "bindings": {b: str(a) for b, a in self.config.bindings.items()},
        }

    # --- outputs -----------------------------------------------------------

    async def start_outputs(self) -> None:
        if self.outputs:  # idempotent: the UI starts these before run() does
            return
        self.outputs = build_outputs(self.config.outputs, self.config.output_settings)
        for output in self.outputs:
            if isinstance(output, (WhooshLinkOutput, ObpMdnsOutput, ObpDirconOutput,
                                    ZwiftDirconOutput)):
                output.on_client_change = self._on_link_change
            await output.start()
            log.info("output %-13s %s", output.name, output.status())
        self.emit("state", **self.snapshot())

    def _on_link_change(self, message: str) -> None:
        log.info("%s", message)
        self.emit("state", **self.snapshot())

    async def stop_outputs(self) -> None:
        for output in self.outputs:
            await output.stop()
        self.outputs = []

    # --- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        await self.start_outputs()
        try:
            await self._discover_forever()
        finally:
            for task in self._tasks.values():
                task.cancel()
            for task in list(self._tasks.values()):
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: B014
                    pass
            self._tasks.clear()
            for link in self._links.values():
                await link.disconnect()
            self._links.clear()
            await self._release_all()
            await self.stop_outputs()
            self.sides.clear()
            self._set_state(IDLE)

    async def _discover_forever(self) -> None:
        """Find both halves, then keep looking for whichever is still missing.

        A half that is merely asleep can join later; its own supervisor takes
        over reconnection from then on, so scanning stops once both are up.
        """
        pinned = [a for a in (self.config.addresses or []) if a]
        if pinned:
            for address in pinned:
                self._ensure_side(address, address)

        while True:
            if len(self._tasks) < 2:
                self._set_state(
                    SCANNING,
                    "scanning for the other half" if self._tasks
                    else "scanning for the Zwift Ride",
                )
                log.info("scanning for Zwift Ride halves...")
                found = await ride.find_rides(self.config.scan_timeout, want=2)
                for hit in found:
                    if hit.address not in self._tasks:
                        log.info("found %s [%s] rssi %d", hit.type_name,
                                 hit.address, hit.rssi)
                        self._ensure_side(hit.address, hit.type_name)
                if not found and not self._tasks:
                    log.warning("no Zwift Ride found -- press a button to wake it")
                elif len(self._tasks) < 2:
                    log.info("only %d of 2 halves up; still looking",
                             len(self._tasks))
            await asyncio.sleep(RESCAN_SECONDS)

    def _ensure_side(self, address: str, name: str) -> None:
        if address in self._tasks:
            return
        self.sides[address] = Side(address, name)
        self._tasks[address] = asyncio.create_task(self._supervise(address))

    async def _supervise(self, address: str) -> None:
        """Keep one half connected, forever, with backoff."""
        side = self.sides[address]
        backoff = 1.0
        while True:
            dropped = asyncio.Event()
            link = ride.RideLink(
                address, lambda op, payload, a=address: self._on_frame(a, op, payload)
            )
            side.state = CONNECTING
            self._refresh_state()
            try:
                await link.connect(disconnect_cb=lambda _c: dropped.set())
            except Exception as exc:  # noqa: BLE001 -- any BLE failure means retry
                log.warning("%s: connect failed: %s", side.name, exc)
                side.state = RETRYING
                self._refresh_state()
                if not self.config.reconnect:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            self._links[address] = link
            side.state = CONNECTED
            backoff = 1.0
            self._refresh_state()
            log.info("%s connected [%s]", side.name, address)

            try:
                await dropped.wait()
            finally:
                self._links.pop(address, None)
                await link.disconnect()
                # Drop this half's buttons only -- the other half rides on.
                side.tracker.reset()
                await self._sync_held()

            log.warning("%s disconnected", side.name)
            side.state = RETRYING
            self._refresh_state()
            if not self.config.reconnect:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    # --- frame handling ----------------------------------------------------

    def _on_frame(self, address: str, opcode: int, payload: bytes):
        side = self.sides.get(address)
        if side is None:
            return None
        if opcode == P.OP_RIDE_KEYPAD:
            try:
                status = P.parse_ride_keypad(payload)
            except ValueError as exc:
                log.debug("undecodable keypad frame: %s", exc)
                return None
            side.tracker.update(status)
            return self._sync_held()
        if opcode == P.OP_BATTERY_NOTIF:
            level = P.parse_battery(payload)
            if level is not None and level != side.battery:
                side.battery = level
                log.info("%s battery %d%%", side.name, level)
                self.emit("state", **self.snapshot())
        return None

    async def _sync_held(self) -> None:
        """Merge both halves' held sets and dispatch the difference."""
        merged: set[str] = set()
        for side in self.sides.values():
            merged |= side.tracker.held
        for button in sorted(merged - self._held):
            self._held.add(button)
            await self._buzz(button)
            await self._dispatch(button, True)
        for button in sorted(self._held - merged):
            self._held.discard(button)
            await self._dispatch(button, False)

    async def _buzz(self, button: str) -> None:
        """Haptic tick on the half that owns the button just pressed.

        Buzzing only the reporting half is the point: it lands under the hand
        that actually pressed, and a half that is disconnected stays quiet.
        Errors are swallowed inside RideLink.vibrate -- haptics never block a
        press from reaching the game.

        Which buttons buzz is per button, set in the panel. The steering
        paddles are off by default because they are held rather than clicked.
        """
        if not self.config.buzzes(button):
            return
        if self.config.action_for(button) is None:
            return  # an unbound button did nothing; do not pretend it did
        for address, side in self.sides.items():
            if button in side.tracker.held:
                link = self._links.get(address)
                if link is not None:
                    await link.vibrate(self.config.vibrate_ms)

    async def _dispatch(self, button: str, pressed: bool) -> None:
        action = self.config.action_for(button)
        edge = "down" if pressed else "up"
        self.emit("button", button=button, pressed=pressed,
                  action=str(action) if action else None,
                  held=sorted(self._held))
        if action is None:
            log.info("%-11s %-4s  (unbound)", button, edge)
            return
        for output in self.outputs:
            result = await output.send(action, pressed)
            if result:
                log.info("%-11s %-4s  %s: %s", button, edge, output.name, result)

    async def _release_all(self) -> None:
        """Release everything still held, so nothing sticks down."""
        for side in self.sides.values():
            side.tracker.reset()
        await self._sync_held()
