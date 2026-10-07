#!/usr/bin/env python3
"""Prepare a fresh-image NTFS/kernel diagnostic; only --execute performs it.

No existing image, physical device, target, backend or option can be supplied.
Execution creates one private 256 MiB regular file, formats it offline, requests
read-only mounting, reads its nonce fixture and normally unmounts it. All files
are retained. No mounted write test, kernel loading or authorization is done.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import time
import uuid


def load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Register the same fixed module names in spawned workers, without any execution
# at import time. Reuse the reviewed descriptor, bounded worker and cleanup code.
REFERENCE = load_sibling("nativol_kernel_image_reference", "fskit-reference-lab.py")
READINESS = load_sibling("nativol_kernel_image_readiness", "kernel-readiness.py")
REFERENCE.BACKEND = "kernel"
REFERENCE.ALLOWED_TYPES = {"macfuse"}
LabError = REFERENCE.LabError
ROOT = Path(__file__).resolve().parent.parent
ENV = REFERENCE.ENV
IMAGE_BYTES = 256 * 1024 * 1024
INSPECTION_WORKER_STILL_RUNNING = False
TOOL_RECORDS = {
    "mkntfs": ("bin/x86_64/mkntfs", "9af42ef92fad65a533dc8a5cbfc802941d7d17baffd71c0bf13c84ad327c388f"),
    "ntfscp": ("bin/x86_64/ntfscp", "0522188e9cdb137ea63ea074aaa164b90122b4a13546a826719ab92039123b66"),
    "ntfs-3g": ("driver/x86_64/ntfs-3g", "7e10c1cc3a750b8d5deece7c8633303f1a1af65b1f2d499152db429f46ab7f43"),
}


def validate_host():
    if sys.argv[1:] not in ([], ["--execute"]):
        raise LabError("Only --execute is accepted; no image or device target is accepted")
    if (sys.platform != "darwin" or platform.machine() != "x86_64"
            or platform.mac_ver()[0].split(".")[0] != "15"):
        raise LabError("Only the reviewed Intel macOS 15 configuration is covered")
    if (os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid()):
        raise LabError("Run as the ordinary user without mixed user or group identities")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise LabError("Invoke with /usr/bin/python3 -I -S")


def verify_tool(name):
    relative, expected = TOOL_RECORDS[name]
    path = ROOT / ".local-engine" / relative
    info = path.lstat()
    if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_nlink != 1
            or info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
            or not os.access(path, os.X_OK)):
        raise LabError("Engine must be an owned, unprivileged regular executable: " + name)
    actual = REFERENCE.digest(path)
    after = path.lstat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise LabError("Engine changed during verification: " + name)
    manifest = path.with_name("binary-checksums.txt")
    manifest_info = manifest.lstat()
    if (manifest.resolve(strict=True) != manifest or not stat.S_ISREG(manifest_info.st_mode)
            or manifest_info.st_uid != os.getuid() or manifest_info.st_nlink != 1
            or manifest_info.st_mode & 0o022 or manifest_info.st_size > 65536):
        raise LabError("Unexpected engine checksum manifest")
    matches = [line.split(None, 1)[0] for line in manifest.read_text().splitlines()
               if len(line.split(None, 1)) == 2 and line.split(None, 1)[1].strip() == str(path)]
    if actual != expected or matches != [expected]:
        raise LabError("Engine differs from reviewed hash or build manifest: " + name)
    return path


def inspect_readiness(require_loaded):
    record = READINESS.inspect_readiness()
    if not record.get("verificationPassed"):
        raise LabError("Installed kernel backend verification failed")
    if require_loaded and (not record.get("backendLoaded") or record.get("errors")):
        raise LabError("Exact verified kernel module is not loaded; this diagnostic cannot load it")
    return record


def preflight(require_loaded):
    validate_host()
    tools = {name: verify_tool(name) for name in TOOL_RECORDS}
    for path, expected in REFERENCE.RUNTIME_HASHES.items():
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022 or REFERENCE.digest(path) != expected:
            raise LabError("Installed macFUSE runtime differs from the reviewed version")
    signed = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(tools["ntfs-3g"])],
                            env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    if signed.returncode:
        raise LabError("NTFS driver signature verification failed")
    return tools, {"toolSHA256": {name: record[1] for name, record in TOOL_RECORDS.items()},
                   "scriptSHA256": {name: REFERENCE.digest(Path(__file__).with_name(name)) for name in
                                    ("kernel-image-lab.py", "fskit-reference-lab.py", "kernel-readiness.py")},
                   "kernelReadiness": inspect_readiness(require_loaded)}


class KernelImageLab(REFERENCE.ReferenceLab):
    def __init__(self, tools, report):
        self.tools, self.report = tools, report
        self.work = Path(tempfile.mkdtemp(prefix="nativol-kernel-image-ro-")).resolve()
        os.chmod(self.work, 0o700)
        self.image = self.work / "disposable.ntfs"
        self.mountpoint = Path("/Volumes") / ("Nativol-Kernel-Image-RO-" + uuid.uuid4().hex)
        self.nonce = os.urandom(32).hex()
        self.fixture_name = "fixture-" + self.nonce + ".bin"
        self.expected = bytes.fromhex(self.nonce) + b"Nativol kernel NTFS read-only image\n" + bytes(range(256)) * 256
        self.server = self.server_log = self.verified_mount = self.image_identity = self.original_hash = None
        report.update({"workDirectory": str(self.work), "imagePath": str(self.image),
                       "mountPoint": str(self.mountpoint), "imageRetained": True,
                       "cleanupRequired": False, "checks": []})

    def verify_image(self):
        directory = self.work.lstat()
        if (self.work.resolve(strict=True) != self.work or not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700
                or self.image != self.work / "disposable.ntfs"):
            raise LabError("Private image directory identity or permissions changed")
        info = self.image.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != IMAGE_BYTES
                or (info.st_dev, info.st_ino) != self.image_identity):
            raise LabError("Fresh disposable image identity, size or permissions changed")

    def hash_image(self):
        self.verify_image()
        descriptor = os.open(self.image, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if ((info.st_dev, info.st_ino) != self.image_identity
                    or not stat.S_ISREG(info.st_mode) or info.st_size != IMAGE_BYTES):
                raise LabError("Image descriptor differs from freshly created image")
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        self.verify_image()
        return digest.hexdigest()

    def run_tool(self, name, *arguments):
        self.verify_image()
        if verify_tool(name) != self.tools[name]:
            raise LabError("Tool path changed")
        result = subprocess.run([str(self.tools[name]), *map(str, arguments)], cwd=self.work,
                                env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        self.report.setdefault("offlineTools", []).append({"name": name, "exitCode": result.returncode,
            "outputTail": (result.stdout + result.stderr)[-4096:].decode("utf-8", "replace")})
        if result.returncode:
            raise LabError(name + " failed with exit " + str(result.returncode))
        self.verify_image()

    def prepare(self):
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint is occupied before image creation")
        descriptor = os.open(self.image, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise LabError("Fresh image is not a private regular file")
            self.image_identity = (info.st_dev, info.st_ino)
            os.ftruncate(descriptor, IMAGE_BYTES)
        finally:
            os.close(descriptor)
        self.run_tool("mkntfs", "-F", "-Q", "-L", "NATIVOL_KERNEL_RO", self.image)
        fixture_path = self.work / self.fixture_name
        descriptor = os.open(fixture_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(self.expected)
        self.run_tool("ntfscp", self.image, fixture_path, "/" + self.fixture_name)
        self.original_hash = self.hash_image()
        self.report.update({"imageSHA256BeforeMount": self.original_hash,
                            "expectedFixtureSHA256": hashlib.sha256(self.expected).hexdigest()})
        self.report["checks"].append("Created fresh private 256 MiB regular NTFS image and random nonce fixture")

    def mount(self):
        self.verify_image()
        if self.original_hash is None:
            raise LabError("No offline image baseline")
        self.report["kernelReadinessBeforeMount"] = inspect_readiness(True)
        if verify_tool("ntfs-3g") != self.tools["ntfs-3g"]:
            raise LabError("Driver path changed")
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated mountpoint became occupied before launch")
        options = ",".join(["backend=kernel", "local", "no_def_opts", "norecover", "ro", "no_detach", "quiet",
                            "uid=" + str(os.getuid()), "gid=" + str(os.getgid()), "umask=077",
                            "volname=" + self.mountpoint.name])
        self.server_log = (self.work / "reference.log").open("xb")
        self.server = subprocess.Popen([str(self.tools["ntfs-3g"]), str(self.image), str(self.mountpoint),
                                        "-o", options], cwd=self.work, env=ENV, stdin=subprocess.DEVNULL,
                                       stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"serverPID": self.server.pid, "requestedOptions": options,
                            "mountAttemptPerformed": True, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            entry = self.entry()
            if entry is not None:
                self.report["observedMount"] = entry
                REFERENCE.validate_identity(entry, self.mountpoint)
                if entry["source"] != str(self.image):
                    raise LabError("Kernel mount source differs from the fresh regular image")
                if self.server.poll() is not None:
                    raise LabError("Candidate has no live owned NTFS server")
                return entry  # Ownership still requires descriptor-bound nonce readback.
            code = self.server.poll()
            if code is not None:
                self.report["serverExitCode"] = code
                raise LabError("NTFS server exited without a verified OS mount")
            time.sleep(0.1)
        raise LabError("No kernel image candidate within 30 seconds")

    def cleanup(self):
        clean = super().cleanup()
        # Even vanished-mount failures can record an unchanged image once no
        # mount/server remains. A failed hash must not invent active cleanup.
        if self.original_hash is not None and not self.report.get("cleanupRequired", True):
            try:
                final_hash = self.hash_image()
                self.report.update({"imageSHA256AfterUnmount": final_hash,
                                    "imageUnchanged": final_hash == self.original_hash})
                if final_hash != self.original_hash:
                    raise LabError("Disposable image changed during the read-only experiment")
                self.report["checks"].append("Whole disposable image SHA-256 remained unchanged")
            except (OSError, LabError) as error:
                self.report["imageIntegrityError"] = str(error)[:500]
                return False
        return clean

    def inspect(self, candidate):
        try:
            return super().inspect(candidate)
        finally:
            self.report["checks"] = [check.replace("in-memory nonce match", "NTFS image nonce fixture match")
                                     for check in self.report["checks"]]


def main():
    global INSPECTION_WORKER_STILL_RUNNING
    INSPECTION_WORKER_STILL_RUNNING = False
    if sys.argv[1:] not in ([], ["--execute"]):
        print("Only --execute is accepted; no image, device or mount target is accepted.", file=sys.stderr)
        return 2
    execute = sys.argv[1:] == ["--execute"]
    report = {"schemaVersion": 1, "kind": "kernel-ntfs-image-readonly-diagnostic",
              "status": "failed", "createdAt": datetime.now(timezone.utc).isoformat(),
              "backend": "kernel", "mode": "execute" if execute else "prepare",
              "architecture": platform.machine(), "macOS": platform.mac_ver()[0],
              "physicalDeviceTargetAccepted": False, "mountAttemptPerformed": False,
              "kernelLoadingPerformed": False, "authorizationRequested": False,
              "writableMountRequested": False, "mountedWriteAttemptPerformed": False,
              "certifiesFilesystemSafety": False, "readyToExecute": False}
    lab = None
    try:
        tools, record = preflight(execute)
        report.update({"build": record, "effectiveUID": os.geteuid(),
                       "readyToExecute": bool(record["kernelReadiness"].get("backendLoaded"))})
        if not execute:
            report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = KernelImageLab(tools, report)
            lab.prepare()
            candidate = lab.mount()
            lab.inspect(candidate)
            report["inspectionPassed"] = True
    except (OSError, LabError, ValueError, subprocess.SubprocessError, KeyboardInterrupt, EOFError) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            clean = lab.cleanup()
            if (clean and report.get("inspectionPassed") and report.get("cleanUnmount")
                    and report.get("imageUnchanged")):
                report["status"] = "passed"
            try:
                lab.log_tail()
            except OSError as error:
                report.update({"logReadError": str(error)[:300], "status": "failed"})
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
    return 0 if report["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    REFERENCE.finish_supervisor(main(), INSPECTION_WORKER_STILL_RUNNING)
