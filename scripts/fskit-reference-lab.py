#!/usr/bin/env python3
"""Ordinary-user, in-memory FSKit reference diagnostic. Accepts no arguments.

Only a newly generated mountpoint and nonce are passed to the reviewed reference
binary. No NTFS engine, backing image, physical-device target, writable mount,
or mounted write attempt is involved. Unknown mounts are retained for review.
"""
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import time
import uuid

MNT_RDONLY, MNT_EXT_FSKIT, MNT_NOWAIT = 1, 2, 2
ALLOWED_TYPES = {"macfuse-local", "macfuse"}
BACKEND = "fskit"
BUILD_SUBDIR = "reference"
BINARY_NAME = "fskit-reference"
MOUNT_PREFIX = "Nativol-Reference-"
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
ROOT = Path(__file__).resolve().parent.parent
RUNTIME_HASHES = {
    Path("/usr/local/lib/libfuse.2.dylib"): "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42",
    Path("/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/MFMount"):
        "e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6",
    Path("/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/Frameworks/libswiftCompatibilitySpan.dylib"):
        "25a7269c3e5ce5e7094ff3169acfb6eaf1770be882e50fd442cf38a2e825776b",
}
INSPECTION_WORKER_STILL_RUNNING = False


class LabError(RuntimeError):
    pass


class DarwinStatFS(ctypes.Structure):
    _fields_ = [("bsize", ctypes.c_uint32), ("iosize", ctypes.c_int32),
                ("blocks", ctypes.c_uint64), ("bfree", ctypes.c_uint64),
                ("bavail", ctypes.c_uint64), ("files", ctypes.c_uint64), ("ffree", ctypes.c_uint64),
                ("fsid", ctypes.c_int32 * 2), ("owner", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32),
                ("fstypename", ctypes.c_char * 16), ("mntonname", ctypes.c_char * 1024),
                ("mntfromname", ctypes.c_char * 1024), ("flags_ext", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 7)]


def snapshot(item):
    return {"source": bytes(item.mntfromname).decode("utf-8"),
            "mountPoint": bytes(item.mntonname).decode("utf-8"),
            "filesystemType": bytes(item.fstypename).decode("utf-8"),
            "flags": item.flags, "extendedFlags": item.flags_ext, "ownerUID": item.owner,
            "filesystemID": list(item.fsid)}


def mount_table():
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.getfsstat64
    function.argtypes = [ctypes.POINTER(DarwinStatFS), ctypes.c_int, ctypes.c_int]
    function.restype = ctypes.c_int
    count = function(None, 0, MNT_NOWAIT)
    if count < 0:
        raise LabError("Cannot count kernel mounts")
    entries = (DarwinStatFS * (count + 32))()
    actual = function(entries, ctypes.sizeof(entries), MNT_NOWAIT)
    if actual < 0 or actual >= len(entries):
        raise LabError("Cannot read a complete kernel mount snapshot")
    return [snapshot(entries[index]) for index in range(actual)]


def descriptor_mount(descriptor):
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.fstatfs64
    function.argtypes = [ctypes.c_int, ctypes.POINTER(DarwinStatFS)]
    function.restype = ctypes.c_int
    result = DarwinStatFS()
    if function(descriptor, ctypes.byref(result)):
        raise LabError("Cannot verify descriptor filesystem")
    return snapshot(result)


def digest(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()


def validate_invocation():
    if len(sys.argv) != 1:
        raise LabError("This diagnostic accepts no targets, options, or other arguments")
    if sys.platform != "darwin" or os.getuid() == 0 or os.getuid() != os.geteuid():
        raise LabError("Run only as the ordinary logged-in macOS user")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise LabError("Invoke with /usr/bin/python3 -I -S")
    architecture = platform.machine()
    if architecture not in {"x86_64", "arm64"}:
        raise LabError("Unsupported architecture")
    directory = ROOT / ".local-engine" / BUILD_SUBDIR / architecture
    binary = directory / BINARY_NAME
    manifest = directory / "manifest.json"
    for path in (binary, manifest):
        info = path.lstat()
        if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)):
            raise LabError("Reference build must contain owned, unprivileged regular files")
    if manifest.stat().st_size > 65536 or not os.access(binary, os.X_OK):
        raise LabError("Invalid local reference build")
    record = json.loads(manifest.read_text())
    if not isinstance(record, dict):
        raise LabError("Reference build manifest must be a JSON object")
    actual = digest(binary)
    if (record.get("schemaVersion") != 1 or record.get("architecture") != architecture
            or record.get("backend") != BACKEND
            or record.get("binarySHA256") != actual
            or record.get("sourceSHA256") != digest(ROOT / "scripts/fskit-reference.c")):
        raise LabError("Reference binary/source differs from the local build record")
    for path, expected in RUNTIME_HASHES.items():
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022 or digest(path) != expected:
            raise LabError("Installed runtime differs from pinned macFUSE 5.4.0")
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(binary)],
                            env=ENV, capture_output=True, timeout=15)
    if result.returncode:
        raise LabError("Reference executable signature verification failed")
    return binary, record


