#!/usr/bin/env python3
"""One fresh-image, read-only FSKit administrator diagnostic; no arguments.

Review and copy this file plus pinned mkntfs, ntfscp, ntfs-3g and libfuse.2.dylib
into root:wheel 0700 /private/tmp/nativol-admin-ro.XXXXXX/bin (bundle also 0700).
Files must be root-owned single-link regular files; tools/library mode 0755.
Invoke with an empty environment: /usr/bin/python3 -I -S <staged-script>.
The caller must separately verify the staged script's reviewed SHA-256.
Never invoke this script through a setuid wrapper. No existing image or device
can be supplied. All images/logs are retained. A pass is diagnostic evidence,
not filesystem certification; this script never attempts a mounted write.
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
import time
import uuid


IMAGE_BYTES = 256 * 1024 * 1024
MNT_RDONLY, MNT_EXT_FSKIT, MNT_NOWAIT = 1, 2, 2
ALLOWED_TYPES = {"macfuse-local", "macfuse"}
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
INSPECTION_WORKER_STILL_RUNNING = False
TOOL_HASHES = {
    "x86_64": {
        "mkntfs": "9af42ef92fad65a533dc8a5cbfc802941d7d17baffd71c0bf13c84ad327c388f",
        "ntfscp": "0522188e9cdb137ea63ea074aaa164b90122b4a13546a826719ab92039123b66",
        "ntfs-3g": "7e10c1cc3a750b8d5deece7c8633303f1a1af65b1f2d499152db429f46ab7f43",
    },
    "arm64": {
        "mkntfs": "3e22d9f942c5b0eadc36e2657403cabba62acd72005a1bdf8b2963e1ec3ae7c8",
        "ntfscp": "df8d77210dd80c3842d5f34c795bf7a29e59eee947c3a300f9294bd3d32c7499",
        "ntfs-3g": "ade858faf96d1058352d3f6d9dab80dbf60b43f0d97b4deda12d08e5f7739db3",
    },
}
FUSE_HASH = "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42"
FRAMEWORK_ROOT = Path("/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A")
PROTECTED_RUNTIME = {
    FRAMEWORK_ROOT / "MFMount": "e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6",
    FRAMEWORK_ROOT / "Frameworks/libswiftCompatibilitySpan.dylib":
        "25a7269c3e5ce5e7094ff3169acfb6eaf1770be882e50fd442cf38a2e825776b",
}


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


def private_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise LabError("Staging directories must be root-owned mode 0700")


def protected_ancestors(path):
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise LabError("Runtime has an untrusted or writable ancestor: " + str(parent))


def verified_file(path, expected=None, executable=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
            or (executable and stat.S_IMODE(info.st_mode) != 0o755)):
        raise LabError("Staged/runtime file permissions or ownership are unsafe: " + path.name)
    actual = digest(path)
    after = path.lstat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise LabError("File changed during verification: " + path.name)
    if expected is not None and actual != expected:
        raise LabError("Pinned checksum mismatch: " + path.name)
    return actual


def validate_invocation():
    if len(sys.argv) != 1:
        raise LabError("This diagnostic accepts no arguments, device paths, images, or options")
    if sys.platform != "darwin" or os.getuid() != 0 or os.geteuid() != 0:
        raise LabError("Requires a genuine root process on macOS; no setuid invocation")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise LabError("Invoke reviewed Python with -I -S under env -i")
    script = Path(__file__).absolute()
    directory, bundle = script.parent, script.parent.parent
    if (directory.name != "bin" or bundle.parent != Path("/private/tmp")
            or not bundle.name.startswith("nativol-admin-ro.") or len(bundle.name) < 22
            or script.resolve(strict=True) != script):
        raise LabError("Requires canonical /private/tmp/nativol-admin-ro.XXXXXX/bin staging")
    temporary = Path("/private/tmp").lstat()
    if temporary.st_uid != 0 or not stat.S_ISDIR(temporary.st_mode) or not temporary.st_mode & stat.S_ISVTX:
        raise LabError("The shared temporary ancestor must be root-owned and sticky")
    private_directory(bundle)
    private_directory(directory)
    verified_file(script)
    # Keep MFMount's @executable_path/../Frameworks search inside this root-only
    # bundle instead of letting it search a directory under shared /private/tmp.
    frameworks = bundle / "Frameworks"
    if os.path.lexists(frameworks):
        private_directory(frameworks)
        if any(frameworks.iterdir()):
            raise LabError("Unexpected private framework overrides")
    architecture = platform.machine()
    if architecture not in TOOL_HASHES:
        raise LabError("Unsupported architecture")
    tools = {name: directory / name for name in TOOL_HASHES[architecture]}
    checksums = {name: verified_file(path, TOOL_HASHES[architecture][name], True)
                 for name, path in tools.items()}
    checksums["libfuse.2.dylib"] = verified_file(directory / "libfuse.2.dylib", FUSE_HASH, True)
    for path, expected in PROTECTED_RUNTIME.items():
        protected_ancestors(path)
        verified_file(path, expected, True)
    protected_ancestors(Path("/Volumes") / "unused")
    return bundle, tools, checksums, dict(ENV, DYLD_LIBRARY_PATH=str(directory))


def validate_identity(entry, mountpoint):
    """Capability (RO) is deliberately separate from ownership evidence."""
    if (entry["mountPoint"] != str(mountpoint) or entry["filesystemType"] not in ALLOWED_TYPES
            or not entry["extendedFlags"] & MNT_EXT_FSKIT or entry["ownerUID"] != 0
            or entry["filesystemID"] == [0, 0]):
        raise LabError("Mount is not the expected root-owned macFUSE FSKit candidate")


def inspect_fixture(mountpoint, fixture_name, expected, candidate):
    descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        entry = descriptor_mount(descriptor)
        validate_identity(entry, mountpoint)
        if entry != candidate:
            raise LabError("Mount descriptor identity differs from kernel snapshot")
        fixture = os.open(fixture_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(fixture, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size != len(expected)
                    or descriptor_mount(stream.fileno()) != entry):
                raise LabError("Fixture is not a regular file in the candidate filesystem")
            restored = stream.read(len(expected) + 1)
        if hashlib.sha256(restored).digest() != hashlib.sha256(expected).digest():
            raise LabError("Unpredictable fixture does not match this fresh image")
        if descriptor_mount(descriptor) != entry:
            raise LabError("Filesystem changed during fixture readback")
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


class AdminImageLab:
    def __init__(self, bundle, tools, environment, report):
        self.tools, self.environment, self.report = tools, environment, report
        self.work = bundle / ("run-" + uuid.uuid4().hex)
        self.work.mkdir(mode=0o700)
        self.image = self.work / "disposable.ntfs"
        self.fixture_name = "nonce-" + uuid.uuid4().hex + ".bin"
        self.mountpoint = Path("/Volumes") / ("Nativol-Admin-RO-" + uuid.uuid4().hex)
        self.server = self.server_log = self.image_identity = self.verified_mount = self.original_hash = None
        self.report.update({"workDirectory": str(self.work), "imagePath": str(self.image),
                            "mountPoint": str(self.mountpoint), "imageRetained": True,
                            "cleanupRequired": False, "checks": []})

    def entry(self):
        entries = [item for item in mount_table() if item["mountPoint"] == str(self.mountpoint)]
        if len(entries) > 1:
            raise LabError("Ambiguous generated mountpoint")
        return entries[0] if entries else None

    def verify_image(self):
        info = self.image.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != IMAGE_BYTES
                or (info.st_dev, info.st_ino) != self.image_identity):
            raise LabError("Fresh private image identity changed")

    def run_tool(self, name, *args):
        self.verify_image()
        with (self.work / (name + ".log")).open("xb") as log:
            result = subprocess.run([str(self.tools[name]), *map(str, args)], cwd=self.work,
                                    env=self.environment, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=60)
        if result.returncode:
            raise LabError(name + " failed; exit " + str(result.returncode))
        self.verify_image()

    def prepare(self):
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint is already occupied")
        descriptor = os.open(self.image, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(descriptor, IMAGE_BYTES)
            info = os.fstat(descriptor)
            self.image_identity = (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)
        self.run_tool("mkntfs", "-F", "-Q", "-L", "NATIVOL_ADMIN_RO", self.image)
        payload = os.urandom(64) + bytes(range(256)) * 4096
        fixture = self.work / self.fixture_name
        with fixture.open("xb") as stream:
            stream.write(payload)
        self.run_tool("ntfscp", self.image, fixture, "/" + self.fixture_name)
        self.original_hash = digest(self.image)
        self.report["imageSHA256BeforeMount"] = self.original_hash
        self.report["checks"].append("Created a fresh private regular image and unpredictable fixture")
        return payload

    def mount(self):
        self.verify_image()
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint became occupied")
        options = "ro,norecover,no_def_opts,no_detach,backend=fskit,quiet,uid=0,gid=0,umask=077,volname=" + self.mountpoint.name
        self.server_log = (self.work / "driver.log").open("xb")
        self.server = subprocess.Popen([str(self.tools["ntfs-3g"]), str(self.image), str(self.mountpoint),
                                        "-o", options], env=self.environment, cwd=self.work,
                                       stdin=subprocess.DEVNULL, stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"driverPID": self.server.pid, "requestedOptions": options, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            entry = self.entry()
            if entry is not None:
                self.report["observedMount"] = entry
                validate_identity(entry, self.mountpoint)
                if self.server.poll() is not None:
                    raise LabError("Candidate mount has no live owned driver process")
                return entry
            code = self.server.poll()
            if code is not None:
                self.report["driverExitCode"] = code
                raise LabError("Driver exited without a mount, even if its exit status was zero")
            time.sleep(0.1)
        raise LabError("No candidate mount within 30 seconds")

    def inspect(self, expected, candidate):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=inspect_worker,
                                 args=(sender, self.mountpoint, self.fixture_name, expected, candidate))
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
                raise LabError("Owned server or mounted identity changed after fixture inspection")
            self.verified_mount = result["verifiedMount"]
            self.report.update(result)
            self.report["ownershipVerified"] = True
            self.report["checks"].append("Live owned server, unique FSKit mount, descriptor FSID and nonce SHA-256 match")
            if not result["effectiveReadOnly"]:
                raise LabError("Ownership verified, but effective MNT_RDONLY is missing; diagnostic failed")
            self.report["checks"].append("Kernel reports effective MNT_RDONLY; mounted write attempts were not performed")
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
                    raise LabError("Unverified/changed mount retained; driver was not terminated")
                validate_identity(entry, self.mountpoint)
                # Ownership alone authorizes cleanup. A failed RO flag check must
                # still permit normal unmount of this proven disposable filesystem.
                result = subprocess.run(["/usr/sbin/diskutil", "unmount", str(self.mountpoint)], env=ENV,
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                self.report["unmountOutput"] = (result.stdout + result.stderr)[-4096:].decode("utf-8", "replace")
                if result.returncode or self.entry() is not None:
                    raise LabError("Normal unmount failed; no force or driver termination attempted")
                clean_unmount = True
            if self.server is not None and self.server.poll() is None:
                # Recheck immediately before signaling only our own child process.
                if self.entry() is not None:
                    raise LabError("Mount appeared before driver shutdown; server retained")
                self.server.terminate()
                self.server.wait(timeout=10)
            if self.entry() is not None:
                raise LabError("Mount appeared during shutdown; retain artifacts for review")
            self.report.update({"cleanupRequired": False, "cleanUnmount": clean_unmount})
            if self.image_identity is not None:
                self.verify_image()
                after = digest(self.image)
                self.report["imageSHA256AfterUnmount"] = after
                if self.original_hash is not None and after != self.original_hash:
                    raise LabError("The read-only diagnostic changed the private image bytes")
            if vanished:
                raise LabError("Verified mount vanished before explicit normal unmount")
            if clean_unmount:
                self.report["checks"].append("Normal unmount completed and image SHA-256 is unchanged")
            return True
        except (OSError, LabError, subprocess.SubprocessError) as error:
            self.report["cleanupError"] = str(error)[:500]
            # Keep known absence separate from lifecycle/hash validation failure.
            # Errors before absence was established retain cleanupRequired=True.
            return False
        finally:
            if self.server_log is not None:
                self.server_log.close()

    def log_tails(self):
        for name in ("mkntfs", "ntfscp", "driver"):
            path = self.work / (name + ".log")
            if path.exists():
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    stream.seek(max(0, os.fstat(stream.fileno()).st_size - 8192))
                    self.report[name + "LogTail"] = stream.read(8192).decode("utf-8", "replace")


def main():
    global INSPECTION_WORKER_STILL_RUNNING
    INSPECTION_WORKER_STILL_RUNNING = False
    report = {"schemaVersion": 1, "kind": "administrator-fskit-fresh-image-ro-diagnostic",
              "status": "failed", "createdAt": datetime.now(timezone.utc).isoformat(),
              "physicalDeviceTargetAccepted": False, "writableMountRequested": False,
              "mountedWriteAttemptPerformed": False, "certifiesFilesystemSafety": False,
              "architecture": platform.machine(), "macOS": platform.mac_ver()[0]}
    lab = None
    try:
        bundle, tools, checksums, environment = validate_invocation()
        os.umask(0o077)
        report.update({"effectiveUID": os.geteuid(), "toolSHA256": checksums,
                       "privateLibraryPath": environment["DYLD_LIBRARY_PATH"]})
        lab = AdminImageLab(bundle, tools, environment, report)
        expected = lab.prepare()
        candidate = lab.mount()
        lab.inspect(expected, candidate)
        report["inspectionPassed"] = True
    except (OSError, LabError, subprocess.SubprocessError, KeyboardInterrupt, EOFError) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            clean = lab.cleanup()
            if clean and report.get("inspectionPassed") and report.get("cleanUnmount"):
                report["status"] = "passed"
            try:
                lab.log_tails()
            except OSError as error:
                report["logReadError"] = str(error)[:300]
        report["completedAt"] = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(report, indent=2) + "\n"
        if lab is not None:
            try:
                with (lab.work / "report.json").open("x", encoding="utf-8") as stream:
                    stream.write(encoded)
            except OSError as error:
                report["reportSaveError"] = str(error)[:300]
                report["status"] = "failed"
                encoded = json.dumps(report, indent=2) + "\n"
        print(encoded, end="")
        INSPECTION_WORKER_STILL_RUNNING = bool(report.get("inspectionWorkerStillRunning"))
    return 0 if report["status"] == "passed" else 1


def finish_supervisor(exit_code, stuck_inspection_worker):
    sys.stdout.flush()
    sys.stderr.flush()
    if stuck_inspection_worker:
        # An uninterruptible filesystem read may survive SIGKILL. Python's
        # multiprocessing atexit would join it indefinitely. JSON already names
        # the inspection PID and preserved mount; never block this supervisor on
        # that child, and never signal the independent filesystem server here.
        os._exit(1)
        return
    raise SystemExit(exit_code)


if __name__ == "__main__":
    finish_supervisor(main(), INSPECTION_WORKER_STILL_RUNNING)
