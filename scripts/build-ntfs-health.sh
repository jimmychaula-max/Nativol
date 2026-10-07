#!/bin/bash
# Compile only. The inspector accepts an inherited descriptor; this never runs it.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
project_root="$(cd "$(dirname "$0")/.." && pwd)"
target_arch="${1:-$(uname -m)}"
if [ "$#" -gt 1 ] || [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo 'Usage: build-ntfs-health.sh [x86_64], as an ordinary macOS user' >&2; exit 2
fi
case "$target_arch" in x86_64) ;; *) exit 2 ;; esac
driver_root="$project_root/.local-engine/driver/$target_arch"
source_root="$(sed -n 's/^Build source: //p' "$driver_root/build-manifest.txt")"
case "$source_root" in "$project_root/.local-engine/work/driver-$target_arch."*) ;; *) echo 'Unexpected build source' >&2; exit 1 ;; esac
output="$project_root/.local-engine/health/$target_arch"
mkdir -p "$output"
/usr/bin/clang -O2 -Wall -Wextra -Werror -arch "$target_arch" -mmacosx-version-min=12.0 \
  -ffile-prefix-map="$project_root"=. -fdebug-prefix-map="$project_root"=. \
  -D_FILE_OFFSET_BITS=64 -D_DARWIN_USE_64_BIT_INODE=1 -DHAVE_CONFIG_H \
  -I"$source_root" -I"$source_root/include/ntfs-3g" \
  "$project_root/engine/nativol-ntfs-health.c" "$source_root/libntfs-3g/.libs/libntfs-3g.a" \
  -framework CoreFoundation -o "$output/nativol-ntfs-health"
/usr/bin/strip -S "$output/nativol-ntfs-health"
codesign --force --sign - --identifier org.nativol.dev.ntfs-health "$output/nativol-ntfs-health"
codesign --verify --strict "$output/nativol-ntfs-health"
lipo "$output/nativol-ntfs-health" -verify_arch "$target_arch"
otool -L "$output/nativol-ntfs-health" > "$output/linked-libraries.txt"
shasum -a 256 "$output/nativol-ntfs-health" > "$output/binary-checksums.txt"
shasum -a 256 "$project_root/engine/nativol-ntfs-health.c" > "$output/source-checksums.txt"
cp "$source_root/COPYING" "$output/NTFS-3G-COPYING"
echo "Built inspector: $output/nativol-ntfs-health (not executed)"