def validate_identity(entry, mountpoint):
    backend_matches = (bool(entry["extendedFlags"] & MNT_EXT_FSKIT) if BACKEND == "fskit"
                       else BACKEND == "kernel" and not entry["extendedFlags"] & MNT_EXT_FSKIT)
    if (entry["mountPoint"] != str(mountpoint) or entry["filesystemType"] not in ALLOWED_TYPES
            or not backend_matches or entry["ownerUID"] != os.getuid()
            or entry["filesystemID"] == [0, 0]):
        raise LabError("Candidate is not the expected user-owned macFUSE " + BACKEND + " mount")


def inspect_fixture(mountpoint, fixture_name, expected, candidate):
    descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        entry = descriptor_mount(descriptor)
        validate_identity(entry, mountpoint)
        if entry != candidate:
            raise LabError("Mount descriptor differs from the kernel snapshot")
        fixture = os.open(fixture_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(fixture, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size != len(expected)
                    or descriptor_mount(stream.fileno()) != entry):
                raise LabError("Fixture is not a regular file in the candidate filesystem")
            restored = stream.read(len(expected) + 1)
        if restored != expected or descriptor_mount(descriptor) != entry:
            raise LabError("Nonce fixture or filesystem identity changed")
        return {"verifiedMount": entry, "fixtureSHA256": hashlib.sha256(restored).hexdigest(),
                "effectiveReadOnly": bool(entry["flags"] & MNT_RDONLY)}
    finally:
        os.close(descriptor)


def inspect_worker(sender, mountpoint, fixture_name, expected, candidate):
    try:
        sender.send(inspect_fixture(mountpoint, fixture_name, expected, candidate))
    except (OSError, LabError) as error:
        sender.send({"error": type(error).__name__ + ": " + str(error)[:500]})
    finally:
        sender.close()


class ReferenceLab:
    def __init__(self, binary, report):
        self.binary, self.report = binary, report
        self.work = Path(tempfile.mkdtemp(prefix="nativol-reference-ro-")).resolve()
        os.chmod(self.work, 0o700)
        self.nonce = os.urandom(32).hex()
        self.fixture_name = "fixture-" + self.nonce + ".txt"
        self.expected = ("Nativol FSKit reference\n" + self.nonce + "\n").encode()
        self.mountpoint = Path("/Volumes") / (MOUNT_PREFIX + uuid.uuid4().hex)
        self.server = self.server_log = self.verified_mount = None
        report.update({"workDirectory": str(self.work), "mountPoint": str(self.mountpoint),
                       "cleanupRequired": False, "checks": []})

    def entry(self):
        entries = [item for item in mount_table() if item["mountPoint"] == str(self.mountpoint)]
        if len(entries) > 1:
            raise LabError("Ambiguous generated mountpoint")
        return entries[0] if entries else None

    def mount(self):
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint is occupied")
        self.server_log = (self.work / "reference.log").open("xb")
        self.server = subprocess.Popen([str(self.binary), "--nonce", self.nonce,
                                        "--mountpoint", str(self.mountpoint)], env=ENV, cwd=self.work,
                                       stdin=subprocess.DEVNULL, stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"serverPID": self.server.pid, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            entry = self.entry()
            if entry is not None:
                self.report["observedMount"] = entry
                validate_identity(entry, self.mountpoint)
                if BACKEND == "kernel" and entry["source"] != MOUNT_PREFIX + self.nonce:
                    raise LabError("Kernel reference source does not match this nonce")
                if self.server.poll() is not None:
                    raise LabError("Candidate has no live owned server")
                return entry
            code = self.server.poll()
            if code is not None:
                self.report["serverExitCode"] = code
                raise LabError("Reference server exited without an OS mount, even if its exit status was zero")
            time.sleep(0.1)
        raise LabError("No candidate mount within 30 seconds")

    def inspect(self, candidate):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=inspect_worker,
                                 args=(sender, self.mountpoint, self.fixture_name, self.expected, candidate))
        try:
            worker.start()
            sender.close()
            self.report["inspectionWorkerPID"] = worker.pid
            worker.join(timeout=20)
            if worker.is_alive():
                raise LabError("Read-only fixture inspection exceeded 20 seconds")
            if worker.exitcode or not receiver.poll():
                raise LabError("Inspection worker exited without evidence")
            result = receiver.recv()
            if "error" in result:
                raise LabError(result["error"])
            if self.server.poll() is not None or self.entry() != result["verifiedMount"]:
                raise LabError("Owned server or mounted identity changed after inspection")
            self.verified_mount = result["verifiedMount"]
            self.report.update(result)
            self.report["ownershipVerified"] = True
            self.report["checks"].append("Live owned server, descriptor FSID and in-memory nonce match")
            if not result["effectiveReadOnly"]:
                raise LabError("Ownership verified, but effective MNT_RDONLY is missing")
            self.report["checks"].append("Kernel reports read-only; no mounted write attempt performed")
        finally:
            sender.close()
            receiver.close()
            if worker.pid is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
                self.report["inspectionWorkerStillRunning"] = worker.is_alive()

    def cleanup(self):
        try:
            entry = self.entry()
            vanished = entry is None and self.verified_mount is not None
            clean_unmount = False
            if entry is not None:
                self.report["observedMountAtCleanup"] = entry
                if (self.verified_mount is None or entry != self.verified_mount
                        or self.server is None or self.server.poll() is not None):
                    raise LabError("Unverified/changed mount retained; server was not terminated")
                validate_identity(entry, self.mountpoint)
                result = subprocess.run(["/usr/sbin/diskutil", "unmount", str(self.mountpoint)], env=ENV,
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                self.report["unmountOutput"] = (result.stdout + result.stderr)[-4096:].decode("utf-8", "replace")
                if result.returncode or self.entry() is not None:
                    raise LabError("Normal unmount failed; no force or server termination attempted")
                clean_unmount = True
            if self.server is not None and self.server.poll() is None:
                if self.entry() is not None:
                    raise LabError("Mount appeared before shutdown; server retained")
                self.server.terminate()
                self.server.wait(timeout=10)
            if self.entry() is not None:
                raise LabError("Mount appeared during shutdown; retained for review")
            self.report.update({"cleanupRequired": False, "cleanUnmount": clean_unmount})
            if vanished:
                raise LabError("Verified mount vanished before explicit normal unmount")
            if clean_unmount:
                self.report["checks"].append("Verified filesystem normally unmounted")
            return True
        except (OSError, LabError, subprocess.SubprocessError) as error:
            self.report["cleanupError"] = str(error)[:500]
            return False
        finally:
            if self.server_log is not None:
                self.server_log.close()

    def log_tail(self):
        path = self.work / "reference.log"
        if path.exists():
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - 8192))
                self.report["serverLogTail"] = stream.read(8192).decode("utf-8", "replace")


