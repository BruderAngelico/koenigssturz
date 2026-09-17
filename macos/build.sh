#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
MACOS="$ROOT/macos"
DIST="$ROOT/dist"
BUILD="$MACOS/build"
PYDEPS_WEB="$MACOS/pydeps"
PYDEPS_READER="$MACOS/pydeps-reader"
ICONS="$ROOT/icons"

echo "Python-Abhängigkeiten …"
python3 -m pip install -q --upgrade pip
python3 -m pip install -q -r "$ROOT/requirements.txt" 'urllib3<2' -t "$PYDEPS_WEB"
python3 -m pip install -q -r "$ROOT/requirements-reader.txt" 'urllib3<2' -t "$PYDEPS_READER"

mkdir -p "$BUILD" "$DIST"
echo "Swift-Hülle kompilieren …"
swiftc -O -parse-as-library \
  -target "$(uname -m)-apple-macos12.0" \
  -sdk "$(xcrun --show-sdk-path)" \
  -framework Cocoa -framework WebKit \
  -o "$BUILD/host" \
  "$MACOS/App.swift"

assemble() {
  app_name="$1"
  plist="$2"
  pydeps="$3"
  icon_icns="$4"
  shift 4
  app="$DIST/$app_name.app"
  contents="$app/Contents"
  res="$contents/Resources"
  macdir="$contents/MacOS"
  rm -rf "$app"
  mkdir -p "$macdir" "$res/python"
  cp "$BUILD/host" "$macdir/$app_name"
  cp "$plist" "$contents/Info.plist"
  if [ -f "$icon_icns" ]; then
    cp "$icon_icns" "$res/AppIcon.icns"
  fi
  for f in "$@"; do
    cp "$ROOT/$f" "$res/python/"
  done
  echo "  $app_name: pydeps kopieren …"
  rsync -a --delete "$pydeps/" "$res/pydeps/"
  find "$res/pydeps" -type d -name "__pycache__" -prune -exec rm -rf {} +
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$app" >/dev/null 2>&1 || true
  fi
  echo "Fertig: $app"
}

assemble "Königssturz" "$MACOS/Info-koenigssturz.plist" "$PYDEPS_WEB" "$ICONS/Koenigssturz.icns" \
  kingfall.py \
  kingfall_web.py \
  kingfall_analyze.py \
  kingfall_vereinsarchiv.py \
  kingfall_va_store.py \
  kingfall_va_pack.py \
  kingfall_macos.py

assemble "VA Reader" "$MACOS/Info-reader.plist" "$PYDEPS_READER" "$ICONS/VAReader.icns" \
  kingfall_va_reader.py \
  kingfall_va_store.py \
  kingfall_va_pack.py \
  kingfall_macos.py

rm -f "$MACOS/Info.plist"

echo "DMGs …"
hdiutil create -volname "Königssturz" -srcfolder "$DIST/Königssturz.app" -ov -format UDZO "$DIST/Königssturz.dmg" >/dev/null
hdiutil create -volname "VA Reader" -srcfolder "$DIST/VA Reader.app" -ov -format UDZO "$DIST/VA Reader.dmg" >/dev/null

echo "Im Finder doppelklicken oder:"
echo "  open \"$DIST/Königssturz.app\""
echo "  open \"$DIST/VA Reader.app\""
echo "DMG: $DIST/Königssturz.dmg"
echo "DMG: $DIST/VA Reader.dmg"
