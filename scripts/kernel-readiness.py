#!/usr/bin/env python3
"""Read-only metadata check for the pinned Intel/Sequoia macFUSE candidate.

Never loads a kernel extension, requests authorization, mounts a filesystem, or
reads private approval databases. Installed files do not establish approval.
backendLoaded means the exact expected bundle ID AND version were observed in
a successful public kmutil showloaded table, not merely that files exist.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import stat
import subprocess
import sys


VERSION = "5.4.0"
BUNDLE_ID = "io.macfuse.filesystems.macfuse.23"
TEAM_ID = "3T5GSNBU6W"
BASE = Path("/Library/Filesystems/macfuse.fs/Contents")
CANDIDATE = BASE / "Extensions/15/macfuse.kext"
RESOLVED_CANDIDATE = BASE / "Extensions/14/macfuse.kext"
RUNTIME = Path("/usr/local/lib/libfuse.2.dylib")
RUNTIME_SHA256 = "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42"
KEXT_SHA256 = "bc8bd6e656eb75dd0ebc437b73faf8fa70e7b977c092a88eacbafc1326f5fc78"
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
LOADED_ROW = re.compile(
    r"^\s*\d+\s+\d+\s+(?:0x)?[0-9a-fA-F]+\s+(?:0x)?[0-9a-fA-F]+"
    r"\s+(?:0x)?[0-9a-fA-F]+\s+(\S+)\s+\(([^)\r\n]+)\)(?:\s|$)")


class InspectionError(RuntimeError):
    pass


def command(arguments):
    """All callers supply fixed metadata-only commands, with bounded execution."""
    return subprocess.run(arguments, env=ENV, cwd="/private/tmp", stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=15)


def regular_bytes(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or not 0 < info.st_size <= maximum):
            raise InspectionError("Unexpected installed file ownership, mode, type, or size")
        data = stream.read(maximum + 1)
        if len(data) != info.st_size:
            raise InspectionError("Installed file changed during inspection")
        return data


def verify_installation():
    if CANDIDATE.resolve(strict=True) != RESOLVED_CANDIDATE:
        raise InspectionError("Unexpected Sequoia kernel-extension alias")
    for directory in (RESOLVED_CANDIDATE, *RESOLVED_CANDIDATE.parents):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise InspectionError("Kernel extension has an untrusted directory ancestor")
    package = plistlib.loads(regular_bytes(BASE / "Info.plist", 65536))
    kext = plistlib.loads(regular_bytes(RESOLVED_CANDIDATE / "Contents/Info.plist", 65536))
    if (not isinstance(package, dict) or not isinstance(kext, dict)
            or package.get("CFBundleVersion") != VERSION
            or package.get("CFBundleIdentifier") != "io.macfuse.filesystems.fs.macfuse"
            or kext.get("CFBundleVersion") != VERSION
            or kext.get("CFBundleIdentifier") != BUNDLE_ID
            or kext.get("CFBundleExecutable") != "macfuse"):
        raise InspectionError("Installed package or kernel-extension identity/version differs")
    executable = RESOLVED_CANDIDATE / "Contents/MacOS/macfuse"
    kext_hash = hashlib.sha256(regular_bytes(executable, 20 * 1024 * 1024)).hexdigest()
    runtime_hash = hashlib.sha256(regular_bytes(RUNTIME, 20 * 1024 * 1024)).hexdigest()
    if kext_hash != KEXT_SHA256 or runtime_hash != RUNTIME_SHA256:
        raise InspectionError("Installed kernel/runtime checksum differs from the reviewed macFUSE 5.4.0")
    requirement = ('anchor apple generic and certificate leaf[subject.OU] = "' + TEAM_ID
                   + '" and identifier "' + BUNDLE_ID + '"')
    signed = command(["/usr/bin/codesign", "--verify", "--strict", "-R", "=" + requirement,
                      str(RESOLVED_CANDIDATE)])
    if signed.returncode:
        raise InspectionError("Official kernel-extension signature verification failed")
    slices = command(["/usr/bin/lipo", "-archs", str(executable)])
    if slices.returncode or "x86_64" not in slices.stdout.split():
        raise InspectionError("Installed kernel extension has no verified Intel slice")
    return {"version": VERSION, "bundleIdentifier": BUNDLE_ID, "signingTeam": TEAM_ID,
            "signatureVerified": True, "architectures": slices.stdout.split(),
            "candidatePath": str(CANDIDATE), "resolvedPath": str(RESOLVED_CANDIDATE),
            "kernelSHA256": kext_hash, "runtimeSHA256": runtime_hash}


def parse_loaded_state(output):
    rows = [match.groups() for line in output.splitlines()
            if (match := LOADED_ROW.match(line)) is not None]
    if not rows:
        raise InspectionError("Public loaded-module output contained no recognizable entries")
    versions = [version for identifier, version in rows if identifier == BUNDLE_ID]
    if len(versions) > 1:
        raise InspectionError("Ambiguous loaded candidate entries")
    if not versions:
        return {"backendLoaded": False, "loadedState": "not-loaded", "loadedVersion": None}
    matches = versions[0] == VERSION
    return {"backendLoaded": matches, "loadedState": "loaded" if matches else "different-version",
            "loadedVersion": versions[0]}


def inspect_readiness():
    report = {"schemaVersion": 1, "kind": "macfuse-kernel-readiness-readonly",
              "createdAt": datetime.now(timezone.utc).isoformat(),
              "architecture": platform.machine(), "macOS": platform.mac_ver()[0],
              "candidateVersion": VERSION, "verificationPassed": False, "backendLoaded": False,
              "loadedState": "unknown", "approvalState": "unknown", "readiness": "not-ready",
              "loadAttempted": False, "mountAttempted": False, "authorizationRequested": False,
              "certifiesFilesystemSafety": False, "errors": []}
    if (sys.platform != "darwin" or report["architecture"] != "x86_64"
            or report["macOS"].split(".")[0] != "15"):
        report["errors"].append({"check": "host", "message": "Only the Intel macOS 15 candidate is covered"})
        return report
    if os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        report["errors"].append({"check": "identity", "message": "Run as an ordinary user without mixed identities"})
        return report
    for check in ("installation", "loaded-state"):
        try:
            if check == "installation":
                report["installation"] = verify_installation()
                report["verificationPassed"] = True
            else:
                loaded = command(["/usr/bin/kmutil", "showloaded", "--list-only"])
                if loaded.returncode:
                    raise InspectionError("Public loaded-module query failed (exit " + str(loaded.returncode) + ")")
                report.update(parse_loaded_state(loaded.stdout))
        except subprocess.TimeoutExpired:
            report["errors"].append({"check": check, "message": "Metadata command exceeded 15 seconds"})
        except (OSError, ValueError, plistlib.InvalidFileException, InspectionError) as error:
            # No unfiltered command output, personal paths, or unrelated module list is retained.
            message = str(error) if isinstance(error, InspectionError) else type(error).__name__
            report["errors"].append({"check": check, "message": message})
    if report["verificationPassed"] and report["backendLoaded"] and not report["errors"]:
        report["readiness"] = "verified-loaded-candidate"
    return report


def main():
    if len(sys.argv) != 1:
        print(json.dumps({"status": "refused", "error": "This read-only checker accepts no arguments"}))
        return 2
    report = inspect_readiness()
    print(json.dumps(report, indent=2))
    return 0 if report["readiness"] == "verified-loaded-candidate" else 1


if __name__ == "__main__":
    raise SystemExit(main())