def main():
    global INSPECTION_WORKER_STILL_RUNNING
    INSPECTION_WORKER_STILL_RUNNING = False
    report = {"schemaVersion": 1, "kind": BACKEND + "-in-memory-reference-ro-diagnostic",
              "backend": BACKEND,
              "status": "failed", "createdAt": datetime.now(timezone.utc).isoformat(),
              "physicalDeviceTargetAccepted": False, "backingImageUsed": False,
              "ntfsEngineUsed": False, "writableMountRequested": False,
              "mountedWriteAttemptPerformed": False, "certifiesFilesystemSafety": False,
              "architecture": platform.machine(), "macOS": platform.mac_ver()[0]}
    lab = None
    try:
        binary, record = validate_invocation()
        os.umask(0o077)
        report.update({"effectiveUID": os.geteuid(), "build": record})
        lab = ReferenceLab(binary, report)
        candidate = lab.mount()
        lab.inspect(candidate)
        report["inspectionPassed"] = True
    except (OSError, LabError, ValueError, subprocess.SubprocessError, KeyboardInterrupt, EOFError) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            clean = lab.cleanup()
            if clean and report.get("inspectionPassed") and report.get("cleanUnmount"):
                report["status"] = "passed"
            try:
                lab.log_tail()
            except OSError as error:
                report["logReadError"] = str(error)[:300]
        report["completedAt"] = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(report, indent=2) + "\n"
        if lab is not None:
            try:
                with (lab.work / "report.json").open("x", encoding="utf-8") as stream:
                    stream.write(encoded)
            except OSError as error:
                report.update({"reportSaveError": str(error)[:300], "status": "failed"})
                encoded = json.dumps(report, indent=2) + "\n"
        print(encoded, end="")
        INSPECTION_WORKER_STILL_RUNNING = bool(report.get("inspectionWorkerStillRunning"))
    return 0 if report["status"] == "passed" else 1


def finish_supervisor(exit_code, stuck_worker):
    sys.stdout.flush()
    sys.stderr.flush()
    if stuck_worker:
        os._exit(1)
        return
    raise SystemExit(exit_code)


if __name__ == "__main__":
    finish_supervisor(main(), INSPECTION_WORKER_STILL_RUNNING)
