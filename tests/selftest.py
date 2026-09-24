"""Offline self-test: everything except the BLE radio.

Feeds synthetic Zwift Ride frames through the real decoder, keymap and
outputs, with a fake MyWhoosh on the other end of the Link socket.
Run it before you touch the bike: python3 tests/selftest.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Runs straight out of a clone -- `python tests/selftest.py` -- so src goes on
# the path here rather than requiring an install first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zwiftbridge import config as config_module          # noqa: E402
from zwiftbridge import protocol as P                    # noqa: E402
from zwiftbridge.actions import parse_action             # noqa: E402
from zwiftbridge.bridge import Bridge, Side              # noqa: E402
from zwiftbridge.outputs.keystrokes import KeystrokeOutput  # noqa: E402


# The right half is mirrored in config.toml: its DOWN paddle is bound to
# shift_up. These tests exercise output plumbing, not the keymap, so they press
# whichever physical button currently means "shift up" on that side.
RIGHT_SHIFT_UP = "shift_dn_r"


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def keypad_frame(pressed: list[str], analog: dict[int, int] | None = None) -> bytes:
    """Build a realistic 0x23 payload. Remember: pressed == bit CLEAR."""
    button_map = 0xFFFF
    for name in pressed:
        button_map &= ~P.BUTTON_MASKS[name]
    payload = b"\x08" + varint(button_map)
    for location, value in (analog or {}).items():
        zigzag = (value << 1) ^ (value >> 31) if value >= 0 else ((-value) << 1) - 1
        inner = b"\x08" + varint(location) + b"\x10" + varint(zigzag)
        payload += b"\x1a" + varint(len(inner)) + inner
    return payload


async def main() -> None:
    failures = []

    def check(label: str, got, want) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        got  {got!r}\n        want {want!r}")
            failures.append(label)

    # --- decoder ----------------------------------------------------------
    print("\ndecoder")
    idle = P.parse_ride_keypad(keypad_frame([]))
    check("idle frame holds nothing", idle.button_map, 0xFFFF)

    tracker = P.ButtonTracker()
    tracker.update(idle)
    frame = P.parse_ride_keypad(keypad_frame(["shift_up_r", "a"]))
    check("two buttons decode active-low",
          sorted(n for n, m in P.BUTTON_MASKS.items()
                 if P.is_pressed(frame.button_map, m)),
          ["a", "shift_up_r"])

    # --- edge detection ---------------------------------------------------
    print("\nedge detection")
    tracker = P.ButtonTracker()
    tracker.update(P.parse_ride_keypad(keypad_frame([])))
    held = P.parse_ride_keypad(keypad_frame(["shift_up_r"]))
    check("press fires once", tracker.update(held), [("shift_up_r", True)])
    check("repeat snapshot fires nothing", tracker.update(held), [])
    check("repeat again still nothing", tracker.update(held), [])
    check("release fires once",
          tracker.update(P.parse_ride_keypad(keypad_frame([]))),
          [("shift_up_r", False)])

    # --- analog paddles ---------------------------------------------------
    print("\nanalog paddles")
    tracker = P.ButtonTracker()
    tracker.update(P.parse_ride_keypad(keypad_frame([])))
    drift = P.parse_ride_keypad(keypad_frame([], {0: 12}))
    check("drift below threshold ignored", tracker.update(drift), [])
    squeeze = P.parse_ride_keypad(keypad_frame([], {0: -80}))
    check("real squeeze registers", tracker.update(squeeze), [("paddle_l", True)])

    # --- MyWhoosh Link over a real socket ---------------------------------
    print("\nMyWhoosh Link (fake MyWhoosh client on a real socket)")
    cfg = config_module.load(overlay=False)
    cfg.outputs = ["whoosh_link"]
    cfg.output_settings = {"whoosh_link": {"host": "127.0.0.1", "port": 21587}}
    bridge = Bridge(cfg)
    # The real Ride is two peripherals; register both halves by hand.
    bridge.sides["LEFT"] = Side("LEFT", "Zwift Ride (left)")
    bridge.sides["RIGHT"] = Side("RIGHT", "Zwift Ride (right)")

    async def feed(address, pressed, analog=None):
        await bridge._on_frame(address, P.OP_RIDE_KEYPAD,
                               keypad_frame(pressed, analog))

    await bridge.start_outputs()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 21587)
        await asyncio.sleep(0.05)

        async def next_message() -> dict:
            line = await asyncio.wait_for(reader.readline(), 2.0)
            return json.loads(line)

        await feed("RIGHT", [])
        await feed("RIGHT", [RIGHT_SHIFT_UP])
        check("shift up (right half)", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"GearShifting": "1"}})

        await feed("RIGHT", [])
        await feed("LEFT", ["shift_dn_l"])
        check("shift down (left half)", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"GearShifting": "-1"}})

        # Steering must send on both edges or you steer into a wall forever.
        await feed("LEFT", [])
        await feed("LEFT", ["left"])
        check("steer left held", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"Steering": "-1"}})
        await feed("LEFT", [])
        check("steer recentres on release", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"Steering": "0"}})

        await feed("RIGHT", ["z"])
        check("emote", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"Emote": "1"}})

        # Releasing a one-shot action must stay silent -- only steering
        # has a release edge worth sending.
        await feed("RIGHT", [])
        quiet = False
        try:
            await asyncio.wait_for(reader.readline(), 0.3)
        except asyncio.TimeoutError:
            quiet = True
        check("emote release sends nothing", quiet, True)

        # A drop mid-steer must not leave MyWhoosh turning.
        await feed("RIGHT", ["right"])
        check("steer right held", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"Steering": "1"}})
        await bridge._release_all()
        check("disconnect releases steering", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"Steering": "0"}})

        # The bug that started this: each half reports ONLY its own buttons,
        # so a left press must survive the right half's idle snapshots.
        await bridge._release_all()
        while True:
            try:
                await asyncio.wait_for(reader.readline(), 0.3)
            except asyncio.TimeoutError:
                break
        await feed("LEFT", ["shift_up_l"])
        check("left half shifts while right idles", await next_message(),
              {"MessageType": "Controls", "InGameControls": {"GearShifting": "1"}})
        await feed("RIGHT", [])
        quiet2 = False
        try:
            await asyncio.wait_for(reader.readline(), 0.3)
        except asyncio.TimeoutError:
            quiet2 = True
        check("right idle frame does not clear a held left button", quiet2, True)
        check("left button still held", sorted(bridge._held), ["shift_up_l"])

        # One half dropping must not release the other half's buttons.
        await feed("LEFT", [])
        await feed("LEFT", [], {0: -80})
        await next_message()
        bridge.sides["RIGHT"].tracker.reset()
        await bridge._sync_held()
        check("right half dropping leaves left steering intact",
              sorted(bridge._held), ["paddle_l"])

        writer.close()
    finally:
        await bridge.stop_outputs()

    # --- OpenBikeProtocol over mDNS ---------------------------------------
    print("\nOpenBikeProtocol (fake MyWhoosh: discovers by mDNS, then pairs)")
    from zwiftbridge import obp
    from zwiftbridge.outputs.obp_mdns import ObpMdnsOutput

    cfg2 = config_module.load(overlay=False)
    cfg2.outputs = ["obp_mdns"]
    cfg2.output_settings = {"obp_mdns": {"port": 36899,
                                         "device_name": "zwiftbridge-test"}}
    bridge2 = Bridge(cfg2)
    bridge2.sides["LEFT"] = Side("LEFT", "Zwift Ride (left)")
    bridge2.sides["RIGHT"] = Side("RIGHT", "Zwift Ride (right)")

    async def feed2(address, pressed, analog=None):
        await bridge2._on_frame(address, P.OP_RIDE_KEYPAD,
                                keypad_frame(pressed, analog))

    await bridge2.start_outputs()
    out = bridge2.outputs[0]
    try:
        # Discovery is the entire point of this output: MyWhoosh must be able
        # to FIND us. Browse for the service the way MyWhoosh does.
        from zeroconf import ServiceBrowser, Zeroconf
        discovered = {}

        class Listener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name, timeout=2000)
                if info:
                    discovered[name] = info

            update_service = add_service

            def remove_service(self, zc, type_, name):
                pass

        zc = Zeroconf()
        ServiceBrowser(zc, obp.MDNS_SERVICE_TYPE, Listener())
        for _ in range(30):
            await asyncio.sleep(0.1)
            if any("zwiftbridge-test" in n for n in discovered):
                break
        ours = [i for n, i in discovered.items() if "zwiftbridge-test" in n]
        check("advertisement is discoverable over mDNS", bool(ours), True)
        if ours:
            info = ours[0]
            check("advertises the port it actually bound", info.port, 36899)
            check("TXT names the OBP service uuid",
                  info.properties.get(b"service-uuids", b"").decode(),
                  obp.SERVICE_UUID)
        zc.close()

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", 36899)
        # We greet a new client with our status.
        greeting = await asyncio.wait_for(reader2.read(3), 2.0)
        check("greets client with device status", greeting,
              obp.encode_device_status(connected=True))

        # Nothing is sent until the app says what it supports.
        await feed2("RIGHT", [])
        await feed2("RIGHT", [RIGHT_SHIFT_UP])
        check("holds off until app info arrives", out.app, None)
        await feed2("RIGHT", [])  # release it, so pairing starts from clean state

        # MyWhoosh's real supported-button set.
        writer2.write(obp.encode_app_info("MyWhoosh", "3.1", [
            0x01, 0x02, 0x03, 0x10, 0x11, 0x12, 0x13, 0x14,
            0x15, 0x16, 0x17, 0x18, 0x19, 0x40,
        ]))
        await writer2.drain()
        await asyncio.sleep(0.3)
        check("parses app info", out.app.app_id if out.app else None, "MyWhoosh")
        check("output reports ready", out.ready, True)

        async def next_obp():
            return await asyncio.wait_for(reader2.read(64), 2.0)

        await feed2("RIGHT", [])
        await feed2("RIGHT", [RIGHT_SHIFT_UP])
        check("shift up", await next_obp(), obp.encode_button_state([(0x01, 1)]))
        await feed2("RIGHT", [])
        check("shift up releases", await next_obp(),
              obp.encode_button_state([(0x01, 0)]))

        await feed2("LEFT", ["shift_dn_l"])
        check("shift down from the left half", await next_obp(),
              obp.encode_button_state([(0x02, 1)]))
        await feed2("LEFT", [])
        await next_obp()

        await feed2("LEFT", ["left"])
        check("steer left", await next_obp(),
              obp.encode_button_state([(0x18, 1)]))
        await feed2("LEFT", [])
        check("steer left releases", await next_obp(),
              obp.encode_button_state([(0x18, 0)]))

        # MyWhoosh does not list HUD toggle (0x44); say so, do not fake it.
        result = await out.send(parse_action("toggle_ui"), True)
        check("unsupported button is reported, not sent",
              "does not support" in (result or ""), True)

        writer2.close()
        await asyncio.sleep(0.2)
        check("app info cleared on disconnect", out.app, None)
    finally:
        await bridge2.stop_outputs()

    # --- OpenBikeProtocol over DirCon -------------------------------------
    print("\nDirCon (fake MyWhoosh: discovers, walks GATT, subscribes)")
    from zwiftbridge import dircon
    from zwiftbridge.outputs.obp_dircon import ObpDirconOutput

    cfg3 = config_module.load(overlay=False)
    cfg3.outputs = ["obp_dircon"]
    cfg3.output_settings = {"obp_dircon": {"port": 36896,
                                           "device_name": "zwiftbridge-dc-test"}}
    bridge3 = Bridge(cfg3)
    bridge3.sides["LEFT"] = Side("LEFT", "Zwift Ride (left)")
    bridge3.sides["RIGHT"] = Side("RIGHT", "Zwift Ride (right)")

    async def feed3(address, pressed, analog=None):
        await bridge3._on_frame(address, P.OP_RIDE_KEYPAD,
                                keypad_frame(pressed, analog))

    await bridge3.start_outputs()
    out3 = bridge3.outputs[0]
    try:
        from zeroconf import ServiceBrowser, Zeroconf
        found3 = {}

        class L3:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name, timeout=2000)
                if info:
                    found3[name] = info
            update_service = add_service
            def remove_service(self, zc, type_, name): pass

        zc3 = Zeroconf()
        ServiceBrowser(zc3, dircon.MDNS_SERVICE_TYPE, L3())
        for _ in range(30):
            await asyncio.sleep(0.1)
            if any("zwiftbridge-dc-test" in n for n in found3):
                break
        mine = [i for n, i in found3.items() if "zwiftbridge-dc-test" in n]
        check("advertises on the DirCon service type MyWhoosh browses",
              bool(mine), True)
        if mine:
            check("TXT carries the OBP service uuid",
                  mine[0].properties.get(b"ble-service-uuids", b"").decode(),
                  obp.SERVICE_UUID)
            check("serial-number is all digits (DirCon parses it as an int)",
                  mine[0].properties.get(b"serial-number", b"").decode().isdigit(),
                  True)
        zc3.close()

        r3, w3 = await asyncio.open_connection("127.0.0.1", 36896)

        async def rpc(identifier, payload=b"", seq=1):
            w3.write(dircon.Packet(identifier, seq, 0, payload).encode())
            await w3.drain()
            raw = await asyncio.wait_for(r3.read(2048), 2.0)
            packet, _ = dircon.decode(raw)
            return packet

        reply = await rpc(dircon.MSG_DISCOVER_SERVICES)
        check("discover services returns the OBP service",
              dircon.bytes_uuid(reply.payload[:16]).lower(),
              obp.SERVICE_UUID.lower())

        reply = await rpc(dircon.MSG_DISCOVER_CHARACTERISTICS,
                          dircon.uuid_bytes(obp.SERVICE_UUID), seq=2)
        chars = {}
        body = reply.payload[16:]
        for i in range(0, len(body), 17):
            chars[dircon.bytes_uuid(body[i:i + 16]).lower()] = body[i + 16]
        check("exposes all three OBP characteristics", len(chars), 3)
        check("button state is notifiable",
              bool(chars.get(obp.BUTTON_STATE_CHAR_UUID.lower(), 0)
                   & dircon.PROP_NOTIFY), True)

        reply = await rpc(dircon.MSG_DISCOVER_CHARACTERISTICS,
                          dircon.uuid_bytes("00000000-0000-0000-0000-00000000ffff"),
                          seq=3)
        check("unknown service is rejected", reply.response_code,
              dircon.RC_SERVICE_NOT_FOUND)

        # Nothing may be sent before the client subscribes.
        await feed3("RIGHT", [])
        await feed3("RIGHT", [RIGHT_SHIFT_UP])
        quiet3 = False
        try:
            await asyncio.wait_for(r3.read(64), 0.3)
        except asyncio.TimeoutError:
            quiet3 = True
        check("silent until subscribed", quiet3, True)
        await feed3("RIGHT", [])

        reply = await rpc(dircon.MSG_ENABLE_NOTIFICATIONS,
                          dircon.uuid_bytes(obp.BUTTON_STATE_CHAR_UUID) + b"\x01",
                          seq=4)
        check("subscribe succeeds", reply.response_code, dircon.RC_SUCCESS)
        check("output reports ready once subscribed", out3.ready, True)

        w3.write(dircon.Packet(
            dircon.MSG_WRITE_CHARACTERISTIC, 5, 0,
            dircon.uuid_bytes(obp.APPINFO_CHAR_UUID)
            + obp.encode_app_info("MyWhoosh", "3.1",
                                  [0x01, 0x02, 0x18, 0x19, 0x40]),
        ).encode())
        await w3.drain()
        await asyncio.sleep(0.3)
        check("app info arrives over DirCon write",
              out3.app.app_id if out3.app else None, "MyWhoosh")

        async def next_notify():
            raw = await asyncio.wait_for(r3.read(2048), 2.0)
            packet, _ = dircon.decode(raw)
            while packet and packet.identifier != dircon.MSG_CHARACTERISTIC_NOTIFICATION:
                raw = await asyncio.wait_for(r3.read(2048), 2.0)
                packet, _ = dircon.decode(raw)
            return packet

        await feed3("RIGHT", [RIGHT_SHIFT_UP])
        note = await next_notify()
        check("shift up notifies on the button-state characteristic",
              dircon.bytes_uuid(note.payload[:16]).lower(),
              obp.BUTTON_STATE_CHAR_UUID.lower())
        check("shift up payload", note.payload[16:],
              obp.encode_button_state([(0x01, 1)]))
        await feed3("RIGHT", [])
        note = await next_notify()
        check("shift up release", note.payload[16:],
              obp.encode_button_state([(0x01, 0)]))

        await feed3("LEFT", ["left"])
        note = await next_notify()
        check("steer left over DirCon", note.payload[16:],
              obp.encode_button_state([(0x18, 1)]))
        await feed3("LEFT", [])
        await next_notify()

        # A packet split across TCP reads must still parse.
        raw = dircon.Packet(dircon.MSG_DISCOVER_SERVICES, 9).encode()
        w3.write(raw[:3]); await w3.drain()
        await asyncio.sleep(0.1)
        w3.write(raw[3:]); await w3.drain()
        reply, _ = dircon.decode(await asyncio.wait_for(r3.read(2048), 2.0))
        check("a split packet still parses", reply.sequence, 9)

        w3.close()
        await asyncio.sleep(0.2)
        check("subscription cleared on disconnect", out3.ready, False)
    finally:
        await bridge3.stop_outputs()

    # --- Zwift Ride emulated over DirCon ----------------------------------
    print("\nZwift Ride over DirCon (what MyWhoosh already knows how to talk to)")
    from zwiftbridge.outputs.zwift_dircon import ZwiftDirconOutput

    cfg4 = config_module.load(overlay=False)
    cfg4.outputs = ["zwift_dircon"]
    cfg4.output_settings = {"zwift_dircon": {"port": 36886,
                                             "device_name": "zwiftbridge-zr-test"}}
    bridge4 = Bridge(cfg4)
    bridge4.sides["LEFT"] = Side("LEFT", "Zwift Ride (left)")
    bridge4.sides["RIGHT"] = Side("RIGHT", "Zwift Ride (right)")

    async def feed4(address, pressed, analog=None):
        await bridge4._on_frame(address, P.OP_RIDE_KEYPAD,
                                keypad_frame(pressed, analog))

    await bridge4.start_outputs()
    out4 = bridge4.outputs[0]
    try:
        from zeroconf import ServiceBrowser, Zeroconf
        found4 = {}

        class L4:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name, timeout=2000)
                if info:
                    found4[name] = info
            update_service = add_service
            def remove_service(self, zc, type_, name): pass

        zc4 = Zeroconf()
        ServiceBrowser(zc4, dircon.MDNS_SERVICE_TYPE, L4())
        for _ in range(30):
            await asyncio.sleep(0.1)
            if any("zwiftbridge-zr-test" in n for n in found4):
                break
        mine4 = [i for n, i in found4.items() if "zwiftbridge-zr-test" in n]
        check("advertises on the DirCon service type", bool(mine4), True)
        if mine4:
            props = mine4[0].properties
            # These three formats are copied from the real KICKR; getting them
            # wrong is what made MyWhoosh list us but never dial.
            check("service uuid in short 0x form like the KICKR",
                  props.get(b"ble-service-uuids", b"").decode(), "0xFC82")
            check("MAC is hyphenated like the KICKR",
                  "-" in props.get(b"mac-address", b"").decode()
                  and ":" not in props.get(b"mac-address", b"").decode(), True)
            check("serial is all digits",
                  props.get(b"serial-number", b"").decode().isdigit(), True)
        zc4.close()

        r4, w4 = await asyncio.open_connection("127.0.0.1", 36886)

        async def rpc4(identifier, payload=b"", seq=1):
            w4.write(dircon.Packet(identifier, seq, 0, payload).encode())
            await w4.drain()
            packet, _ = dircon.decode(await asyncio.wait_for(r4.read(2048), 2.0))
            return packet

        reply = await rpc4(dircon.MSG_DISCOVER_SERVICES)
        check("discover services returns the Zwift Ride service",
              dircon.bytes_uuid(reply.payload[:16]).lower(),
              P.RIDE_SERVICE_UUID.lower())

        reply = await rpc4(dircon.MSG_DISCOVER_CHARACTERISTICS,
                           dircon.uuid_bytes(P.RIDE_SERVICE_UUID), seq=2)
        chars4 = {}
        body = reply.payload[16:]
        for i in range(0, len(body), 17):
            chars4[dircon.bytes_uuid(body[i:i + 16]).lower()] = body[i + 16]
        check("exposes the Ride's three characteristics", len(chars4), 3)
        check("async characteristic is notifiable",
              bool(chars4.get(P.ASYNC_CHAR_UUID.lower(), 0) & dircon.PROP_NOTIFY),
              True)

        reply = await rpc4(dircon.MSG_ENABLE_NOTIFICATIONS,
                           dircon.uuid_bytes(P.ASYNC_CHAR_UUID) + b"\x01", seq=3)
        check("subscribe to button data succeeds", reply.response_code,
              dircon.RC_SUCCESS)
        check("output ready once subscribed", out4.ready, True)

        # The RideOn handshake, from the app's side this time.
        await rpc4(dircon.MSG_ENABLE_NOTIFICATIONS,
                   dircon.uuid_bytes(P.SYNC_TX_CHAR_UUID) + b"\x01", seq=4)
        w4.write(dircon.Packet(
            dircon.MSG_WRITE_CHARACTERISTIC, 5, 0,
            dircon.uuid_bytes(P.SYNC_RX_CHAR_UUID) + P.RIDE_ON,
        ).encode())
        await w4.drain()

        async def next_notify4(want_uuid):
            deadline = asyncio.get_running_loop().time() + 2.0
            buf = b""
            while asyncio.get_running_loop().time() < deadline:
                buf += await asyncio.wait_for(r4.read(2048), 2.0)
                while True:
                    packet, buf = dircon.decode(buf)
                    if packet is None:
                        break
                    if (packet.identifier == dircon.MSG_CHARACTERISTIC_NOTIFICATION
                            and dircon.bytes_uuid(packet.payload[:16]).lower()
                            == want_uuid.lower()):
                        return packet
            raise AssertionError("no notification arrived")

        note = await next_notify4(P.SYNC_TX_CHAR_UUID)
        check("answers the RideOn handshake",
              note.payload[16:].startswith(P.RIDE_ON), True)

        # A press must produce a real Ride frame the app can decode.
        await feed4("RIGHT", [])
        await feed4("RIGHT", [RIGHT_SHIFT_UP])
        note = await next_notify4(P.ASYNC_CHAR_UUID)
        frame = note.payload[16:]
        check("emits a Ride keypad frame", frame[0], P.OP_RIDE_KEYPAD)
        decoded = P.parse_ride_keypad(frame[1:])
        held = sorted(n for n, m in P.BUTTON_MASKS.items()
                      if P.is_pressed(decoded.button_map, m))
        check("shift up presents as the right up-paddle", held, ["shift_up_r"])

        await feed4("RIGHT", [])
        note = await next_notify4(P.ASYNC_CHAR_UUID)
        released = P.parse_ride_keypad(note.payload[16:][1:])
        check("release returns the neutral frame", released.button_map,
              0xFFFFFFFF)

        # A left-half shift must present as the LEFT up-paddle, mirroring a
        # real Ride, or the app shifts the wrong way.
        await feed4("LEFT", ["shift_dn_l"])
        note = await next_notify4(P.ASYNC_CHAR_UUID)
        decoded = P.parse_ride_keypad(note.payload[16:][1:])
        held = sorted(n for n, m in P.BUTTON_MASKS.items()
                      if P.is_pressed(decoded.button_map, m))
        check("shift down presents as the left up-paddle", held, ["shift_up_l"])
        await feed4("LEFT", [])
        await next_notify4(P.ASYNC_CHAR_UUID)

        # Two buttons at once must ride in a single bitfield.
        await feed4("LEFT", ["left"])
        await next_notify4(P.ASYNC_CHAR_UUID)
        await feed4("RIGHT", ["a"])
        note = await next_notify4(P.ASYNC_CHAR_UUID)
        decoded = P.parse_ride_keypad(note.payload[16:][1:])
        held = sorted(n for n, m in P.BUTTON_MASKS.items()
                      if P.is_pressed(decoded.button_map, m))
        check("simultaneous buttons share one frame", held, ["a", "left"])

        w4.close()
        await asyncio.sleep(0.2)
        check("subscription cleared on disconnect", out4.ready, False)
    finally:
        await bridge4.stop_outputs()

    # --- MyWhoosh keystrokes ----------------------------------------------
    print("\nMyWhoosh keystrokes (the shortcuts MyWhoosh actually publishes)")
    from zwiftbridge.outputs.keystrokes import ACTION_KEYS, KEYCODES, NO_KEY, _split_key

    # K is up, I is down -- confirmed against a running MyWhoosh, against what
    # the published shortcut lists claim. Pinned here so it does not drift back.
    check("shift up is K", ACTION_KEYS["shift_up"], "k")
    check("shift down is I", ACTION_KEYS["shift_down"], "i")
    check("steer left is A", ACTION_KEYS["steer_left"], "a")
    check("steer right is D", ACTION_KEYS["steer_right"], "d")
    check("minimal UI is U", ACTION_KEYS["minimal_ui"], "u")
    check("hide-all UI is H", ACTION_KEYS["toggle_ui"], "h")
    check("every mapped key has a keycode",
          sorted(k for spec in ACTION_KEYS.values()
                 for k in [_split_key(spec)[1]] if k not in KEYCODES), [])
    check("emotes 1-7 all have keycodes",
          [str(n) for n in range(1, 8) if str(n) not in KEYCODES], [])
    # Fullscreen is the one binding that differs by platform: MyWhoosh uses
    # ctrl+cmd+F on macOS and F11 on Windows.
    check("fullscreen is right for this platform",
          _split_key(ACTION_KEYS["fullscreen"]),
          ([], "f11") if sys.platform == "win32" else (["ctrl", "cmd"], "f"))

    keys = KeystrokeOutput()
    await keys.start()
    # MyWhoosh publishes no camera/uturn/tuck shortcut: say so, do not fake it.
    for action_name in NO_KEY:
        result = await keys.send(parse_action(action_name), True)
        check(f"{action_name} reports it has no key",
              "no keyboard shortcut" in (result or ""), True)

    # --- controller haptics -----------------------------------------------
    print("\ncontroller haptics")
    check("vibrate frame is the known-good pattern", P.vibrate_command(),
          bytes([0x12, 0x12, 0x08, 0x0A, 0x06, 0x08, 0x02, 0x10, 0x00, 0x18, 0x20]))
    check("duration is the last byte", P.vibrate_command(0x40)[-1], 0x40)
    check("duration clamps into range",
          (P.vibrate_command(0)[-1], P.vibrate_command(999)[-1]), (0x01, 0x7F))
    check("legacy vibrate=off still disables everything",
          config_module._buzz_map({"vibrate": "off"}, "t")[0], False)
    check("legacy vibrate=shift becomes a per-button map",
          sorted(b for b, on in
                 config_module._buzz_map({"vibrate": "shift"}, "t")[1].items() if on),
          ["shift_dn_l", "shift_dn_r", "shift_up_l", "shift_up_r"])
    check("legacy vibrate_except still silences its buttons",
          config_module._buzz_map({"vibrate_except": ["a"]}, "t")[1]["a"], False)
    check("paddles are off by default, arrows are on",
          (config_module.DEFAULT_BUZZ["paddle_l"],
           config_module.DEFAULT_BUZZ["left"]), (False, True))

    class FakeLink:
        def __init__(self):
            self.buzzes = 0

        async def vibrate(self, duration=0x20):
            self.buzzes += 1

    async def buzz_count(button: str, *, master: bool = True,
                         per_button: dict | None = None) -> int:
        cfg = config_module.load(overlay=False)
        cfg.vibrate = master
        if per_button:
            cfg.vibrate_buttons = {**cfg.vibrate_buttons, **per_button}
        b = Bridge(cfg)
        side = Side("aa", "left")
        # Paddles arrive as analog squeezes, not bitfield presses.
        if button in P.ANALOG_BUTTONS:
            frame = keypad_frame([], {P.ANALOG_BUTTONS.index(button): 100})
        else:
            frame = keypad_frame([button])
        side.tracker.update(P.parse_ride_keypad(frame))
        b.sides["aa"] = side
        link = FakeLink()
        b._links["aa"] = link
        await b._buzz(button)
        return link.buzzes

    check("a bound button buzzes", await buzz_count("a"), 1)
    # Paddles are held, not clicked. The arrows steer too and must still buzz,
    # so the setting is per-button, not per-action.
    check("the left steering paddle stays silent",
          await buzz_count("paddle_l"), 0)
    check("the right steering paddle stays silent",
          await buzz_count("paddle_r"), 0)
    check("the d-pad arrows still buzz while steering",
          await buzz_count("left"), 1)
    check("the master switch silences everything",
          await buzz_count("a", master=False), 0)
    # The panel writes exactly this shape, so both directions must stick.
    check("a button can be switched off on its own",
          await buzz_count("a", per_button={"a": False}), 0)
    check("a paddle can be switched on if you really want it",
          await buzz_count("paddle_l", per_button={"paddle_l": True}), 1)

    unbound = config_module.load(overlay=False)
    unbound.bindings.pop("up", None)
    b = Bridge(unbound)
    b.sides["aa"] = Side("aa", "left")
    b.sides["aa"].tracker.update(P.parse_ride_keypad(keypad_frame(["up"])))
    b._links["aa"] = link = FakeLink()
    await b._buzz("up")
    check("an unbound button does not pretend to work", link.buzzes, 0)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
