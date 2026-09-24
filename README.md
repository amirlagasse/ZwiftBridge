# zwiftbridge

Zwift Ride controller → MyWhoosh. No subscription, no per-day button limit,
auto-connects on launch.

Runs on Windows and macOS from the same checkout. Outputs share one keymap;
pick them with checkboxes in the control panel.

| Output | Where MyWhoosh runs | Notes |
|---|---|---|
| `keystrokes` | this computer | MyWhoosh must be the front window |
| `zwift_dircon` | a tablet, or another computer | emulates a Zwift Ride over the network |
| `obp_dircon` / `obp_mdns` | a tablet, or this computer | alternative network protocols |
| `whoosh_link` | a tablet, or this computer | MyWhoosh must dial us; rarely engages |

**Riding on the same machine MyWhoosh runs on? Use `keystrokes`.** It needs no
network, no router settings and no pairing. Everything below about Wi-Fi is for
the case where MyWhoosh is on a separate device.

**Your trainer is not involved.** The KICKR pairs straight to MyWhoosh over
Bluetooth as the power source. zwiftbridge presents a *controller*, so the two
are independent and nothing proxies your power data.

## Setup

**Windows**

`setup.ps1` does all three steps (uv if installed, else pip; reuses an
existing `.venv`):

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

By hand:

```powershell
py -3 -m venv .venv
.venv\Scripts\pip install -e .            # or: -r requirements.txt
.venv\Scripts\python tests\selftest.py    # offline, no bike required
```

**macOS**

```sh
python3 -m venv .venv
.venv/bin/pip install -e .            # or: -r requirements.txt
.venv/bin/python tests/selftest.py    # offline, no bike required
```

Python 3.11 or newer. The only platform-specific dependencies are the pyobjc
packages, and pip skips them off macOS — Windows needs nothing extra, because
the keystrokes output drives `SendInput` through `ctypes`.

Want it in the Start Menu or the Dock?

```powershell
.venv\Scripts\pip install -e ".[app]"
powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
```

```sh
.venv/bin/pip install -e '.[app]' && scripts/install_app.sh
```

Both build a *launcher* around this checkout, not a copy — edit the code here
and relaunch to get the change.

## Use

```powershell
.\run.cmd          # Windows: double-clickable, or run.ps1 from a shell
```

```sh
./run.sh           # macOS
```

Opens a control panel at **http://127.0.0.1:8770** with a Start button, a live
connection status, the buttons lighting up as you press them, and a log. That
is the everyday command. `zwiftbridge run` is the same bridge headless.

First run, the OS asks for Bluetooth. Grant it — on macOS to your terminal app
rather than to the script, on Windows to Python.

If you are riding on a separate device, then over there:

1. same Wi-Fi as this computer (avoid guest networks and AP isolation)
2. MyWhoosh → settings → enable **Virtual Shifting**
3. pair your trainer as usual, then scan for controllers
4. **Zwift Ride (zwiftbridge)** appears in the list — pair it

The panel's output row flips to `paired with MyWhoosh` once it connects, and
logs which buttons MyWhoosh says it supports.

## Commands

```sh
zwiftbridge scan       # list nearby Zwift devices + manufacturer bytes
zwiftbridge dump       # raw notification frames, decoded buttonMap
zwiftbridge buttons    # press a button, see its name — for remapping
zwiftbridge bindings   # current keymap, and which output supports each action
zwiftbridge run        # the bridge, headless
zwiftbridge run -o keystrokes   # override the configured output
zwiftbridge ui         # the control panel (what run.cmd / run.sh does)
```

Pin the controller once you know its address (`scan` prints it) by setting
`address` in `config.toml` — it skips the scan and connects faster.

## Using the keystrokes output

The whole setup on Windows, and the fallback on macOS.

1. **macOS only:** System Settings → Privacy & Security → **Accessibility** →
   add your terminal app. Without this `CGEventPost` silently no-ops — no
   error, nothing happens. `zwiftbridge run -o keystrokes` prints the
   permission state at startup.
