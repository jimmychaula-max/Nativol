#!/bin/bash
# Native development reference build only. Never executes the reference, mounts,
# installs, loads a kernel extension, or requests administrator authorization.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export LC_ALL=C

backend=fskit
build_subdir=reference
binary_name=fskit-reference
compile_defines=()
if [ "$#" = 1 ] && [ "$1" = --kernel ]; then
  backend=kernel
  build_subdir=reference-kernel
  binary_name=kernel-reference
  compile_defines=(-DNATIVOL_REFERENCE_KERNEL=1)
elif [ "$#" != 0 ]; then
  echo "Usage: scripts/build-fskit-reference.sh [--kernel] (build only)" >&2
  exit 2
fi
if [ "$(uname -s)" != Darwin ] || [ "$(id -u)" = 0 ]; then
  echo "Run scripts/build-fskit-reference.sh [--kernel] as an ordinary macOS user." >&2
  exit 2
fi
project_root="$(cd "$(dirname "$0")/.." && pwd)"
engine_root="$project_root/.local-engine"
inspect_root="$project_root/.local-fuse-inspect"
sdk_root="$inspect_root/release-pkg/Core.pkg/Payload"
host_arch="$(uname -m)"
case "$host_arch" in x86_64|arm64) ;; *) echo "Unsupported host architecture." >&2; exit 1 ;; esac
if [ -L "$engine_root" ] || [ -L "$inspect_root" ] || [ -L "$engine_root/$build_subdir" ]; then
  echo "Refusing a symlink at a local development root." >&2
  exit 1
fi

# Digests measured from the official, signature-checked macFUSE 5.4.0 SDK.
(
  cd "$sdk_root"
  shasum -a 256 --check <<'SDK_CHECKSUMS'
4d3944c66c14d0a3b83267da697ec9cb64241ee9019649c24f07820ea406ad15  usr/local/include/fuse/fuse.h
eec3a2f2e5a5c503b3835a2fa5bb8fab52faaa0acbc84c694bddbc46d28c3170  usr/local/include/fuse/fuse_common.h
bbfcaff369604fc5f14eb92386f38f70c2ab7c6c9a4b28db022a386e69858a70  usr/local/include/fuse/fuse_common_compat.h
72771df4823f3f4f6a90a685f3c00c810cc87e65218d6f4287dbd1c69ca70fb5  usr/local/include/fuse/fuse_compat.h
7d67999b03d629bccde48ba4c3284a9287c55fbc8620af96fb920063bae8cfcf  usr/local/include/fuse/fuse_lowlevel.h
a84919fda51e7ea83a35f0f0adc5845bc3d53de45153f268ff8f05184baed4d5  usr/local/include/fuse/fuse_lowlevel_compat.h
ae678aebb063aba3112c9b2f62c013e7490daaf29ee4a41edf602eb6775bb563  usr/local/include/fuse/fuse_opt.h
SDK_CHECKSUMS
)
shasum -a 256 --check <<'RUNTIME_CHECKSUMS'
7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42  /usr/local/lib/libfuse.2.dylib
e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6  /Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/MFMount
25a7269c3e5ce5e7094ff3169acfb6eaf1770be882e50fd442cf38a2e825776b  /Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/Frameworks/libswiftCompatibilitySpan.dylib
RUNTIME_CHECKSUMS
codesign --verify --strict /usr/local/lib/libfuse.2.dylib
codesign --verify --deep --strict /Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework

output="$engine_root/$build_subdir/$host_arch"
source_file="$project_root/scripts/fskit-reference.c"
if [ -L "$output" ]; then echo "Refusing a symlink output directory." >&2; exit 1; fi
mkdir -p "$output"
if ! mkdir "$engine_root/$build_subdir/.build-lock" 2>/dev/null; then
  echo "Another reference build is active." >&2
  exit 1
fi
trap 'rmdir "$engine_root/$build_subdir/.build-lock"' EXIT
binary="$output/$binary_name"
if [ -L "$binary" ]; then echo "Refusing a symlink output binary." >&2; exit 1; fi
/usr/bin/clang -std=c11 -O2 -Wall -Wextra -Werror \
  -arch "$host_arch" -mmacosx-version-min=12.0 \
  -D_FILE_OFFSET_BITS=64 -D_DARWIN_USE_64_BIT_INODE=1 \
  ${compile_defines[@]+"${compile_defines[@]}"} \
  -I "$sdk_root/usr/local/include/fuse" \
  "$source_file" /usr/local/lib/libfuse.2.dylib -o "$binary"
codesign --force --sign - --identifier "org.nativol.dev.$binary_name" "$binary"
codesign --verify --strict "$binary"
lipo "$binary" -verify_arch "$host_arch"
codesign -dvv "$binary" > "$output/signature.txt" 2>&1
otool -L "$binary" > "$output/linked-libraries.txt"
shasum -a 256 "$binary" > "$output/binary-checksums.txt"
shasum -a 256 "$source_file" "$0" > "$output/source-checksums.txt"
/usr/bin/python3 -I -S - "$output" "$source_file" "$host_arch" "$backend" "$binary_name" <<'PY'
import hashlib
import json
import pathlib
import sys

output, source, architecture = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
backend, binary_name = sys.argv[4:6]
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {
    "schemaVersion": 1,
    "architecture": architecture,
    "backend": backend,
    "binarySHA256": digest(output / binary_name),
    "sourceSHA256": digest(source),
    "runtimeSHA256": {
        "libfuse.2.dylib": "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42",
        "MFMount": "e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6",
        "libswiftCompatibilitySpan.dylib": "25a7269c3e5ce5e7094ff3169acfb6eaf1770be882e50fd442cf38a2e825776b",
    },
    "purpose": "Development diagnosis only; in-memory immutable reference; no mount performed by build",
    "deploymentTarget": "12.0 (compile target, not runtime certification)",
    "macFUSEVersion": "5.4.0",
    "signing": "local ad-hoc, org.nativol.dev." + binary_name + "; not notarized",
    "flags": ["_FILE_OFFSET_BITS=64", "_DARWIN_USE_64_BIT_INODE=1"] + (["NATIVOL_REFERENCE_KERNEL=1"] if backend == "kernel" else []),
    "fixedOptions": "backend=" + backend + ",local,ro,quiet",
}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
echo "Built development reference without mounting: $binary"
cat "$output/manifest.json"
