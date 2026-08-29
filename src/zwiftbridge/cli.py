"""CLI. One subcommand per step: scan, dump, buttons, then run."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys

from . import protocol as P
from . import config as config_module
from . import ride
from .bridge import Bridge
from .outputs.whoosh_link import LINK_PORT


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("bleak").setLevel(logging.WARNING)


def _lan_ip() -> str:
    """Best-guess LAN address to show the user for the iPad setup."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 1))
        return sock.getsockname()[0]
    except OSError:
        return "your Mac's Wi-Fi IP"
    finally:
        sock.close()


# --- step 1: scan ----------------------------------------------------------


async def cmd_scan(args) -> int:
    print(f"Scanning {args.timeout:.0f}s for Zwift devices...\n")
    found = await ride.scan(args.timeout)
    if not found:
        print("Nothing found. Wake the Ride by pressing a button, then retry.")
        return 1
    for hit in found:
        print(f"  {hit.type_name}")
        print(f"    address  {hit.address}")
        print(f"    name     {hit.name}")
        print(f"    rssi     {hit.rssi} dBm")
        print(f"    mfr data {hit.raw_manufacturer.hex(' ')}")
        print()
    rides = [h for h in found if h.is_ride]
    if rides:
        joined = ", ".join(f'"{r.address}"' for r in rides)
        print(f"Pin them: set addresses = [{joined}] in config.toml")
        if len(rides) < 2:
            print("Only one half seen -- the Ride is two peripherals. Wake the")
            print("other side and rescan, or it will be dead in game.")
    return 0


# --- step 2: dump raw frames ----------------------------------------------


async def cmd_dump(args) -> int:
    address = args.address
    if not address:
        found = await ride.find_ride(args.timeout)
        if not found:
            print("No Zwift Ride found.")
            return 1
        print(f"Found {found.type_name} [{found.address}]")
        address = found.address

    stop = asyncio.Event()

    def on_frame(opcode: int, payload: bytes) -> None:
        name = P.OPCODE_NAMES.get(opcode, f"0x{opcode:02x}")
        if opcode == P.OP_EMPTY and not args.all:
            return
        line = f"{name:<15} {payload.hex(' ')}"
        if opcode == P.OP_RIDE_KEYPAD:
            try:
                status = P.parse_ride_keypad(payload)
                held = [n for n, m in P.BUTTON_MASKS.items()
                        if P.is_pressed(status.button_map, m)]
                line += f"\n{'':<15} buttonMap=0x{status.button_map:05x}"
                line += f" held={held or '-'}"
                if status.analog:
                    line += f" analog={status.analog}"
            except ValueError as exc:
                line += f"  <decode failed: {exc}>"
        print(line)

    link = ride.RideLink(address, on_frame)
    await link.connect(disconnect_cb=lambda _c: stop.set())
    print("Connected. Press buttons; ctrl-C to stop.\n")
    try:
        await stop.wait()
    finally:
        await link.disconnect()
    return 0


# --- step 3: named buttons -------------------------------------------------


async def cmd_buttons(args) -> int:
    cfg = config_module.load(args.config)
    cfg.outputs = ["console"]
    if args.address:
        cfg.addresses = [args.address]
    print("Press buttons to see their names and bound actions. Ctrl-C to stop.\n")
    await Bridge(cfg).run()
    return 0


# --- steps 4-6: the real thing --------------------------------------------


async def cmd_run(args) -> int:
    cfg = config_module.load(args.config)
    if args.output:
        cfg.outputs = args.output
    if args.address:
        cfg.addresses = [args.address]

    if any(o.startswith(("zwift_dircon", "obp_")) for o in cfg.outputs):
        print()
        print(f"  Advertising 'zwiftbridge' on {_lan_ip()} over Wi-Fi.")
        print("  On the device running MyWhoosh (iPad or this Mac):")
        print("    1. same Wi-Fi network as this Mac")
        print("    2. MyWhoosh > settings > enable Virtual Shifting")
        print("    3. pair your trainer as usual, then scan for controllers")
        name = cfg.output_settings.get("zwift_dircon", {}).get(
            "device_name", "Zwift Ride (zwiftbridge)")
        print(f"    4. '{name}' appears in the list -- pair it")
        print()
    elif "whoosh_link" in cfg.outputs:
        print()
        print(f"  MyWhoosh Link listening on {_lan_ip()}:{LINK_PORT}")
        print("  Requires the MyWhoosh Link companion app to have run once.")
        print()

    await Bridge(cfg).run()
    return 0


