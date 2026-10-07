#!/bin/bash
# Historical filename; this builds the Intel external-media beta payload.
# The vendor library is a separately acquired dependency and is not bundled.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$#" -ne 1 ] || [ "$(id -u)" = 0 ]; then
  echo 'Usage: scripts/build-personal-backend.sh new-staging-directory (ordinary user)' >&2; exit 2
fi
destination="$1"
test ! -e "$destination"
mkdir -p "$destination/bin" "$destination/licenses"
clang -arch x86_64 -mmacosx-version-min=12.0 -ffile-prefix-map="$PWD"=. -fdebug-prefix-map="$PWD"=. -O2 -fobjc-arc \
  -Wno-deprecated-declarations -framework Foundation -framework DiskArbitration \
  -framework IOKit -framework SystemConfiguration Sources/NativolHelper/main.m \
  -o "$destination/bin/NativolHelper"
cp .local-engine/driver/x86_64/ntfs-3g "$destination/bin/ntfs-3g"
cp .local-engine/health/x86_64/nativol-ntfs-health "$destination/bin/nativol-ntfs-health"
cp .local-engine/driver/x86_64/licenses/* "$destination/licenses/"
for executable in NativolHelper ntfs-3g nativol-ntfs-health; do
  test "$(lipo -archs "$destination/bin/$executable")" = x86_64
  /usr/bin/strip -S "$destination/bin/$executable"
  codesign --force --sign - --identifier "org.nativol.personal.$executable" "$destination/bin/$executable"
  codesign --verify --strict "$destination/bin/$executable"
done
/usr/bin/python3 -I -S - "$destination" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1])
files={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for folder in ('bin','licenses') for f in sorted((p/folder).iterdir()) if f.is_file()}
# The installer requires this exact locally installed dependency, then verifies
# its protected copy. Its bytes are deliberately absent from the app and DMG.
files['lib/libfuse.2.dylib']='7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42'
data=json.dumps({'schemaVersion':1,'profile':'intel-external-test-v1','files':files},sort_keys=True,separators=(',',':')).encode()
(p/'manifest.json').write_bytes(data)
(p/'version.txt').write_text(hashlib.sha256(data).hexdigest()+'\n')
PY
chmod -R u=rwX,go=rX "$destination"
chmod 0555 "$destination/bin/"*
echo "Built Intel backend payload, without vendor binaries: $destination"
