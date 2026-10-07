#!/bin/bash
# Build a local Intel beta DMG. Never installs or publishes the app.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
cd "$(dirname "$0")/.."
if [ "$#" -ne 0 ] || [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo 'Usage: bash scripts/package-test-dmg.sh (ordinary macOS user, no arguments)' >&2; exit 2
fi
app="$PWD/dist/Nativol.app"
test -d "$app"
test ! -L "$app"
test -f dist/build-record.json
test -f docs/INTEL-BETA.md
test -f THIRD_PARTY_NOTICES.md
test -f LICENSE
stage="$(mktemp -d "$PWD/dist/.nativol-dmg.XXXXXX")"
mounted_device=''
cleanup() {
  if [ -n "$mounted_device" ]; then
    /usr/bin/hdiutil detach "$mounted_device" >/dev/null || true
  fi
  /bin/rm -rf "$stage"
}
trap cleanup EXIT
/usr/bin/python3 -I -S scripts/release-inventory.py "$app" > "$stage/current-build.json"
/usr/bin/python3 -I -S - dist/build-record.json "$stage/current-build.json" <<'PY'
import json,sys
if json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])):
    raise SystemExit('App or build inputs changed after compilation; rebuild before packaging')
PY
source_archive="$(/usr/bin/python3 -I -S scripts/package-source.py)"
version="$(/usr/bin/python3 -I -S - "$stage/current-build.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['appVersion'])
PY
)"
case "$version" in ''|*[!A-Za-z0-9._-]*) echo 'Unsafe version' >&2; exit 1 ;; esac
final="$PWD/dist/Nativol-$version-Intel-Beta.dmg"
test ! -e "$final"
test ! -L "$final"
contents="$stage/contents"
mkdir "$contents"
/usr/bin/ditto "$app" "$contents/Nativol.app"
/bin/ln -s /Applications "$contents/Applications"
/bin/cp docs/INTEL-BETA.md "$contents/Read Me First.md"
/bin/cp THIRD_PARTY_NOTICES.md LICENSE "$contents/"
/usr/bin/ditto licenses "$contents/Licenses"
/bin/cp "$source_archive" "$source_archive.sha256" "$contents/"
/bin/cp "$stage/current-build.json" "$contents/BUILD-RECORD.json"
/usr/bin/python3 -I -S - "$contents" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);files={}
for item in sorted(p.rglob('*')):
    rel=str(item.relative_to(p))
    if item.is_symlink():
        if rel != 'Applications' or str(item.readlink()) != '/Applications':
            raise SystemExit('Unexpected DMG symlink: '+rel)
    elif item.is_file():
        files[rel]=hashlib.sha256(item.read_bytes()).hexdigest()
record={'schemaVersion':1,'kind':'nativol-intel-testing-dmg','productionReady':False,
        'notarized':False,'macFUSEBundled':False,'files':files}
(p/'PACKAGE-MANIFEST.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
PY
/usr/bin/hdiutil create -quiet -srcfolder "$contents" -volname 'Nativol Intel Beta' \
  -fs HFS+ -format UDZO "$stage/image.dmg"
/usr/bin/hdiutil verify "$stage/image.dmg"
/usr/bin/hdiutil attach -readonly -nobrowse -plist "$stage/image.dmg" > "$stage/attached.plist"
mounted_device="$(/usr/bin/python3 -I -S - "$stage/attached.plist" <<'PY'
import plistlib,sys
entities=plistlib.load(open(sys.argv[1],'rb'))['system-entities']
print(next(row['dev-entry'] for row in entities if row.get('mount-point')))
PY
)"
/usr/bin/python3 -I -S - "$stage/attached.plist" <<'PY'
import hashlib,json,pathlib,plistlib,subprocess,sys
entities=plistlib.load(open(sys.argv[1],'rb'))['system-entities']
mount=pathlib.Path(next(row['mount-point'] for row in entities if row.get('mount-point')))
manifest=json.loads((mount/'PACKAGE-MANIFEST.json').read_bytes())
actual={}
for path in sorted(mount.rglob('*')):
    relative=str(path.relative_to(mount))
    if path.is_symlink():
        if relative != 'Applications' or str(path.readlink()) != '/Applications':
            raise SystemExit('Unexpected mounted symlink: '+relative)
    elif path.is_file():
        # HFS+ volume metadata is not an artifact payload file.
        if relative.split('/')[0] in ('.DS_Store','.fseventsd','.Spotlight-V100','.Trashes'):
            continue
        if relative != 'PACKAGE-MANIFEST.json':
            actual[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
if actual != manifest['files']:
    raise SystemExit('Mounted DMG payload differs from package manifest')
subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(mount/'Nativol.app')],check=True)
print('Read-only mounted DMG verified:',len(actual),'files')
PY
/usr/bin/hdiutil detach "$mounted_device"
mounted_device=''
/usr/bin/python3 -I -S - "$stage/image.dmg" "$final" <<'PY'
import os,sys
# Exclusive creation; never replace an earlier package with the same name.
os.link(sys.argv[1],sys.argv[2],follow_symlinks=False)
PY
(cd "${final%/*}" && /usr/bin/shasum -a 256 "${final##*/}") > "$final.sha256"
echo "Intel testing DMG: $final"
echo "Corresponding source: $source_archive"
echo 'Ad-hoc signed beta. Not notarized. No upload or installation was performed.'