2. **Windows only:** if MyWhoosh runs as administrator, start zwiftbridge the
   same way. UIPI drops injected input from a lower integrity level with no
   error at all, which looks exactly like a broken keymap.
   `.venv\Scripts\python scripts\check_sendinput.py` proves the injection path
   works without typing anything you can see.
3. MyWhoosh must be the front window. Keys are held back while anything else
   is, so a d-pad press cannot go arrowing around your browser.
4. Sanity-check against Notepad or TextEdit first: you should see literal
   `i`/`k` typed.

MyWhoosh's keyboard support is narrow — this is the whole of it:

| Key | Action | Our action name |
|---|---|---|
| `K` | shift up | `shift_up` |
| `I` | shift down | `shift_down` |
| `A` / `←` | steer left (hold) | `steer_left`, `nav_left` |
| `D` / `→` | steer right (hold) | `steer_right`, `nav_right` |
| `U` | toggle minimal UI | `minimal_ui` |
| `H` | hide all controls (HD build only) | `toggle_ui` |
| `1`–`7` | emotes: peace, wave, fist bump, dab, elbow flick, toast, thumbs up | `emote:1`–`emote:7` |
| `F11` (Win) / `ctrl+cmd+F` (Mac) | fullscreen ↔ windowed | `fullscreen` |

There is **no** shortcut for camera angle, U-turn, tuck, ERG toggle, intensity
or ending a ride — `camera`/`uturn`/`tuck` log a line saying so rather than
firing. Bind them only on a network output. `select`/`back`/`menu` map to
Return/Escape/Tab, which MyWhoosh does not document; its menus are click-driven.

`K` is up and `I` is down. MyWhoosh's own docs say only "use the shortcuts I
and K"; the community shortcut lists claim I is up, which is also what the key
positions suggest — both are wrong. This is what the game does on the bike, and
it matches BikeControl. Don't "fix" it back to match the docs.

Windows sends **scan codes**, not virtual keys. MyWhoosh is a Unity game and
reads raw input; a scan code is what a real keyboard puts on the wire, and a
VK-only `SendInput` often gets ignored.

## Riding on a separate device

`zwift_dircon` is the one to use. It emulates a Zwift Ride over DirCon — the
same protocol and service id (`0xFC82`) your KICKR advertises — so MyWhoosh
sees a controller it already knows how to talk to and you pair it in its normal
device list.

```
Zwift Ride ──BLE──▶ this computer ──Wi-Fi/mDNS──▶ MyWhoosh on a tablet
KICKR ──────────────────────BLE─────────────────▶ MyWhoosh on a tablet
```

### If the other device discovers it but hangs on "pairing in progress"

That is almost always **router client isolation**, not zwiftbridge. Check it in
30 seconds: run `python -m http.server 8899` here and open
`http://<this-computer-ip>:8899` in a browser on the other device. If the page
will not load, the devices cannot reach each other and no setting in this tool
will help — turn off AP/client isolation on the router, or put both devices on
a phone hotspot. This is exactly what blocked the iPad setup on the Mac.

`whoosh_link` is the older path: MyWhoosh's own Link protocol on TCP 21587,
where *MyWhoosh* has to dial us and only does so after its companion app has
run once. It has never engaged here.

## Remapping

Edit the `[buttons]` table in `config.toml`. `zwiftbridge buttons` prints
button names live so you can see what you are holding.

Buttons: `left up right down a b y z shift_up_l shift_dn_l shift_up_r
shift_dn_r powerup_l powerup_r onoff_l onoff_r paddle_l paddle_r`

Actions: `shift_up shift_down steer_left steer_right nav_up nav_down nav_left
nav_right select back menu toggle_ui minimal_ui fullscreen emote:1-7 camera:N
uturn tuck none`

