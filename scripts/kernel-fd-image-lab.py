#!/usr/bin/env python3
"""Default prepare-only. --execute tests only a fresh renamed regular image.

The held image FD is inherited as fd 3 by the reviewed NTFS driver. The old
pathname is replaced before launch, proving that reads/writes follow the held
inode. No root, device path, existing image or custom option is accepted.
"""
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("nativol_fd_write_base", Path(__file__).with_name("kernel-write-image-lab.py"))
WRITE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = WRITE
spec.loader.exec_module(WRITE)
BASE, REFERENCE, LabError, ENV = WRITE.BASE, WRITE.REFERENCE, WRITE.LabError, WRITE.ENV
# This new diagnostic alone selects the newly built Darwin-corrected engine.
BASE.TOOL_RECORDS["ntfs-3g"] = ("driver/x86_64/ntfs-3g", "e88c93583bea62297084cf8fac13a70d1c02d5b7ad0abfacdd9afbab66b474c5")
SOURCE = "/dev/fd/3"
TRAMPOLINE = "import os,sys; fd=int(sys.argv[1]); os.dup2(fd,3,inheritable=True); os.set_inheritable(3,True); os.execv(sys.argv[2],sys.argv[2:])"
STUCK = False


def fd_worker(kind, result_path, mountpoint, candidate, fixture_name, expected):
    try:
        result = REFERENCE.inspect_fixture(mountpoint, fixture_name, expected, candidate)
        if candidate["source"] != SOURCE:
            raise LabError("Unexpected descriptor source")
        if kind != "ownership":
            if kind not in {"exercise", "verify"}:
                raise LabError("Unknown fixed worker action")
            writing = kind == "exercise"
            if result["effectiveReadOnly"] is writing:
                raise LabError("Unexpected effective access")
            directory = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if REFERENCE.descriptor_mount(directory) != candidate:
                    raise LabError("Mount changed before fixture access")
                payload = hashlib.sha256(expected).digest() * 2048
                name = "held-descriptor-write.bin"
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if writing else os.O_RDONLY
                fd = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or REFERENCE.descriptor_mount(fd) != candidate):
                        raise LabError("Fixture is not a regular file in the proved mount")
                    if writing:
                        if os.write(fd, payload) != len(payload): raise LabError("Short fixture write")
                        os.fsync(fd)
                        os.lseek(fd, 0, os.SEEK_SET)
                    actual = os.read(fd, len(payload) + 1)
                    if actual != payload or os.fstat(fd).st_size != len(payload):
                        raise LabError("Held-FD fixture did not persist exactly")
                    if REFERENCE.descriptor_mount(directory) != candidate:
                        raise LabError("Mount changed during fixture access")
                finally:
                    os.close(fd)
            finally:
                os.close(directory)
            result.update({"status": "passed", "manifest": {"name": name, "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}})
        encoded = json.dumps(result).encode()
        if len(encoded) > WRITE.MAX_RESULT: raise LabError("Oversized result")
    except Exception as error:
        encoded = json.dumps({"error": type(error).__name__ + ": " + str(error)[:500]}).encode()
    fd = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream: stream.write(encoded)


class FDStage(WRITE.ImageStage):
    def verify_image(self):
        self.owner.verify_image()

    def entry(self):
        table = REFERENCE.mount_table()
        if any(item["source"] in {SOURCE, str(self.image), str(self.owner.replaced_path)}
               and item["mountPoint"] != str(self.mountpoint) for item in table):
            raise LabError("Descriptor/image source has an unexpected mount; server retained")
        matching = [item for item in table if item["mountPoint"] == str(self.mountpoint)]
        if len(matching) > 1: raise LabError("Ambiguous mountpoint")
        return matching[0] if matching else None

    def mount(self):
        self.owner.assert_detached()
        self.verify_image()
        self.report["kernelReadinessBeforeMount"] = BASE.inspect_readiness(True)
        if BASE.verify_tool("ntfs-3g") != self.tools["ntfs-3g"]: raise LabError("Driver changed")
        if os.path.lexists(self.mountpoint) or self.entry(): raise LabError("Occupied mountpoint")
        options = ",".join(["backend=kernel", "local", "no_def_opts", "norecover", self.phase,
            "no_detach", "quiet", "uid=" + str(os.getuid()), "gid=" + str(os.getgid()), "umask=077",
            "default_permissions", "usermapping=/dev/null", "windows_names", "nosuid", "nodev",
            "volname=" + self.mountpoint.name])
        self.server_log = self.log_path.open("xb")
        self.server = subprocess.Popen(["/usr/bin/python3", "-I", "-S", "-c", TRAMPOLINE,
            str(self.owner.held_fd), str(self.tools["ntfs-3g"]), SOURCE, str(self.mountpoint), "-o", options],
            pass_fds=(self.owner.held_fd,), cwd=self.work, env=ENV, stdin=subprocess.DEVNULL,
            stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"serverPID": self.server.pid, "requestedOptions": options,
                            "mountAttemptPerformed": True, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            candidate = self.entry()
            if candidate:
                REFERENCE.validate_identity(candidate, self.mountpoint)
                if candidate["source"] != SOURCE or self.server.poll() is not None:
                    raise LabError("Candidate lacks exact inherited source/live server")
                self.report["observedMount"] = candidate
                return candidate
            if self.server.poll() is not None: raise LabError("Driver exited without mount")
            time.sleep(0.1)
        raise LabError("Mount deadline exceeded")

    def require_current_ownership(self):
        if (self.verified_mount is None or self.server is None or self.server.poll() is not None
                or self.entry() != self.verified_mount or self.verified_mount["source"] != SOURCE):
            raise LabError("Held-FD stage ownership changed")
        self.owner.verify_image()
        REFERENCE.validate_identity(self.verified_mount, self.mountpoint)

    def run_worker(self, kind, candidate):
        path = self.work / (self.phase + "-" + kind + ".json")
        worker = multiprocessing.get_context("spawn").Process(target=fd_worker,
            args=(kind, path, self.mountpoint, candidate, self.fixture_name, self.expected))
        evidence = {"operation": kind, "resultPath": str(path)}
        self.report["workers"].append(evidence)
        try:
            worker.start(); evidence["pid"] = worker.pid
            worker.join(timeout=30)
            if worker.is_alive(): raise LabError("Fixture worker exceeded deadline")
            if worker.exitcode: raise LabError("Fixture worker failed")
            return WRITE.read_result(path)
        finally:
            if worker.pid is not None and worker.is_alive():
                worker.terminate(); worker.join(timeout=2)
                if worker.is_alive(): worker.kill(); worker.join(timeout=2)
                self.report["inspectionWorkerStillRunning"] = worker.is_alive()


class FDLab(WRITE.WriteImageLab):
    def __init__(self, tools, report):
        super().__init__(tools, report)
        self.replaced_path = self.image
        self.held_fd = None
        self.renamed = False

    def verify_image(self):
        if not self.renamed: return super().verify_image()
        info = self.image.lstat()
        opened = os.fstat(self.held_fd)
        replacement = self.replaced_path.lstat()
        if (self.image != self.work / "held.ntfs" or self.work.resolve() != self.work
                or self.image.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or info.st_size != BASE.IMAGE_BYTES
                or (info.st_dev, info.st_ino) != self.image_identity
                or (opened.st_dev, opened.st_ino) != self.image_identity
                or (replacement.st_dev, replacement.st_ino) != self.replacement_identity):
            raise LabError("Held image or replacement identity changed")

    def assert_detached(self):
        super().assert_detached()
        if any(item["source"] in {SOURCE, str(self.replaced_path)} for item in REFERENCE.mount_table()):
            self.report["detachedStateUncertain"] = True
            raise LabError("Descriptor or replaced image source remains mounted")

    def prepare(self):
        super().prepare()
        self.held_fd = os.open(self.image, os.O_RDWR | os.O_NOFOLLOW)
        if (os.fstat(self.held_fd).st_dev, os.fstat(self.held_fd).st_ino) != self.image_identity:
            raise LabError("Held descriptor differs from fresh image")
        self.image.rename(self.work / "held.ntfs")
        self.image = self.work / "held.ntfs"
        fd = os.open(self.replaced_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"Nativol replacement must remain untouched\n" + os.urandom(256))
            stream.truncate(BASE.IMAGE_BYTES)
            info = os.fstat(stream.fileno())
            self.replacement_identity = (info.st_dev, info.st_ino)
        self.replacement_hash = REFERENCE.digest(self.replaced_path)
        self.renamed = True
        self.verify_image()
        self.report.update({"imagePath": str(self.image), "replacedPath": str(self.replaced_path),
            "heldImageDevice": self.image_identity[0], "heldImageInode": self.image_identity[1],
            "replacementInode": self.replacement_identity[1], "replacementSHA256Before": self.replacement_hash})

    def run_stage(self, phase):
        # Reuse reviewed normal-unmount/natural-exit lifecycle with only the
        # stage constructor changed. The base module is private to this runner.
        previous = WRITE.ImageStage
        WRITE.ImageStage = FDStage
        try: return super().run_stage(phase)
        finally: WRITE.ImageStage = previous

    def execute(self):
        super().execute()
        self.assert_detached()
        replacement_hash = REFERENCE.digest(self.replaced_path)
        self.report.update({"replacementSHA256After": replacement_hash,
            "replacementUnchanged": replacement_hash == self.replacement_hash,
            "heldDescriptorBindingPassed": replacement_hash == self.replacement_hash})
        if replacement_hash != self.replacement_hash: raise LabError("Replacement pathname was modified")


def main():
    global STUCK
    if sys.argv[1:] not in ([], ["--execute"]): return 2
    report = {"schemaVersion": 1, "kind": "kernel-held-fd-image", "status": "failed",
              "physicalDeviceTargetAccepted": False, "authorizationRequested": False,
              "mountAttemptPerformed": False}
    lab = None
    try:
        tools, evidence = WRITE.preflight(bool(sys.argv[1:]))
        evidence["scriptSHA256"]["kernel-fd-image-lab.py"] = REFERENCE.digest(Path(__file__))
        report["build"] = evidence
        if not sys.argv[1:]: report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = FDLab(tools, report)
            lab.execute()
            if not WRITE.success_evidence(report): raise LabError("Incomplete persistence evidence")
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab:
            STUCK = any(s.report.get("inspectionWorkerStillRunning") for s in lab.stages)
            report["cleanupRequired"] = bool(report.get("detachedStateUncertain") or STUCK
                or any(s.report.get("cleanupRequired") for s in lab.stages))
            report["mountAttemptPerformed"] = any(s.report.get("mountAttemptPerformed") for s in lab.stages)
            if not report["cleanupRequired"] and lab.held_fd is not None: os.close(lab.held_fd)
            if report["cleanupRequired"]: report["status"] = "failed"
            try:
                with (lab.work / "fd-report.json").open("x") as stream: json.dump(report, stream, indent=2)
            except OSError as error:
                report.update({"status": "failed", "reportSaveError": str(error)[:300]})
        print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    REFERENCE.finish_supervisor(main(), STUCK)
