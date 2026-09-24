#!/bin/sh
# Builds /Applications/zwiftbridge.app, which runs THIS repo. macOS only --
# the Windows equivalent is scripts/install_shortcut.ps1.
#
# The bundle is a launcher, not a copy: edit the code here and relaunch the app
# to get the change. Re-run this script only if you move the repo.
set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="/Applications/zwiftbridge.app"

if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "No .venv here. Create it first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "Installing the desktop shell into .venv"
"$REPO/.venv/bin/pip" install --quiet --upgrade \
  pywebview pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-WebKit

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>zwiftbridge</string>
    <key>CFBundleDisplayName</key>       <string>zwiftbridge</string>
    <key>CFBundleIdentifier</key>        <string>com.amirlagasse.zwiftbridge</string>
    <key>CFBundleVersion</key>           <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleSignature</key>         <string>????</string>
    <key>CFBundleExecutable</key>        <string>zwiftbridge</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>    <string>11.0</string>
    <key>NSHighResolutionCapable</key>   <true/>
    <key>LSUIElement</key>               <false/>
    <key>NSBluetoothAlwaysUsageDescription</key>
    <string>zwiftbridge talks to your Zwift Ride controller over Bluetooth.</string>
    <key>NSLocalNetworkUsageDescription</key>
    <string>zwiftbridge sends your button presses to MyWhoosh over your local network.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/zwiftbridge" <<LAUNCHER
#!/bin/bash
# zwiftbridge desktop launcher. Runs live code out of the repo.
REPO="$REPO"
cd "\$REPO" || exit 1
PYTHONPATH="\$REPO/src\${PYTHONPATH:+:\$PYTHONPATH}" \\
  exec "\$REPO/.venv/bin/python" -m zwiftbridge.desktop \\
  >> "\$HOME/Library/Logs/zwiftbridge.log" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/zwiftbridge"

printf 'APPL????' > "$APP/Contents/PkgInfo"

# icon_app.png is the Dock-shaped one (squircle, inset); icon.png is the
# full-bleed favicon and only stands in if the app icon was never rendered.
ICONS="$REPO/src/zwiftbridge/assets/icons"
ART="$ICONS/icon_app.png"
[ -f "$ART" ] || ART="$ICONS/icon.png"

if [ -f "$ART" ]; then
  echo "Building the icon"
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z $size $size "$ART" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) "$ART" \
      --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
fi

# Bump the mtime so Finder notices a rebuilt bundle and re-reads the icon.
touch "$APP"

echo
echo "Installed $APP"
echo "It runs the code in $REPO, so edits show up on relaunch."
echo
echo "Bluetooth and Accessibility permissions are per-app, so the first launch"
echo "will ask again even if your terminal was already allowed."
