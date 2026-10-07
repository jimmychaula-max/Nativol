#!/bin/bash
# Compile a native read-only diagnostic library; no installation or process query.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
if [ "$#" -ne 0 ] || [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo 'Usage: build-process-identity.sh, as an ordinary macOS user' >&2
  exit 2
fi
project_root="$(cd "$(dirname "$0")/.." && pwd)"
output="$project_root/.local-engine/diagnostics"
if [ -L "$project_root/.local-engine" ] || [ -L "$output" ]; then
  echo 'Diagnostic directory cannot be a symbolic link' >&2
  exit 1
fi
mkdir -p "$output"
chmod 700 "$output"
stage="$(mktemp -d "$output/build.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
/usr/bin/clang -std=c11 -O2 -Wall -Wextra -Werror -dynamiclib \
  -mmacosx-version-min=12.0 \
  -Wl,-install_name,@rpath/libnativol-process-identity.dylib \
  "$project_root/engine/nativol-process-identity.c" \
  -o "$stage/libnativol-process-identity.dylib"
codesign --force --sign - --identifier org.nativol.dev.process-identity \
  "$stage/libnativol-process-identity.dylib"
codesign --verify --strict "$stage/libnativol-process-identity.dylib"
chmod 600 "$stage/libnativol-process-identity.dylib"
mv -f "$stage/libnativol-process-identity.dylib" "$output/libnativol-process-identity.dylib"
shasum -a 256 "$output/libnativol-process-identity.dylib" \
  "$project_root/engine/nativol-process-identity.c"
