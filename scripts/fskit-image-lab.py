#!/usr/bin/env python3
"""Read-only FSKit experiment on a newly created disposable regular image.

No physical device, existing image, mountpoint, writable mode, or option string
can be supplied. Never run as root. A failed/uncertain mount retains its image,
server and recovery paths; this harness never forces unmount or kills a server
while its mount could still exist. Review this script before its first real run.
"""
import argparse
import ctypes
import errno
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
from datetime import datetime, timezone


IMAGE_BYTES = 256 * 1024 * 1024
MNT_RDONLY = 0x1
MNT_EXT_FSKIT = 0x2
MNT_NOWAIT = 2
ALLOWED_TYPES = {"macfuse-local", "macfuse"}
RUNTIME_HASHES = {
    Path("/usr/local/lib/libfuse.2.dylib"): "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42",
    Path("/Library/Filesystems/macfuse.fs/Contents/Frameworks/MFMount.framework/Versions/A/MFMount"):
        "e63b1a477d2ae0df65aec1d09707108476d9e4adbe2004f9f6aba9cd1c276fa6",
}
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}


class LabError(RuntimeError):
    pass


class DarwinStatFS(ctypes.Structure):
    # 64-bit layout from the deployment SDK's sys/mount.h, including f_flags_ext.
    _fields_ = [("bsize", ctypes.c_uint32), ("iosize", ctypes.c_int32),
                ("blocks", ctypes.c_uint64), ("bfree", ctypes.c_uint64),
                ("bavail", ctypes.c_uint64), ("files", ctypes.c_uint64), ("ffree", ctypes.c_uint64),
                ("fsid", ctypes.c_int32 * 2), ("owner", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32),
                ("fstypename", ctypes.c_char * 16), ("mntonname", ctypes.c_char * 1024),
                ("mntfromname", ctypes.c_char * 1024), ("flags_ext", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 7)]


def snapshot(item):
    return {"source": bytes(item.mntfromname).decode("utf-8", errors="strict"),
            "mountPoint": bytes(item.mntonname).decode("utf-8", errors="strict"),
            "filesystemType": bytes(item.fstypename).decode("utf-8", errors="strict"),
            "flags": item.flags, "extendedFlags": item.flags_ext, "ownerUID": item.owner,
            "filesystemID": list(item.fsid)}


def mount_table():
    """Read the kernel's cached mount table without touching filesystem contents."""
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.getfsstat64
    function.argtypes = [ctypes.POINTER(DarwinStatFS), ctypes.c_int, ctypes.c_int]
    function.restype = ctypes.c_int
    count = function(None, 0, MNT_NOWAIT)
    if count < 0:
        raise OSError(ctypes.get_errno(), "Could not read mount count")
    entries = (DarwinStatFS * (count + 16))()
    actual = function(entries, ctypes.sizeof(entries), MNT_NOWAIT)
    if actual < 0 or actual > len(entries):
        raise LabError("Could not take a complete mount snapshot")
    return [snapshot(entries[index]) for index in range(actual)]


def descriptor_mount(descriptor):
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.fstatfs64
    function.argtypes = [ctypes.c_int, ctypes.POINTER(DarwinStatFS)]
    function.restype = ctypes.c_int
    result = DarwinStatFS()
    if function(descriptor, ctypes.byref(result)) != 0:
        raise OSError(ctypes.get_errno(), "Could not verify open mount descriptor")
    return snapshot(result)


def digest(path):
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
        return result.hexdigest()


def tools_for(prefix):
    architecture = platform.machine()
    if architecture not in {"arm64", "x86_64"}:
        raise LabError("Unsupported host architecture")
    prefix = prefix.resolve(strict=True)
    tools = {name: prefix / "bin" / architecture / name for name in ["mkntfs", "ntfscp"]}
    tools["ntfs-3g"] = prefix / "driver" / architecture / "ntfs-3g"
    checksums = {}
    for path in tools.values():
        info = path.lstat()
        if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
                or not os.access(path, os.X_OK)):
            raise LabError("Engine binaries must be owned, unprivileged files in the selected build tree")
        # Pin execution to checksums emitted by the reviewed local build scripts.
        records = path.with_name("binary-checksums.txt").read_text(encoding="utf-8").splitlines()
        matching = [line.split(None, 1)[0] for line in records
                    if len(line.split(None, 1)) == 2 and line.split(None, 1)[1].strip() == str(path)]
        actual = digest(path)
        if matching != [actual]:
            raise LabError("Engine checksum does not match its local build manifest")
        checksums[path.name] = actual
    for path, expected in RUNTIME_HASHES.items():
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022 or digest(path) != expected:
            raise LabError("Installed macFUSE runtime differs from the reviewed 5.4.0 build")
    return tools, checksums


