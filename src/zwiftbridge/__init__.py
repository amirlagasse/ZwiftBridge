"""zwiftbridge -- a Zwift Ride controller talking to MyWhoosh.

The bridge itself is `bridge.Bridge`: it owns the two BLE halves, turns their
frames into actions from `actions`, and hands those to whatever is listed in
`outputs`. `cli` is the command line, `web` the control panel, `desktop` the
Mac app shell around that panel.
"""

__version__ = "1.0.0"
