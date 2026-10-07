#!/bin/bash
# Compile/package only. Never installs or launches the root service.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
cd "$(dirname "$0")/.."
if [ "$(id -u)" = 0 ] || [ "$#" -gt 1 ]; then
  echo 'Usage: bash scripts/build-background-service.sh [destination], as ordinary user' >&2
  exit 2
fi
destination="${1:-.local-engine/background-service}"
mkdir -p "$destination"
for arch in x86_64; do
  clang -arch "$arch" -mmacosx-version-min=12.0 -ffile-prefix-map="$PWD"=. -fdebug-prefix-map="$PWD"=. -O2 -fobjc-arc -Wall -Wextra \
    -Wno-deprecated-declarations -framework Foundation -framework Security \
    -framework SystemConfiguration -lbsm Sources/NativolService/main.m \
    -o "$destination/NativolService-$arch"
done
mv "$destination/NativolService-x86_64" "$destination/NativolService"
test "$(lipo -archs "$destination/NativolService")" = x86_64
/usr/bin/strip -S "$destination/NativolService"
codesign --force --sign - --options runtime --identifier org.nativol.service "$destination/NativolService"
codesign --verify --strict "$destination/NativolService"
/usr/bin/python3 -I -S - "$destination" <<'PY'
import hashlib,json,os,pathlib,sys,tempfile
p=pathlib.Path(sys.argv[1])
with tempfile.NamedTemporaryFile(mode='w',prefix='.manifest-',dir=p,delete=False) as stream:
    json.dump({'schemaVersion':1,'files':{'NativolService':hashlib.sha256((p/'NativolService').read_bytes()).hexdigest()}},stream,sort_keys=True,separators=(',',':'))
os.replace(stream.name,p/'manifest.json')
PY
chmod 0555 "$destination/NativolService"
chmod 0444 "$destination/manifest.json"
chmod 0755 "$destination"
echo "Built service payload (not installed or launched): $destination"
