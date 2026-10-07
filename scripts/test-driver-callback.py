#!/usr/bin/env python3
"""Exercise the pinned create callback on a fresh, private regular NTFS image.

No command-line target is accepted. No device, OS mount, installation, or root
operation is supported. The deliberately synthetic fixture is retained locally.
"""
import hashlib
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile


def run(arguments, *, cwd, env, timeout=120):
    result = subprocess.run(arguments, cwd=cwd, env=env, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {arguments[0]}\n{result.stdout}")
    return result.stdout


def main():
    if len(sys.argv) != 1 or platform.system() != "Darwin" or os.geteuid() == 0:
        raise RuntimeError("Run on macOS as a normal user, with no arguments.")
    root = Path(__file__).resolve().parent.parent
    arch = platform.machine()
    if arch not in ("x86_64", "arm64"):
        raise RuntimeError("Unsupported host architecture.")
    engine = root / ".local-engine"
    manifest = engine / "driver" / arch / "build-manifest.txt"
    fields = dict(line.split(": ", 1) for line in manifest.read_text().splitlines() if ": " in line)
    source = Path(fields["Build source"])
    if not source.resolve().is_relative_to((engine / "work").resolve()):
        raise RuntimeError("Driver source is outside the local build directory.")
    driver_source = source / "src" / "ntfs-3g.c"
    if "ntfs_inode *dir_ni = NULL, *ni = NULL;" not in driver_source.read_text():
        raise RuntimeError("Expected no-plugins initialization patch is missing.")
    sdk = root / ".local-fuse-inspect" / "release-pkg" / "Core.pkg" / "Payload"
    mkntfs = engine / "bin" / arch / "mkntfs"
    if not mkntfs.is_file():
        raise RuntimeError("Build the native image tools first.")
    base = engine / "callback-tests"
    if base.is_symlink():
        raise RuntimeError("Refusing a symlink at callback-tests.")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="callback-", dir=base))
    image = work / "fixture.ntfs"
    descriptor = os.open(image, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.ftruncate(descriptor, 64 * 1024 * 1024)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise RuntimeError("Fixture is not a private owned regular file.")
    finally:
        os.close(descriptor)
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    binary = work / "callback-test"
    command = ["/usr/bin/clang", "-O1", "-g", "-std=gnu23", "-DFUSE_USE_VERSION=26",
               "-D_FILE_OFFSET_BITS=64", "-D_DARWIN_USE_64_BIT_INODE=1", "-DHAVE_CONFIG_H",
               "-Werror=sometimes-uninitialized", "-Werror=incompatible-pointer-types",
               f'-DNATIVOL_DRIVER_SOURCE="{driver_source}"',
               "-I", str(source), "-I", str(source / "include" / "ntfs-3g"),
               "-I", str(sdk / "usr/local/include/fuse"),
               str(root / "scripts/test-driver-callback.c"),
               str(source / "src/ntfs_3g-ntfs-3g_common.o"),
               str(source / "libntfs-3g/.libs/libntfs-3g.a"),
               str(sdk / "usr/local/lib/libfuse.2.dylib"),
               "-framework", "CoreFoundation", "-o", str(binary)]
    try:
        (work / "compile.log").write_text(run(command, cwd=work, env=environment))
        (work / "format.log").write_text(run([str(mkntfs), "-F", "-Q", "-L", "NATIVOL_CALLBACK",
                                               str(image)], cwd=work, env=environment))
        environment["DYLD_LIBRARY_PATH"] = str(sdk / "usr/local/lib")
        environment["DYLD_FRAMEWORK_PATH"] = str(sdk / "Library/Filesystems/macfuse.fs/Contents/Frameworks")
        result = run([str(binary)], cwd=work, env=environment)
        report = (result + f"Host architecture: {arch}\nSource: {source}\n"
                  + "Fixture: synthetic reparse flag, not a complete Windows junction\n"
                  + "FUSE context: deterministic local test context; no FSKit transport tested\n"
                  + f"C harness SHA-256: {hashlib.sha256((root / 'scripts/test-driver-callback.c').read_bytes()).hexdigest()}\n")
        (work / "result.txt").write_text(report)
        print(report, end="")
        print(f"Report: {work / 'result.txt'}")
    except Exception as error:
        (work / "failure.txt").write_text(str(error))
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
