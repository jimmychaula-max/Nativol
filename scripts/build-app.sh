#!/bin/bash
# Build locally only; never install, launch, enroll, or touch a physical drive.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:---intel}" in --intel|--native) ;; *) echo 'Usage: scripts/build-app.sh [--intel]' >&2; exit 2 ;; esac
if [ "$#" -gt 1 ] || [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != x86_64 ] || [ "$(id -u)" = 0 ]; then
  echo 'Build the Intel beta as an ordinary user on an Intel Mac.' >&2; exit 2
fi
# Rebuild GPL components from the included, verified source and patches.
bash scripts/engine-build.sh x86_64
bash scripts/build-driver-dev.sh x86_64
bash scripts/build-ntfs-health.sh x86_64
mkdir -p dist
stage="$(mktemp -d "$PWD/dist/.nativol-build.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
app="$stage/Nativol.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swift build -c release --arch x86_64 \
  -Xswiftc -debug-prefix-map -Xswiftc "$PWD=." \
  -Xswiftc -file-prefix-map -Xswiftc "$PWD=."
intel_bin="$(swift build -c release --arch x86_64 --show-bin-path)/Nativol"
cp "$intel_bin" "$app/Contents/MacOS/Nativol"
/usr/bin/strip -S "$app/Contents/MacOS/Nativol"
cp Resources/Info.plist "$app/Contents/Info.plist"
bash scripts/build-personal-backend.sh "$app/Contents/Resources/Backend"
bash scripts/build-background-service.sh "$app/Contents/Resources/Service"
swift scripts/make-icon.swift "$stage/AppIcon.iconset"
/usr/bin/iconutil -c icns "$stage/AppIcon.iconset" -o "$app/Contents/Resources/AppIcon.icns"
# Ad-hoc beta identity only. This is not Developer ID signing or notarization.
/usr/bin/codesign --force --sign - --options runtime "$app"
/usr/bin/codesign --verify --deep --strict "$app"
/usr/bin/python3 -I -S scripts/release-inventory.py "$app" > "$stage/build-record.json"
if [ -d dist/Nativol.app ]; then
  rm -rf dist/Nativol.previous.app
  mv dist/Nativol.app dist/Nativol.previous.app
fi
mv "$app" dist/Nativol.app
mv "$stage/build-record.json" dist/build-record.json
echo "Built Intel beta: $PWD/dist/Nativol.app"
echo 'Not installed. This beta is ad-hoc signed and is not notarized.'
