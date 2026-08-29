"""Local control panel: http://127.0.0.1:8770

A tiny asyncio HTTP server with server-sent events -- no extra dependencies,
no build step. Start/stop the bridge, watch the connection state, and see
buttons light up as you press them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from . import config as config_module
from . import labels
from . import paths
from . import protocol as P
from . import ride
from . import settings as settings_module
from .actions import parse_action
from .bridge import Bridge

log = logging.getLogger("zwiftbridge")

UI_HTML = paths.UI_HTML
# Served rather than inlined as a data: URI -- Safari ignores those for
# favicons and shows a blank square, which is exactly what happened.
ICONS = {
    "/icon.svg": (paths.ICONS / "icon.svg", b"image/svg+xml"),
    "/icon.png": (paths.ICONS / "icon.png", b"image/png"),
    "/favicon.ico": (paths.ICONS / "icon.png", b"image/png"),
}
LOG_HISTORY = 300


class SSELogHandler(logging.Handler):
    """Pipes the bridge's own log lines into the browser."""

    def __init__(self, server: "ControlServer") -> None:
        super().__init__()
        self.server = server

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.server.push_log(record.levelname.lower(), self.format(record))
        except Exception:  # noqa: BLE001 -- logging must never raise
            pass


class ControlServer:
    def __init__(self, config_path: str | None = None,
                 host: str = "127.0.0.1", port: int = 8770) -> None:
        self.config_path = config_path
        self.host = host
        self.port = port
        self.bridge: Bridge | None = None
        self.task: asyncio.Task | None = None
        self.clients: set[asyncio.Queue] = set()
        self.history: list[dict] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        # Everything the panel changes goes straight to settings.json, so it
        # survives a relaunch and not just a start/stop. No mirror kept here.

    # --- event fan-out -----------------------------------------------------

    def broadcast(self, event: dict) -> None:
        if event["kind"] == "log":
            self.history.append(event)
            del self.history[:-LOG_HISTORY]
        for queue in list(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def push_log(self, level: str, message: str) -> None:
        event = {"kind": "log", "level": level, "message": message}
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.broadcast, event)
        else:
            self.broadcast(event)

    # --- bridge control ----------------------------------------------------

    def state(self) -> dict:
        if self.bridge:
            snapshot = self.bridge.snapshot()
        else:
            cfg = config_module.load(self.config_path)
            snapshot = {
                "state": "idle", "detail": "", "sides": [],
                "battery": None, "held": [], "outputs": [],
                "bindings": {b: str(a) for b, a in cfg.bindings.items()},
            }
        cfg = self.bridge.config if self.bridge else config_module.load(self.config_path)
        snapshot["running"] = bool(self.task and not self.task.done())
        snapshot["selected_outputs"] = list(cfg.outputs)
        snapshot["vibrate"] = cfg.vibrate
        snapshot["vibrate_ms"] = cfg.vibrate_ms
        # Every button, always: a missing key in the panel would render as
        # "off" when it actually means "the default for this button".
        snapshot["buzz"] = {b: cfg.buzzes(b) for b in P.ALL_BUTTONS}
        snapshot["bindings"] = {b: str(a) for b, a in cfg.bindings.items()}
        snapshot["binding_names"] = {
            b["id"]: labels.action_name(snapshot["bindings"].get(b["id"]))
            for b in labels.BUTTONS
        }
        return snapshot

    async def save_settings(self, payload: dict) -> dict:
        """Apply a change from the panel and remember it.

        Bindings and buzz flags take effect on the running bridge immediately.
        Outputs cannot: they own sockets and an mDNS advertisement, so
        changing those restarts the bridge, which the panel warns about.
        """
        saved = settings_module.load()

        if "bindings" in payload:
            incoming = payload["bindings"] or {}
            for button, spec in incoming.items():
                if button not in P.ALL_BUTTONS:
                    return {"ok": False, "error": f"unknown button {button}"}
                try:
                    parse_action(str(spec))
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
            merged = {**saved.get("bindings", {}), **incoming}
            # "none" is a real choice, not an absence, so it is stored as such
            # rather than dropped -- otherwise config.toml's binding returns.
            saved["bindings"] = merged

        if "vibrate_buttons" in payload:
            incoming = payload["vibrate_buttons"] or {}
            for button in incoming:
                if button not in P.ALL_BUTTONS:
                    return {"ok": False, "error": f"unknown button {button}"}
            saved["vibrate_buttons"] = {
                **saved.get("vibrate_buttons", {}),
                **{k: bool(v) for k, v in incoming.items()},
            }

        if "vibrate" in payload:
            saved["vibrate"] = bool(payload["vibrate"])
        if "vibrate_ms" in payload:
            saved["vibrate_ms"] = max(1, min(int(payload["vibrate_ms"]), 127))

        outputs_changed = False
        if "outputs" in payload:
            chosen = [o for o in (payload["outputs"] or []) if isinstance(o, str)]
            if not chosen:
                return {"ok": False, "error": "pick at least one destination"}
            outputs_changed = chosen != list(saved.get("outputs", []) or
                                             config_module.load(self.config_path).outputs)
            saved["outputs"] = chosen

        try:
            settings_module.save(saved)
        except OSError as exc:
            return {"ok": False, "error": f"could not save: {exc}"}

        # Re-read through the same path the bridge uses, so a bad combination
        # fails here rather than at the next button press.
        try:
            fresh = config_module.load(self.config_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if self.bridge and outputs_changed and self.task and not self.task.done():
            await self.stop_bridge()
            result = await self.start_bridge()
            return {**result, **self.state()}
        if self.bridge:
            self.bridge.config.bindings = fresh.bindings
            self.bridge.config.vibrate = fresh.vibrate
            self.bridge.config.vibrate_ms = fresh.vibrate_ms
            self.bridge.config.vibrate_buttons = fresh.vibrate_buttons
            self.broadcast({"kind": "state", **self.state()})
        return {"ok": True, **self.state()}

    async def reset_settings(self) -> dict:
        """Throw away every panel change, back to config.toml as written."""
        try:
            settings_module.save({})
        except OSError as exc:
            return {"ok": False, "error": f"could not save: {exc}"}
        if self.task and not self.task.done():
            await self.stop_bridge()
            result = await self.start_bridge()
            return {**result, **self.state()}
        return {"ok": True, **self.state()}

    async def start_bridge(self, outputs: list[str] | None = None) -> dict:
        if self.task and not self.task.done():
            return {"ok": False, "error": "already running"}
        cfg = config_module.load(self.config_path)
        if outputs:
            cfg.outputs = outputs
        bridge = Bridge(cfg)
        bridge.listeners.append(self.broadcast)
        self.bridge = bridge
        # Started here rather than inside the task so a failure -- a busy 21587,
        # most likely another zwiftbridge -- reaches the browser as an error
        # instead of disappearing into a background task.
        try:
            await bridge.start_outputs()
        except OSError as exc:
            self.bridge = None
            log.error("could not start outputs: %s", exc)
            detail = (f"port {cfg.output_settings.get('whoosh_link', {}).get('port', 21587)}"
                      " is already in use -- is zwiftbridge already running?")
            return {"ok": False, "error": detail}
        self.task = asyncio.create_task(self._supervise(bridge))
        return {"ok": True}

    async def _supervise(self, bridge: Bridge) -> None:
        try:
            await bridge.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- surface it, don't die silently
            log.error("bridge stopped: %s", exc)
        finally:
            self.broadcast({"kind": "state", **self.state()})

    async def stop_bridge(self) -> dict:
        if not self.task or self.task.done():
            # Still tear down outputs a failed/finished run may have left up,
            # so no mDNS advertisement outlives us.
            if self.bridge:
                await self.bridge.stop_outputs()
                self.bridge = None
            return {"ok": False, "error": "not running"}
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):  # noqa: B014
            pass
        self.task = None
        self.bridge = None
        self.broadcast({"kind": "state", **self.state()})
        return {"ok": True}

    async def shutdown(self) -> None:
        """Stop the bridge and release the mDNS advertisement."""
        if self.task and not self.task.done():
            await self.stop_bridge()
        elif self.bridge:
            await self.bridge.stop_outputs()
            self.bridge = None

    async def scan(self) -> dict:
        if self.task and not self.task.done():
            return {"ok": False, "error": "stop the bridge before scanning"}
        log.info("scanning...")
        found = await ride.scan(8.0)
        for hit in found:
            log.info("found %s [%s] rssi %d", hit.type_name, hit.address, hit.rssi)
        rides = [h for h in found if h.is_ride]
        if not found:
            log.warning("nothing found -- press a button on the Ride to wake it")
        elif len(rides) < 2:
            log.warning("only %d Ride half seen -- it is two peripherals; wake "
                        "the other side or half the bike will be dead", len(rides))
        return {
            "ok": True,
            "devices": [
                {"name": hit.type_name, "address": hit.address, "rssi": hit.rssi,
                 "is_ride": hit.is_ride}
                for hit in found
            ],
        }

    # --- HTTP --------------------------------------------------------------

    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        handler = SSELogHandler(self)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)

        server = await asyncio.start_server(self._handle, self.host, self.port)
        async with server:
            await server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, path, *_ = request_line.decode("latin-1").split()

            length = 0
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            body = await reader.readexactly(length) if length else b""

            if path == "/events":
                await self._stream_events(writer)
            elif path == "/":
                await self._send_file(writer)
            elif path in ICONS:
                await self._send_icon(writer, path)
            else:
                await self._send_json(writer, await self._api(method, path, body))
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _api(self, method: str, path: str, body: bytes) -> dict:
        payload = json.loads(body) if body else {}
        if path == "/api/state":
            return self.state()
        if path == "/api/start" and method == "POST":
            result = await self.start_bridge(payload.get("outputs"))
            return {**result, **self.state()}
        if path == "/api/stop" and method == "POST":
            result = await self.stop_bridge()
            return {**result, **self.state()}
        if path == "/api/vocab":
            return labels.vocabulary()
        if path == "/api/settings" and method == "POST":
            return await self.save_settings(payload)
        if path == "/api/reset" and method == "POST":
            return await self.reset_settings()
        if path == "/api/scan" and method == "POST":
            return await self.scan()
        return {"ok": False, "error": "not found"}

    async def _send_file(self, writer: asyncio.StreamWriter) -> None:
        data = UI_HTML.read_bytes()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"Cache-Control: no-store\r\n"
            + f"Content-Length: {len(data)}\r\n\r\n".encode() + data
        )
        await writer.drain()

    async def _send_icon(self, writer: asyncio.StreamWriter, path: str) -> None:
        source, content_type = ICONS[path]
        try:
            data = source.read_bytes()
        except OSError:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type + b"\r\n"
            b"Cache-Control: max-age=86400\r\n"
            + f"Content-Length: {len(data)}\r\n\r\n".encode() + data
        )
        await writer.drain()

    async def _send_json(self, writer: asyncio.StreamWriter, payload: dict) -> None:
        data = json.dumps(payload).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(data)}\r\n\r\n".encode() + data
        )
        await writer.drain()

    async def _stream_events(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-store\r\n"
            b"Connection: keep-alive\r\n\r\n"
        )
        await writer.drain()

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.clients.add(queue)
        try:
            # Replay so a refreshed page is not blank.
            for event in self.history[-60:]:
                queue.put_nowait(event)
            queue.put_nowait({"kind": "state", **self.state()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 20.0)
                    chunk = f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    chunk = ": keepalive\n\n"  # keeps proxies and Safari happy
                writer.write(chunk.encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.clients.discard(queue)
