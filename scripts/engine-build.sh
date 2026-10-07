#!/bin/bash
# Build a pinned NTFS userspace engine locally. Never installs or opens a drive.
set -euo pipefail
umask 077

project_root="$(cd "$(dirname "$0")/.." && pwd)"
engine_root="$project_root/.local-engine"
host_arch="$(uname -m)"
target_arch="${1:-$host_arch}"
case "$target_arch" in
  x86_64) ;;
  *) echo "Usage: scripts/engine-build.sh [x86_64]" >&2; exit 2 ;;
esac
if [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo "Build as a normal user on macOS with Apple Command Line Tools." >&2
  exit 1
fi
if [ -L "$engine_root" ]; then
  echo "Refusing a symlink at .local-engine." >&2
  exit 1
fi
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
command -v clang >/dev/null
xcrun --find clang >/dev/null
mkdir -p "$engine_root/downloads" "$engine_root/work" "$engine_root/logs" "$engine_root/bin"
if ! mkdir "$engine_root/.build-lock" 2>/dev/null; then
  echo "Another engine build is active (.local-engine/.build-lock)." >&2
  exit 1
fi
trap 'rmdir "$engine_root/.build-lock"' EXIT

ntfs_version=2026.9.28
ntfs_sha256=350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8
ntfs_commit=7f0f841fc52cf719106c5c93bafe465004e36816
pkgconf_version=2.5.1
pkgconf_sha256=cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243

fetch_verified() {
  local url="$1" destination="$2" expected="$3" actual
  if [ ! -f "$destination" ] && [ -f "$project_root/third-party/sources/${destination##*/}" ]; then
    cp "$project_root/third-party/sources/${destination##*/}" "$destination"
  fi
  if [ ! -f "$destination" ]; then
    curl --fail --silent --show-error --location \
      --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --connect-timeout 15 --max-time 240 \
      "$url" -o "$destination.part"
    mv "$destination.part" "$destination"
  fi
  actual="$(shasum -a 256 "$destination" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    echo "Checksum mismatch: $destination. No source was executed." >&2
    exit 1
  fi
}

ntfs_archive="$engine_root/downloads/ntfs-3g_ntfsprogs-$ntfs_version.tgz"
pkgconf_archive="$engine_root/downloads/pkgconf-$pkgconf_version.tar.xz"
fetch_verified "https://tuxera.com/opensource/ntfs-3g_ntfsprogs-$ntfs_version.tgz" \
  "$ntfs_archive" "$ntfs_sha256"
fetch_verified "https://distfiles.ariadne.space/pkgconf/pkgconf-$pkgconf_version.tar.xz" \
  "$pkgconf_archive" "$pkgconf_sha256"

# A build-time dependency only, compiled from source and never installed.
pkgconf_source="$engine_root/work/pkgconf-$pkgconf_version-$host_arch"
if [ ! -x "$pkgconf_source/pkgconf" ]; then
  mkdir -p "$pkgconf_source"
  tar -xf "$pkgconf_archive" --strip-components=1 -C "$pkgconf_source"
  echo "Building local pkgconf $pkgconf_version ($host_arch)..."
  (
    cd "$pkgconf_source"
    CC=/usr/bin/clang CFLAGS=-O2 ./configure \
      --disable-shared --enable-static --disable-dependency-tracking
    make -j4
  ) > "$engine_root/logs/pkgconf-$host_arch.log" 2>&1 || {
    tail -50 "$engine_root/logs/pkgconf-$host_arch.log" >&2
    exit 1
  }
fi
"$pkgconf_source/pkgconf" --version

# Keep each source/build directory as corresponding-source evidence. Relative
# PKG_CONFIG avoids Autoconf's unquoted invocation breaking on project spaces.
ntfs_source="$(mktemp -d "$engine_root/work/ntfs-$target_arch.XXXXXX")"
tar -xzf "$ntfs_archive" --strip-components=1 -C "$ntfs_source"
build_log="$engine_root/logs/ntfs-$target_arch.log"
configure_args=(
  --disable-ntfs-3g --enable-ntfsprogs
  --disable-shared --enable-static --disable-library
  --disable-plugins --disable-crypto --disable-extras
  --disable-ldconfig --disable-mount-helper
)
if [ "$target_arch" != "$host_arch" ]; then
  configure_args+=(--build="$host_arch-apple-darwin" --host="$target_arch-apple-darwin")
fi
echo "Building NTFS-3G $ntfs_version utilities ($target_arch)..."
(
  cd "$ntfs_source"
  # Exclude an optional gettext header: macOS provides setlocale itself. Some
  # Homebrew headers otherwise redirect it to an unlinked libintl dependency.
  PKG_CONFIG="../pkgconf-$pkgconf_version-$host_arch/pkgconf" \
    CC=/usr/bin/clang \
    CFLAGS="-O2 -arch $target_arch -mmacosx-version-min=12.0" \
    LDFLAGS="-arch $target_arch -mmacosx-version-min=12.0" \
    ac_cv_header_libintl_h=no \
    ./configure "${configure_args[@]}"
  make -j4
) > "$build_log" 2>&1 || {
  tail -70 "$build_log" >&2
  echo "Build failed; source and full log retained: $build_log" >&2
  exit 1
}

output="$engine_root/bin/$target_arch"
mkdir -p "$output/licenses"
# Export only the small utility set used in the image lab. Destructive utilities
# built by upstream, such as ntfsfix/ntfsresize, remain outside this entry point.
for tool in mkntfs ntfscp ntfscat ntfsinfo ntfsls; do
  cp "$ntfs_source/ntfsprogs/$tool" "$output/$tool"
  lipo "$output/$tool" -verify_arch "$target_arch"
done
cp "$ntfs_source/COPYING" "$output/licenses/NTFS-3G-COPYING"
cp "$ntfs_source/README" "$output/licenses/NTFS-3G-README"
cp "$pkgconf_source/COPYING" "$output/licenses/pkgconf-COPYING"
cat > "$output/build-manifest.txt" <<EOF
NTFS-3G version: $ntfs_version
Upstream tag commit: $ntfs_commit
Release archive SHA-256: $ntfs_sha256
pkgconf version: $pkgconf_version (build-time only)
pkgconf archive SHA-256: $pkgconf_sha256
Host architecture: $host_arch
Target architecture: $target_arch
Deployment target: macOS 12.0 (does not certify runtime compatibility)
Source/build directory: $ntfs_source
Build log: $build_log
Source patches: none
Configure override: ac_cv_header_libintl_h=no
Mount driver: disabled
System installation: none
EOF
clang --version >> "$output/build-manifest.txt"
printf 'Build SDK version: ' >> "$output/build-manifest.txt"
xcrun --show-sdk-version >> "$output/build-manifest.txt"
shasum -a 256 "$output/mkntfs" "$output/ntfscp" "$output/ntfscat" \
  "$output/ntfsinfo" "$output/ntfsls" > "$output/binary-checksums.txt"
if [ "$target_arch" = "$host_arch" ]; then
  "$output/mkntfs" --version
  "$output/ntfscp" --version
  "$output/ntfscat" --version
else
  echo "Cross-compiled only; execute and test these tools on $target_arch hardware."
fi
echo "Built local NTFS image tools: $output"
echo "No drive was opened, formatted, mounted, or modified by this build script."
