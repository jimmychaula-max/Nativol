#!/usr/bin/env python3
"""Compile/test inherited descriptor I/O using a fresh private regular file only."""
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) != 1 or os.getuid() == 0 or platform.system() != "Darwin":
        raise RuntimeError("Requires ordinary macOS user, no arguments")
    arch = platform.machine()
    manifest = ROOT / ".local-engine/driver" / arch / "build-manifest.txt"
    source = Path(next(line[14:] for line in manifest.read_text().splitlines()
                       if line.startswith("Build source: ")))
    source.relative_to(ROOT / ".local-engine/work")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    with tempfile.TemporaryDirectory(prefix="nativol-fd-permissions-") as tmp:
        binary = Path(tmp) / "fd-test"
        subprocess.run(["/usr/bin/clang", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-arch", arch, "-mmacosx-version-min=12.0", "-DHAVE_CONFIG_H",
                        "-D_FILE_OFFSET_BITS=64", "-D_DARWIN_USE_64_BIT_INODE=1",
                        "-I", str(source), "-I", str(source / "include/ntfs-3g"),
                        str(ROOT / "scripts/test-driver-fd.c"),
                        str(source / "libntfs-3g/.libs/libntfs-3g.a"),
                        "-framework", "CoreFoundation", "-o", str(binary)],
                       check=True, cwd=tmp, env=env, timeout=60)
        subprocess.run([str(binary)], check=True, cwd=tmp, env=env, timeout=20)


if __name__ == "__main__":
    main()