async def cmd_ui(args) -> int:
    import webbrowser

    from .web import ControlServer

    server = ControlServer(args.config, port=args.port)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  zwiftbridge control panel: {url}")
    print("  ctrl-C to quit\n")
    if not args.no_browser:
        webbrowser.open(url)
    # Ctrl-C must withdraw the mDNS advertisement, or a stale "zwiftbridge"
    # lingers in MyWhoosh's device list pointing at a dead port. A signal
    # handler gives us a clean async shutdown; unwinding from KeyboardInterrupt
    # does not, because the loop is already tearing down by then.
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, signal_name), stopping.set)
        except (NotImplementedError, AttributeError):
            pass

    try:
        serving = asyncio.create_task(server.serve())
        stopped = asyncio.create_task(stopping.wait())
        done, _ = await asyncio.wait(
            [serving, stopped], return_when=asyncio.FIRST_COMPLETED
        )
        if serving in done:
            serving.result()  # re-raise a bind failure
        else:
            print("\n  shutting down, withdrawing advertisement...")
            serving.cancel()
        await server.shutdown()
        return 0
    except OSError as exc:
        # Almost always a zwiftbridge that is still running -- often one that
        # outlived the terminal it was started from.
        print(f"  Port {args.port} is already in use: {exc}")
        print("  zwiftbridge is probably already running. Either open")
        print(f"  {url} -- it may be the panel you want --")
        print("  or stop the old one and try again:\n")
        print("      pkill -f 'main.py ui'\n")
        return 1
    return 0


def cmd_bindings(args) -> int:
    cfg = config_module.load(args.config)
    print(f"{'button':<12} {'action':<14} obp   link  keys")
    print("-" * 50)
    link_ok = {"shift_up", "shift_down", "steer_left", "steer_right",
               "emote", "camera", "uturn", "tuck"}
    from . import obp
    from .outputs.keystrokes import ACTION_KEYS
    for button in P.ALL_BUTTONS:
        action = cfg.action_for(button)
        if action is None:
            print(f"{button:<12} {'-':<14}")
            continue
        has_obp = "yes" if action.name in obp.ACTION_BUTTONS else " - "
        has_link = "yes" if action.name in link_ok else " - "
        key = ACTION_KEYS.get(action.name) or (
            str(action.arg or 1) if action.name == "emote" else None
        )
        print(f"{button:<12} {str(action):<14} {has_obp}   {has_link}   {key or ' -'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zwiftbridge",
        description="Zwift Ride controller -> MyWhoosh, over the network or as keystrokes.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--config", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="list nearby Zwift devices")
    p.add_argument("-t", "--timeout", type=float, default=8.0)
    p.set_defaults(func=cmd_scan, is_async=True)

    p = sub.add_parser("dump", help="connect and print raw notification frames")
    p.add_argument("-a", "--address")
    p.add_argument("-t", "--timeout", type=float, default=15.0)
    p.add_argument("--all", action="store_true", help="include keepalive frames")
    p.set_defaults(func=cmd_dump, is_async=True)

    p = sub.add_parser("buttons", help="print button names as you press them")
    p.add_argument("-a", "--address")
    p.set_defaults(func=cmd_buttons, is_async=True)

    p = sub.add_parser("run", help="run the bridge")
    p.add_argument("-a", "--address")
    p.add_argument("-o", "--output", action="append",
                   choices=["zwift_dircon", "obp_dircon", "obp_mdns", "whoosh_link", "keystrokes", "console"],
                   help="override configured outputs (repeatable)")
    p.set_defaults(func=cmd_run, is_async=True)

    p = sub.add_parser("ui", help="local control panel in the browser")
    p.add_argument("-p", "--port", type=int, default=8770)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui, is_async=True)

    p = sub.add_parser("bindings", help="show the current keymap")
    p.set_defaults(func=cmd_bindings, is_async=False)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    try:
        if args.is_async:
            return asyncio.run(args.func(args))
        return args.func(args)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