`zwiftbridge bindings` shows which output can carry each action. OBP covers
everything except tuck, and MyWhoosh tells us at pairing time exactly which
buttons it honors — anything it does not is reported in the log rather than
silently dropped.

The default keymap is the natural one: every *up* paddle shifts up, every
*down* paddle shifts down. (BikeControl instead makes the whole left side shift
down and the whole right side shift up.)

## Haptics

The Ride buzzes in your hand on every button press, so a shift lands without
you looking down. It is on by default; the panel has a **Buzz the controller**
checkbox that takes effect immediately (no restart, unlike the output
checkboxes), and `config.toml` has the launch default:

```toml
[bridge]
vibrate = "all"    # "all" | "shift" (gear changes only) | "off"
vibrate_ms = 32    # raw duration byte, 1-127; 32 is the Zwift app's tick
```

Only the half that reported the button buzzes, so the tick is under the hand
that actually pressed. Unbound buttons stay silent rather than implying they
did something. The Ride and the Play have haptic motors; Click and Click v2
ignore the command.

## Files

The code is a package under `src/`; `config.toml` and the panel's
`settings.json` stay in the repo root, where you can edit them by hand.

```
config.toml                     the keymap you edit
run.cmd / run.ps1               the everyday command on Windows
run.sh                          the everyday command on macOS
pyproject.toml                  packaging, dependencies, console scripts
src/zwiftbridge/
  cli.py                        the command line (`zwiftbridge ...`)
  desktop.py                    the native window around the panel
  protocol.py                   UUIDs, opcodes, button masks, protobuf decode
  ride.py                       BLE scan, connect, RideOn handshake, dispatch
  bridge.py                     frames → actions → outputs, with reconnect
  actions.py                    the vocabulary between buttons and outputs
  config.py / settings.py       the keymap, and the panel's overlay on it
  paths.py                      where config, settings and assets live
  labels.py                     button names and the panel's layout grid
  obp.py / dircon.py            OpenBikeProtocol and DirCon framing
  web.py                        the local control panel's server
  outputs/keystrokes.py         SendInput on Windows, CGEvent on macOS
  outputs/zwift_dircon.py       Zwift Ride emulation over the network
  outputs/obp_mdns.py           OBP server + mDNS advertisement
  outputs/whoosh_link.py        TCP 21587 JSON to MyWhoosh
  assets/ui.html                the panel itself
  assets/icons/                 favicon, Dock icon, Windows .ico
scripts/install_shortcut.ps1    Start Menu + Desktop shortcut (Windows)
scripts/install_app.sh          builds /Applications/zwiftbridge.app (macOS)
scripts/check_sendinput.py      proves keystroke injection works (Windows)
scripts/make_icons.py           redraws the icons from code
scripts/sniff_mdns.py           what a tablet is actually looking for
tests/selftest.py               offline test of everything except the radio
```

## Gotchas

- **The Ride is two Bluetooth devices**, left and right, each reporting only
  its own buttons. zwiftbridge connects to both and merges them. If one half
  is asleep its buttons do nothing — press one to wake it and it joins on its
  own within ~20s. The control panel shows both halves and their state.
- Buttons are **active low**: `(buttonMap & MASK) == 0` means *pressed*.
- `buttonMap` is a repeating state snapshot, not an event — hence the edge
  detector in `protocol.py`. Skip it and one press becomes dozens of shifts.
- **zwiftbridge is not a Bluetooth device.** On the network outputs it will
  never appear in a Bluetooth scan — MyWhoosh finds it over Wi-Fi via mDNS. If
  it does not show up, check both devices are on the same network, then confirm
  the advertisement (`dns-sd -B _openbikecontrol._tcp local` on macOS).
- **Keystroke output fails silently on both platforms**, for different
  reasons: Accessibility on macOS, UIPI on Windows. Neither raises an error.
- If `scan` finds nothing, wake the Ride with a button press. If it finds the
  Ride but the custom service is missing, update its firmware in Zwift
  Companion.
