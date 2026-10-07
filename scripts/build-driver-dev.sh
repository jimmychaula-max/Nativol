#!/bin/bash
# Compile only. Does not install a runtime, register extensions, or open volumes.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export LC_ALL=C

project_root="$(cd "$(dirname "$0")/.." && pwd)"
engine_root="$project_root/.local-engine"
sdk_root="/" # Official macFUSE 5.4.0 must be installed separately.
host_arch="$(uname -m)"
target_arch="${1:-$host_arch}"
case "$target_arch" in
  x86_64) ;;
  *) echo "Usage: scripts/build-driver-dev.sh [x86_64]" >&2; exit 2 ;;
esac
if [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo "Run as a normal user on macOS with Apple Command Line Tools." >&2
  exit 1
fi
if [ -L "$engine_root" ]; then
  echo "Refusing a symlink at the local development root." >&2
  exit 1
fi

source_archive="$engine_root/downloads/ntfs-3g_ntfsprogs-2026.9.28.tgz"
pkgconf="$engine_root/work/pkgconf-2.5.1-$host_arch/pkgconf"
patch_file="$project_root/patches/ntfs-3g-2026.9.28-no-plugins-inode.patch"
darwin_patch="$project_root/patches/ntfs-3g-2026.9.28-darwin-fuse.patch"
descriptor_patch="$project_root/patches/ntfs-3g-2026.9.28-darwin-fd.patch"
if [ ! -f "$source_archive" ] || [ ! -x "$pkgconf" ]; then
  echo "First run scripts/engine-build.sh to prepare the pinned source and local pkgconf." >&2
  exit 1
fi
if [ ! -f /usr/local/include/fuse/fuse.h ]; then
  echo "Install official macFUSE 5.4.0 with its development headers separately. See docs/BUILDING.md." >&2
  exit 1
fi
expected_source=350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8
actual_source="$(shasum -a 256 "$source_archive" | awk '{print $1}')"
if [ "$actual_source" != "$expected_source" ]; then
  echo "NTFS-3G source checksum mismatch; no source executed." >&2
  exit 1
fi
# These digests were measured from the official, signature-checked 5.4.0
# package and bind this experiment to the inspected headers and runtime code.
(
  cd "$sdk_root"
  shasum -a 256 --check <<'SDK_CHECKSUMS'
7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42  usr/local/lib/libfuse.2.dylib
e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6  Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/MFMount
4d3944c66c14d0a3b83267da697ec9cb64241ee9019649c24f07820ea406ad15  usr/local/include/fuse/fuse.h
eec3a2f2e5a5c503b3835a2fa5bb8fab52faaa0acbc84c694bddbc46d28c3170  usr/local/include/fuse/fuse_common.h
bbfcaff369604fc5f14eb92386f38f70c2ab7c6c9a4b28db022a386e69858a70  usr/local/include/fuse/fuse_common_compat.h
72771df4823f3f4f6a90a685f3c00c810cc87e65218d6f4287dbd1c69ca70fb5  usr/local/include/fuse/fuse_compat.h
7d67999b03d629bccde48ba4c3284a9287c55fbc8620af96fb920063bae8cfcf  usr/local/include/fuse/fuse_lowlevel.h
a84919fda51e7ea83a35f0f0adc5845bc3d53de45153f268ff8f05184baed4d5  usr/local/include/fuse/fuse_lowlevel_compat.h
ae678aebb063aba3112c9b2f62c013e7490daaf29ee4a41edf602eb6775bb563  usr/local/include/fuse/fuse_opt.h
SDK_CHECKSUMS
)
codesign --verify --deep --strict "$sdk_root/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework"

mkdir -p "$engine_root/work" "$engine_root/logs" "$engine_root/driver"
if ! mkdir "$engine_root/.driver-build-lock" 2>/dev/null; then
  echo "Another driver build is active (.local-engine/.driver-build-lock)." >&2
  exit 1
fi
sdk_temp=""
cleanup() {
  if [ -n "$sdk_temp" ]; then
    rm -f "$sdk_temp/payload"
    rmdir "$sdk_temp"
  fi
  rmdir "$engine_root/.driver-build-lock"
}
trap cleanup EXIT
# Autotools splits space-containing include paths. A private temporary alias
# supplies a stable temporary alias without changing the installed SDK.
sdk_temp="$(mktemp -d /tmp/nativol-fuse-sdk.XXXXXX)"
ln -s "$sdk_root" "$sdk_temp/payload"
build_dir="$(mktemp -d "$engine_root/work/driver-$target_arch.XXXXXX")"
tar -xzf "$source_archive" --strip-components=1 -C "$build_dir"
patch --batch --forward -d "$build_dir" -p1 -i "$patch_file"
patch --batch --forward -d "$build_dir" -p1 -i "$darwin_patch"
patch --batch --forward -d "$build_dir" -p1 -i "$descriptor_patch"

configure_args=(
  --with-fuse=external --disable-ntfsprogs
  --disable-shared --enable-static --disable-library
  --disable-plugins --disable-crypto --disable-extras
  --disable-ldconfig --disable-mount-helper
)
if [ "$target_arch" != "$host_arch" ]; then
  configure_args+=(--build="$host_arch-apple-darwin" --host="$target_arch-apple-darwin")
fi
build_log="$engine_root/logs/driver-$target_arch.log"
echo "Compiling NTFS-3G 2026.9.28 mount driver ($target_arch), without installation..."
(
  cd "$build_dir"
  PKG_CONFIG="../pkgconf-2.5.1-$host_arch/pkgconf" \
  FUSE_MODULE_CFLAGS="-I$sdk_temp/payload/usr/local/include/fuse -D_FILE_OFFSET_BITS=64 -D_DARWIN_USE_64_BIT_INODE=1" \
  FUSE_MODULE_LIBS="-L$sdk_temp/payload/usr/local/lib -lfuse -pthread" \
  CC=/usr/bin/clang \
  CFLAGS="-O2 -arch $target_arch -mmacosx-version-min=12.0 -D_DARWIN_USE_64_BIT_INODE=1 -Werror=sometimes-uninitialized" \
  LDFLAGS="-arch $target_arch -mmacosx-version-min=12.0" \
  ac_cv_header_libintl_h=no \
  ./configure "${configure_args[@]}"
  make -j4
) > "$build_log" 2>&1 || {
  tail -70 "$build_log" >&2
  echo "Build failed; source and log retained: $build_log" >&2
  exit 1
}

output="$engine_root/driver/$target_arch"
mkdir -p "$output/licenses"
cp "$build_dir/src/ntfs-3g" "$output/ntfs-3g"
cp "$build_dir/src/ntfs-3g.probe" "$output/ntfs-3g.probe"
lipo "$output/ntfs-3g" -verify_arch "$target_arch"
lipo "$output/ntfs-3g.probe" -verify_arch "$target_arch"
# A completely unsigned Intel executable cannot configure macFUSE's vendor-team
# XPC peer requirement on our Sequoia host (EINVAL). Give these local builds an
# ad-hoc identity; do not alter vendor code, requirements, or OS policy.
# This is not Developer ID signing or notarization for distribution.
for executable in ntfs-3g ntfs-3g.probe; do
  /usr/bin/strip -S "$output/$executable"
  codesign --force --sign - --identifier "org.nativol.dev.$executable" "$output/$executable"
  codesign --verify --strict "$output/$executable"
  codesign -dvv "$output/$executable" > "$output/$executable.signature.txt" 2>&1
done
cp "$build_dir/COPYING" "$output/licenses/NTFS-3G-COPYING"
cp "$build_dir/COPYING.LIB" "$output/licenses/NTFS-3G-COPYING.LIB"
cp "$patch_file" "$output/source-patch.diff"
cp "$darwin_patch" "$output/darwin-fuse-patch.diff"
cp "$descriptor_patch" "$output/darwin-fd-patch.diff"

cat > "$output/build-manifest.txt" <<EOF
Status: Intel beta compilation; not a filesystem safety certification
NTFS-3G: 2026.9.28; upstream tag commit 7f0f841fc52cf719106c5c93bafe465004e36816
Source archive SHA-256: $expected_source
macFUSE: 5.4.0; external FUSE 2 API reports 2.9.9 (29)
Official SDK DMG SHA-256: 861814f0ac7fa8f6547ea40cdd49a36ac84bcc7d34f38a1fa74e8cf68b0401c5
SDK: separately installed official macFUSE 5.4.0
Build source: $build_dir
Compiler log: $build_log
Host architecture: $host_arch
Target architecture: $target_arch
Deployment target: macOS 12.0, not a runtime compatibility certificate
NTFS library: static; macFUSE library: dynamic, original absolute install names
Flags: _FILE_OFFSET_BITS=64, _DARWIN_USE_64_BIT_INODE=1, -Werror=sometimes-uninitialized
Configure override: ac_cv_header_libintl_h=no
Plugins, crypto, extras, mount-helper installation: disabled
Patch: initialize ntfs_fuse_create ni to NULL for disabled-plugin refusal path
Patch: select FSTYPE_FUSE on Darwin; retain block-device I/O without Linux fuseblk options
Patch: preserve exact inherited /dev/fd/3 on Darwin and duplicate its validated compatible regular/block descriptor without pathname reopen
Patch regression: run scripts/test-driver-callback.py separately; not part of this build
Signing: local ad-hoc, org.nativol.dev.<executable>, no entitlements; NOT Developer ID or notarized
Signing purpose: permit XPC LWCR configuration by a locally built executable on Sequoia
System install, extension registration, mount, probe with a device: none
App bundling: none
The version smoke check uses the separately installed, hash-verified runtime.
Nativol installs its own protected copy of the verified local library; no vendor library is redistributed.
EOF
shasum -a 256 "$patch_file" >> "$output/build-manifest.txt"
shasum -a 256 "$darwin_patch" >> "$output/build-manifest.txt"
shasum -a 256 "$descriptor_patch" >> "$output/build-manifest.txt"
clang --version >> "$output/build-manifest.txt"
xcrun --show-sdk-version >> "$output/build-manifest.txt"
otool -L "$output/ntfs-3g" > "$output/linked-libraries.txt"
shasum -a 256 "$output/ntfs-3g" "$output/ntfs-3g.probe" > "$output/binary-checksums.txt"

if [ "$target_arch" = "$host_arch" ]; then
  # Version-only execution has no device argument and uses the verified runtime.
  "$output/ntfs-3g" --version 2>&1 | tee "$output/version-smoke.txt"
else
  echo "Cross-compiled only; no execution on this host." | tee "$output/version-smoke.txt"
fi
echo "Driver artifacts: $output"
echo "No device was opened, mounted, formatted, probed, or modified. This script does not install a runtime."
