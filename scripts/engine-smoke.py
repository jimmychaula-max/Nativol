#!/usr/bin/env python3
"""Exercise the NTFS engine on a NEW, UNMOUNTED, disposable regular file only.

There is intentionally no option for an image/device/volume target. Never run as root.
This does not mount a filesystem or certify an external-drive backend.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-prefix", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        parser.error("This test must run as an ordinary user, never as root.")

    try:
        prefix = args.engine_prefix.resolve(strict=True)
    except OSError:
        parser.error("The engine prefix does not exist or is not accessible.")
    if not prefix.is_dir():
        parser.error("The engine prefix must be a directory.")
    tools = {}
    for name in ["mkntfs", "ntfscp", "ntfscat", "ntfsinfo"]:
        candidates = [prefix / folder / name for folder in ["bin", "sbin", "."]]
        found = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
        if found is None:
            parser.error("Missing built tool: " + name)
        path = found.resolve(strict=True)
        if prefix not in path.parents or path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
            parser.error("Tool must be a non-setuid executable within the supplied prefix.")
        tools[name] = path

    report = {
        "schemaVersion": 1,
        "kind": "unmounted-ntfs-image",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "architecture": platform.machine(),
        "macOS": platform.mac_ver()[0],
        "status": "failed",
        "physicalDevicesAccessed": False,
        "mountedFilesystemTested": False,
        "checks": [],
        "toolSHA256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in tools.items()},
    }
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
    try:
        with tempfile.TemporaryDirectory(prefix="nativol-image-lab-") as directory:
            work = Path(directory).resolve()
            os.chmod(work, 0o700)
            image = work / "test-volume.ntfs"
            descriptor = os.open(image, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.ftruncate(descriptor, 256 * 1024 * 1024)
                identity = os.fstat(descriptor)
            finally:
                os.close(descriptor)

            def verify_image():
                current = image.lstat()
                if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                        or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
                        or current.st_uid != os.getuid() or image.parent != work):
                    raise RuntimeError("Disposable image identity changed; operation refused.")

            def run(tool, *arguments, success=True):
                verify_image()
                result = subprocess.run([str(tools[tool]), *map(str, arguments)],
                                        cwd=work, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                if success and result.returncode != 0:
                    # Tool stderr may contain temporary paths; keep the persisted report generic.
                    raise RuntimeError("%s failed with exit %s" % (tool, result.returncode))
                return result

            # -F is needed because this target is a regular file, never a device.
            run("mkntfs", "-F", "-Q", "-L", "NATIVOL_IMAGE", image)
            report["checks"].append({"name": "Create disposable 256 MiB NTFS image", "passed": True})
            version = run("mkntfs", "--version")
            version_lines = [line.strip() for line in (version.stdout + version.stderr)
                             .decode("utf-8", errors="replace").splitlines() if line.strip()]
            if not version_lines:
                raise RuntimeError("Engine did not report its version")
            report["engineVersion"] = version_lines[0][:180]

            def verify_payload(label, target, payload):
                source = work / "input.bin"
                source.write_bytes(payload)
                run("ntfscp", image, source, target)
                restored = run("ntfscat", image, target).stdout
                expected = hashlib.sha256(payload).hexdigest()
                actual = hashlib.sha256(restored).hexdigest()
                if len(restored) != len(payload) or actual != expected:
                    raise RuntimeError("Readback mismatch: " + label)
                report["checks"].append({"name": label, "passed": True, "bytes": len(payload), "sha256": expected})

            verify_payload("Empty file round trip", "/empty.bin", b"")
            verify_payload("UTF-8 text round trip", "/hello.txt", "Nativol — NTFS image test.\n".encode("utf-8"))
            block = bytes(range(256))
            verify_payload("Unaligned binary round trip", "/unaligned.bin", block * 1024 + b"end")
            verify_payload("8 MiB binary round trip", "/large.bin", block * 32768)
            verify_payload("Unicode filename round trip", "/Safari_\u6771\u4eac.txt", b"Unicode path fixture\n")
            verify_payload("Initial overwrite fixture", "/replace.bin", block * 256)
            verify_payload("Shorter overwrite truncates correctly", "/replace.bin", b"replacement\n")

            run("ntfsinfo", "-m", image)
            report["checks"].append({"name": "Read NTFS volume metadata", "passed": True})
            invalid = work / "not-ntfs.img"
            invalid.write_bytes(b"\0" * 1024 * 1024)
            rejected = run("ntfscat", invalid, "/missing", success=False)
            if rejected.returncode == 0:
                raise RuntimeError("Invalid NTFS fixture was not rejected")
            report["checks"].append({"name": "Reject invalid filesystem image", "passed": True})
            report["status"] = "passed"
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = type(error).__name__ + ": " + str(error).split("\n")[0][:220]
    finally:
        report["completedChecks"] = len(report["checks"])
        encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        print(encoded, end="")
        if args.report:
            try:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                # Replace only the requested report, without following its final symlink.
                fd, temporary = tempfile.mkstemp(prefix=".nativol-report-", dir=args.report.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(encoded)
                    os.replace(temporary, args.report)
                finally:
                    if os.path.lexists(temporary): os.unlink(temporary)
            except OSError:
                print("The test report could not be saved; results are printed above.", file=sys.stderr)
                return 1
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
