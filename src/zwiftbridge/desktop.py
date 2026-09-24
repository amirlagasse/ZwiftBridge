"""Desktop-app launcher for zwiftbridge.

Starts the control server on a background thread, waits for it to answer, then
opens a native window on it -- WebKit on macOS, Edge WebView2 on Windows.
Closing the window shuts everything down, including the mDNS advertisement --
leave that up and a dead "zwiftbridge" lingers in MyWhoosh's device list.

Run with `python -m zwiftbridge.desktop`, via zwiftbridge.app on macOS, or
via the Start Menu shortcut on Windows.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import urllib.request

from . import paths

HOST = "127.0.0.1"
PORT = 8770
URL = f"http://{HOST}:{PORT}"

_server = None
_loop = None


def _run_server() -> None:
    """The control server, on its own loop in its own thread.

    Its own loop matters: asyncio signal handlers only install on the main
    thread, and the window owns that. Shutdown is driven from _shutdown()
    instead, via run_coroutine_threadsafe.
    """
    global _server, _loop
    from .web import ControlServer

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _server = ControlServer(None, host=HOST, port=PORT)
    try:
        _loop.run_until_complete(_server.serve())
    except Exception as exc:  # noqa: BLE001 -- surface it, the window is gone
        print(f"[zwiftbridge] server stopped: {exc}", file=sys.stderr, flush=True)


def _wait_for_server(timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.15)
    return False


def _shutdown() -> None:
    """Stop the bridge before the process dies.

    Closing the window is the normal way to quit, and it must withdraw the
    mDNS advertisement. Unwinding from process exit is too late for that.
    """
    if _server is None or _loop is None or not _loop.is_running():
        return
    try:
        future = asyncio.run_coroutine_threadsafe(_server.shutdown(), _loop)
        future.result(timeout=5)
    except Exception as exc:  # noqa: BLE001 -- quitting must not hang
        print(f"[zwiftbridge] shutdown: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    # Finder launches with cwd=/, and a Windows shortcut launches with
    # whatever its "start in" says. config.toml and settings.json are
    # resolved by paths, but sit in whichever directory those chose.
    os.chdir(paths.user_dir())

    threading.Thread(target=_run_server, daemon=True).start()
    if not _wait_for_server():
        print(f"Server did not start at {URL}", file=sys.stderr)
        return 1

    try:
        import webview
    except ImportError:
        # No desktop shell installed: still useful, just open the browser.
        import webbrowser
        print(f"pywebview not installed, opening a browser at {URL}")
        print("  " + (r".venv\Scripts\pip install pywebview"
                      if sys.platform == "win32" else
                      ".venv/bin/pip install pywebview pyobjc-framework-WebKit"))
        webbrowser.open(URL)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        _shutdown()
        return 0

    # WKWebView keeps its own asset cache across launches, so an edited
    # ui.html would otherwise not show up until the cache aged out. WebView2
    # on Windows has no such problem, and the import below just no-ops there.
    try:
        import WebKit  # type: ignore
        from Foundation import NSDate  # type: ignore
        store = WebKit.WKWebsiteDataStore.defaultDataStore()
        store.removeDataOfTypes_modifiedSince_completionHandler_(
            WebKit.WKWebsiteDataStore.allWebsiteDataTypes(),
            NSDate.dateWithTimeIntervalSince1970_(0),
            lambda: None,
        )
    except Exception as exc:  # noqa: BLE001 -- cosmetic, never fatal
        print(f"[zwiftbridge] cache clear skipped: {exc}", flush=True)

    webview.create_window(
        title="zwiftbridge",
        url=URL,
        width=980,
        height=880,
        min_size=(560, 600),
    )
    webview.start()   # blocks until the window closes
    _shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