def validate_mount(entry, image, mountpoint):
    if entry["mountPoint"] != str(mountpoint):
        raise LabError("Unexpected mountpoint")
    if entry["filesystemType"] not in ALLOWED_TYPES or not entry["extendedFlags"] & MNT_EXT_FSKIT:
        raise LabError("Effective backend is not verified macFUSE FSKit")
    if entry["source"] != str(image):
        raise LabError("FSKit source differs from the image fsname; review observed mount metadata")
    if not entry["flags"] & MNT_RDONLY:
        raise LabError("The effective mount is not read-only")


def inspect_fixture(image, mountpoint, expected, verified_mount):
    descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        entry = descriptor_mount(descriptor)
        validate_mount(entry, image, mountpoint)
        if entry["filesystemID"] != verified_mount["filesystemID"]:
            raise LabError("Mounted filesystem identity changed")
        fixture = os.open("fixture.bin", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(fixture, "rb") as stream:
            restored = stream.read(len(expected) + 1)
        if hashlib.sha256(restored).digest() != hashlib.sha256(expected).digest():
            raise LabError("Mounted fixture readback mismatch")
        # The directory descriptor remains bound to this filesystem if a path
        # changes. Never create through a path that could fall back to the host disk.
        validate_mount(descriptor_mount(descriptor), image, mountpoint)
        try:
            attempt = os.open("write-must-fail-" + uuid.uuid4().hex,
                              os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=descriptor)
        except OSError as error:
            if error.errno != errno.EROFS:
                raise LabError("Write attempt failed for a reason other than EROFS") from error
        else:
            os.close(attempt)
            raise LabError("Read-only enforcement failed: empty-file creation unexpectedly succeeded")
        return ["Read fixture through FSKit with matching SHA-256", "New-file creation rejected with EROFS"]
    finally:
        os.close(descriptor)


def inspect_worker(connection, image, mountpoint, expected, verified_mount):
    try:
        connection.send({"checks": inspect_fixture(image, mountpoint, expected, verified_mount)})
    except (OSError, LabError, KeyboardInterrupt) as error:
        connection.send({"error": type(error).__name__ + ": " + str(error)[:300]})
    finally:
        connection.close()


class ImageLab:
    def __init__(self, tools, report):
        self.tools = tools
        self.report = report
        self.work = Path(tempfile.mkdtemp(prefix="nativol-fskit-ro-")).resolve()
        os.chmod(self.work, 0o700)
        self.image = self.work / "disposable.ntfs"
        self.mountpoint = Path("/Volumes") / ("Nativol-RO-" + uuid.uuid4().hex)
        self.server = None
        self.server_log = None
        self.identity = None
        self.verified_mount = None
        self.original_hash = None
        self.report.update({"workDirectory": str(self.work), "mountPoint": str(self.mountpoint),
                            "imagePath": str(self.image), "imageRetained": True,
                            "cleanupRequired": False, "checks": []})

    def verify_image(self):
        info = self.image.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_size != IMAGE_BYTES or (info.st_dev, info.st_ino) != self.identity
                or self.image.parent != self.work):
            raise LabError("Disposable image identity changed")

    def run_tool(self, name, *arguments):
        self.verify_image()
        result = subprocess.run([str(self.tools[name]), *map(str, arguments)], cwd=self.work,
                                env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        if result.returncode:
            raise LabError(name + " failed with exit " + str(result.returncode))
        self.verify_image()

    def entry(self):
        matches = [entry for entry in mount_table() if entry["mountPoint"] == str(self.mountpoint)]
        if len(matches) > 1:
            raise LabError("Ambiguous mountpoint")
        return matches[0] if matches else None

    def prepare(self):
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint is already occupied")
        descriptor = os.open(self.image, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(descriptor, IMAGE_BYTES)
            info = os.fstat(descriptor)
            self.identity = (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)
        self.run_tool("mkntfs", "-F", "-Q", "-L", "NATIVOL_RO_LAB", self.image)
        payload = uuid.uuid4().bytes + bytes(range(256)) * 4096 + b"Nativol FSKit RO fixture\n"
        fixture = self.work / "fixture.bin"
        fixture.write_bytes(payload)
        self.run_tool("ntfscp", self.image, fixture, "/fixture.bin")
        self.original_hash = digest(self.image)
        self.report["imageSHA256BeforeMount"] = self.original_hash
        self.report["checks"].append("Created private disposable image with unpredictable fixture")
        return payload

    def mount(self):
        self.verify_image()
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint became occupied before launch")
        options = ",".join(["backend=fskit", "no_def_opts", "norecover", "ro", "no_detach", "quiet",
                            "uid=" + str(os.getuid()), "gid=" + str(os.getgid()), "umask=077",
                            "volname=" + self.mountpoint.name])
        self.server_log = (self.work / "driver.log").open("xb")
        self.server = subprocess.Popen([str(self.tools["ntfs-3g"]), str(self.image), str(self.mountpoint),
                                        "-o", options], cwd=self.work, env=ENV, stdin=subprocess.DEVNULL,
                                       stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"driverPID": self.server.pid, "requestedOptions": options,
                            "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            entry = self.entry()
            if entry is not None:
                self.report["observedMount"] = entry
                validate_mount(entry, self.image, self.mountpoint)
                self.verified_mount = entry
                self.report["checks"].append("Verified image source, FSKit backend and effective read-only mount")
                return
            exit_code = self.server.poll()
            if exit_code is not None:
                self.report["driverExitCode"] = exit_code
                raise LabError("Driver exited before a verified mount; inspect retained driver.log")
            time.sleep(0.1)
        raise LabError("Mount did not become ready within 30 seconds")

    def inspect(self, expected):
        # A stalled experimental filesystem must not block the parent indefinitely.
        # The worker has only this generated image/mountpoint; there is no CLI worker mode.
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=inspect_worker,
                                 args=(sender, self.image, self.mountpoint, expected, self.verified_mount))
        try:
            worker.start()
            sender.close()
            self.report["inspectionWorkerPID"] = worker.pid
            worker.join(timeout=20)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
                self.report["inspectionWorkerStillRunning"] = worker.is_alive()
                raise LabError("Mounted I/O timed out; preserve the image if normal unmount cannot finish")
            if worker.exitcode != 0 or not receiver.poll():
                raise LabError("Mounted inspection worker exited without a result")
            result = receiver.recv()
            if "error" in result:
                raise LabError(result["error"])
            self.report["checks"].extend(result["checks"])
        finally:
            sender.close()
            receiver.close()

    def cleanup(self):
        """Only unmount the exact verified image; uncertainty retains every artifact."""
        try:
            entry = self.entry()
            vanished_before_unmount = entry is None and self.verified_mount is not None
            normal_unmount_completed = False
            if entry is not None:
                self.report["observedMountAtCleanup"] = entry
                if self.verified_mount is None or entry != self.verified_mount:
                    raise LabError("Unverified or changed mount retained for manual review")
                validate_mount(entry, self.image, self.mountpoint)
                result = subprocess.run(["/usr/sbin/diskutil", "unmount", str(self.mountpoint)],
                                        env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                if result.returncode or self.entry() is not None:
                    raise LabError("Normal unmount did not complete; no force attempted")
                normal_unmount_completed = True
            if self.server is not None and self.server.poll() is None:
                # A failed asynchronous mount may still be pending. Terminate only our
                # own process after absence is established, then recheck the mount table.
                self.server.terminate()
                self.server.wait(timeout=10)
            if self.entry() is not None:
                raise LabError("A mount appeared during shutdown; artifacts retained")
            if self.server_log is not None:
                self.server_log.close()
            self.report["cleanupRequired"] = False
            self.report["cleanUnmount"] = normal_unmount_completed
            if self.identity is not None:
                self.verify_image()
                after = digest(self.image)
                self.report["imageSHA256AfterUnmount"] = after
                if self.original_hash is not None and after != self.original_hash:
                    raise LabError("Read-only experiment changed the image bytes")
            if vanished_before_unmount:
                self.report["cleanupError"] = "Verified mount disappeared before normal unmount; lifecycle check failed"
                return False
            if normal_unmount_completed:
                self.report["checks"].append("Normal unmount and unchanged image SHA-256")
            # Preserve logs and failed experiments. Successful private images alone are removed.
            if self.report.get("inspectionPassed") and normal_unmount_completed and self.original_hash is not None:
                self.image.unlink()
                (self.work / "fixture.bin").unlink()
                self.report["imageRetained"] = False
            return True
        except (OSError, LabError, subprocess.SubprocessError) as error:
            self.report["cleanupError"] = str(error)[:300]
            self.report["cleanupRequired"] = True
            return False


def save_report(report, path):
    encoded = json.dumps(report, indent=2) + "\n"
    print(encoded, end="")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".nativol-fskit-report-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-prefix", type=Path, required=True, help="Reviewed .local-engine build root")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        parser.error("Run only as the ordinary logged-in user")
    if sys.platform != "darwin":
        parser.error("This experimental harness requires macOS")
    report = {"schemaVersion": 1, "kind": "fskit-read-only-disposable-image", "status": "failed",
              "createdAt": datetime.now(timezone.utc).isoformat(), "architecture": platform.machine(),
              "macOS": platform.mac_ver()[0], "physicalDeviceTargetAccepted": False,
              "writableMountRequested": False}
    lab = None
    try:
        tools, checksums = tools_for(args.engine_prefix)
        report["toolSHA256"] = checksums
        lab = ImageLab(tools, report)
        payload = lab.prepare()
        lab.mount()
        lab.inspect(payload)
        report["inspectionPassed"] = True
    except (OSError, LabError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:300]
    finally:
        if lab is not None:
            clean = lab.cleanup()
            if clean and report.get("inspectionPassed"):
                report["status"] = "passed"
            report["completedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            save_report(report, args.report)
        except OSError:
            print("Could not save report; JSON was printed above.", file=sys.stderr)
            return 1
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
