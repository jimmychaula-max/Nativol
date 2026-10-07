#!/usr/bin/env python3
"""Prepare-only by default; --execute tests writes on one new regular NTFS image.

No external target or option is accepted. The writable stage proves ownership
before fixture writes. A separate read-only remount verifies persistence. Both
stages require normal unmounts; all private files and diagnostic logs remain.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
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


def load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_sibling("nativol_kernel_write_image_base", "kernel-image-lab.py")
FIXTURES = load_sibling("nativol_kernel_write_fixtures", "kernel-write-fixtures.py")
REFERENCE, LabError, ENV = BASE.REFERENCE, BASE.LabError, BASE.ENV
MAX_RESULT = 32 * 1024
INSPECTION_WORKER_STILL_RUNNING = False


def preflight(require_loaded):
    tools, record = BASE.preflight(require_loaded)
    record["scriptSHA256"].update({name: REFERENCE.digest(Path(__file__).with_name(name)) for name in
                                   ("kernel-write-image-lab.py", "kernel-write-fixtures.py")})
    return tools, record


def worker_entry(kind, result_path, mountpoint, candidate, fixture_name, expected):
    """A host-side bounded result file avoids pipe send/join deadlocks."""
    try:
        if kind == "ownership":
            result = REFERENCE.inspect_fixture(mountpoint, fixture_name, expected, candidate)
        elif kind == "exercise":
            result = FIXTURES.exercise(mountpoint, candidate, fixture_name, expected)
        elif kind == "verify":
            result = FIXTURES.verify(mountpoint, candidate, fixture_name, expected)
        else:
            raise LabError("Unknown internal worker operation")
        encoded = json.dumps(result).encode("utf-8")
        if len(encoded) > MAX_RESULT:
            raise LabError("Worker result exceeds the diagnostic limit")
    except (Exception, KeyboardInterrupt) as error:
        encoded = json.dumps({"error": type(error).__name__ + ": " + str(error)[:500]}).encode("utf-8")
    descriptor = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


def read_result(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= MAX_RESULT):
            raise LabError("Worker result is not a bounded private regular file")
        data = stream.read(MAX_RESULT + 1)
        if len(data) != info.st_size:
            raise LabError("Worker result changed while reading")
    result = json.loads(data)
    if not isinstance(result, dict) or "error" in result:
        raise LabError(result.get("error", "Invalid worker result") if isinstance(result, dict)
                       else "Worker result must be an object")
    return result


class ImageStage(BASE.KernelImageLab):
    def __init__(self, owner, phase):
        if phase not in {"rw", "ro"}:
            raise LabError("Invalid internal stage")
        self.owner, self.phase = owner, phase
        self.tools, self.work, self.image = owner.tools, owner.work, owner.image
        self.image_identity = owner.image_identity
        self.fixture_name, self.expected = owner.fixture_name, owner.expected
        self.mountpoint = (owner.mountpoint if phase == "rw" else
                           Path("/Volumes") / ("Nativol-Kernel-Write-RO-" + uuid.uuid4().hex))
        self.log_path = self.work / (phase + "-driver.log")
        self.server = self.server_log = self.verified_mount = None
        self.report = {"phase": phase, "status": "failed", "mountPoint": str(self.mountpoint),
                       "cleanupRequired": False, "checks": [], "workers": [],
                       "mountedWriteWorkloadRequested": False}

    def entry(self):
        table = REFERENCE.mount_table()
        if any(item["source"] == str(self.image) and item["mountPoint"] != str(self.mountpoint)
               for item in table):
            raise LabError("Fresh image is mounted outside this stage; server retained")
        matching = [item for item in table if item["mountPoint"] == str(self.mountpoint)]
        if len(matching) > 1:
            raise LabError("Ambiguous stage mountpoint")
        return matching[0] if matching else None

    def mount(self):
        self.owner.assert_detached()
        self.verify_image()
        self.report["kernelReadinessBeforeMount"] = BASE.inspect_readiness(True)
        if BASE.verify_tool("ntfs-3g") != self.tools["ntfs-3g"]:
            raise LabError("Reviewed driver path changed")
        if os.path.lexists(self.mountpoint) or self.entry() is not None:
            raise LabError("Generated stage mountpoint is occupied")
        options = ",".join(["backend=kernel", "local", "no_def_opts", "norecover", self.phase,
                            "no_detach", "quiet", "uid=" + str(os.getuid()),
                            "gid=" + str(os.getgid()), "umask=077", "volname=" + self.mountpoint.name])
        self.server_log = self.log_path.open("xb")
        self.server = subprocess.Popen([str(self.tools["ntfs-3g"]), str(self.image), str(self.mountpoint),
                                        "-o", options], cwd=self.work, env=ENV, stdin=subprocess.DEVNULL,
                                       stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"serverPID": self.server.pid, "requestedOptions": options,
                            "mountAttemptPerformed": True, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            candidate = self.entry()
            if candidate is not None:
                self.report["observedMount"] = candidate
                REFERENCE.validate_identity(candidate, self.mountpoint)
                if candidate["source"] != str(self.image):
                    raise LabError("Candidate source is not this freshly created image")
                if self.server.poll() is not None:
                    raise LabError("Candidate has no live owned NTFS server")
                return candidate
            code = self.server.poll()
            if code is not None:
                self.report["serverExitCode"] = code
                raise LabError("NTFS server exited without an OS mount")
            time.sleep(0.1)
        raise LabError("No stage mount appeared within 30 seconds")

    def run_worker(self, kind, candidate):
        timeout = 20 if kind == "ownership" else 60
        result_path = self.work / (self.phase + "-" + kind + "-" + uuid.uuid4().hex + ".json")
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=worker_entry,
            args=(kind, result_path, self.mountpoint, candidate, self.fixture_name, self.expected))
        evidence = {"operation": kind, "timeoutSeconds": timeout, "resultPath": str(result_path)}
        self.report["workers"].append(evidence)
        try:
            worker.start()
            evidence["pid"] = worker.pid
            worker.join(timeout=timeout)
            if worker.is_alive():
                raise LabError(kind + " worker exceeded " + str(timeout) + " seconds")
            evidence["exitCode"] = worker.exitcode
            if worker.exitcode:
                raise LabError(kind + " worker exited without successful completion")
            return read_result(result_path)
        finally:
            if worker.pid is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
                evidence["stillRunning"] = worker.is_alive()
                self.report["inspectionWorkerStillRunning"] = bool(
                    self.report.get("inspectionWorkerStillRunning") or evidence["stillRunning"])

    def require_current_ownership(self):
        if (self.verified_mount is None or self.server is None or self.server.poll() is not None
                or self.entry() != self.verified_mount):
            raise LabError("Live owned stage identity is required before fixture operations")
        REFERENCE.validate_identity(self.verified_mount, self.mountpoint)
        if self.verified_mount["source"] != str(self.image):
            raise LabError("Owned stage no longer identifies the fresh image")

    def prove_ownership(self, candidate):
        result = self.run_worker("ownership", candidate)
        if (result.get("verifiedMount") != candidate or self.server.poll() is not None
                or self.entry() != candidate
                or result.get("fixtureSHA256") != hashlib.sha256(self.expected).hexdigest()):
            raise LabError("Descriptor-bound nonce or live stage identity did not match")
        self.verified_mount = candidate
        self.report.update({"ownershipVerified": True, "ownership": result})
        # Preserve ownership before checking capability, so failed RO/RW gates
        # still permit normal cleanup of this proven mount.
        expected_ro = self.phase == "ro"
        if (result.get("effectiveReadOnly") is not expected_ro
                or bool(candidate["flags"] & REFERENCE.MNT_RDONLY) != expected_ro):
            raise LabError("Effective mount access differs from this fixed stage")
        self.report["checks"].append("Live owned NTFS server, exact image source, descriptor FSID and nonce match")

    def workload(self):
        self.require_current_ownership()
        kind = "exercise" if self.phase == "rw" else "verify"
        self.report["mountedWriteWorkloadRequested"] = self.phase == "rw"
        result = self.run_worker(kind, self.verified_mount)
        self.require_current_ownership()
        if (result.get("status") != "passed" or result.get("verifiedMount") != self.verified_mount
                or result.get("effectiveReadOnly") is not (self.phase == "ro")
                or result.get("fixtureSHA256") != hashlib.sha256(self.expected).hexdigest()
                or not isinstance(result.get("manifest"), dict)):
            raise LabError("Fixture worker did not return matching verified stage evidence")
        self.report.update({"workloadPassed": True, "fixtureResult": result})
        return result["manifest"]

    def cleanup(self):
        # NTFS may finish metadata work after the OS removes the mount. A proven
        # stage must drain naturally; terminating it can interrupt that work.
        self.report["cleanupRequired"] = True
        self.report["cleanUnmount"] = False
        try:
            entry = self.entry()
            vanished = entry is None and self.verified_mount is not None
            clean_unmount = False
            if entry is not None:
                self.report["observedMountAtCleanup"] = entry
                if (self.verified_mount is None or entry != self.verified_mount
                        or self.server is None or self.server.poll() is not None):
                    raise LabError("Unknown or changed mount retained without server termination")
                REFERENCE.validate_identity(entry, self.mountpoint)
                result = subprocess.run(["/usr/sbin/diskutil", "unmount", str(self.mountpoint)],
                    env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                self.report["unmountOutput"] = (result.stdout + result.stderr)[-4096:].decode("utf-8", "replace")
                if result.returncode or self.entry() is not None:
                    raise LabError("Normal stage unmount failed; no force or server termination")
                clean_unmount = True
                self.report["cleanUnmount"] = True
            if self.server is not None and self.server.poll() is None:
                if self.entry() is not None:
                    raise LabError("Mount appeared before server drain; server retained")
                try:
                    self.server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if self.verified_mount is not None:
                        self.report["serverDrainPending"] = True
                        raise LabError("Owned NTFS server did not exit naturally; no hash or remount allowed")
                    # Failed launch only: there was no proven mount or workload.
                    # Recheck every same-image mount before terminating our process.
                    if self.entry() is not None:
                        raise LabError("Unverified mount appeared; server retained")
                    self.server.terminate()
                    self.server.wait(timeout=10)
                    self.report["failedLaunchServerTerminated"] = True
            if self.entry() is not None:
                raise LabError("Mount remains after server drain")
            exit_code = self.server.poll() if self.server is not None else None
            self.report.update({"cleanupRequired": False, "cleanUnmount": clean_unmount,
                                "serverExitCode": exit_code})
            if vanished:
                raise LabError("Verified stage vanished before explicit normal unmount")
            if clean_unmount and exit_code != 0:
                raise LabError("NTFS server exited unsuccessfully after normal unmount")
            if clean_unmount:
                self.report["checks"].append("Normal unmount completed and NTFS server exited naturally with status zero")
            return True
        except (OSError, LabError, subprocess.SubprocessError) as error:
            self.report["cleanupError"] = str(error)[:500]
            return False
        finally:
            if self.server_log is not None:
                self.server_log.close()

    def log_tail(self):
        if self.log_path.exists():
            descriptor = os.open(self.log_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - 8192))
                self.report["serverLogTail"] = stream.read(8192).decode("utf-8", "replace")


class WriteImageLab(BASE.KernelImageLab):
    def __init__(self, tools, report):
        self.tools, self.report = tools, report
        self.work = Path(tempfile.mkdtemp(prefix="nativol-kernel-image-rw-")).resolve()
        os.chmod(self.work, 0o700)
        self.image = self.work / "disposable.ntfs"
        self.mountpoint = Path("/Volumes") / ("Nativol-Kernel-Write-RW-" + uuid.uuid4().hex)
        self.nonce = os.urandom(32).hex()
        self.fixture_name = "fixture-" + self.nonce + ".bin"
        self.expected = bytes.fromhex(self.nonce) + b"Nativol kernel NTFS image nonce\n" + bytes(range(256)) * 256
        self.server = self.server_log = self.verified_mount = self.image_identity = self.original_hash = None
        self.stages = []
        report.update({"workDirectory": str(self.work), "imagePath": str(self.image), "imageRetained": True,
                       "cleanupRequired": False, "checks": [], "stages": []})

    def assert_detached(self):
        points = {str(self.mountpoint)} | {str(stage.mountpoint) for stage in self.stages}
        try:
            table = REFERENCE.mount_table()
        except (OSError, LabError):
            self.report["detachedStateUncertain"] = True
            raise
        if any(item["source"] == str(self.image) or item["mountPoint"] in points
               for item in table):
            self.report["detachedStateUncertain"] = True
            raise LabError("Same-image or stage mount persists; no offline hashing or remount allowed")
        if any((stage.server is not None and stage.server.poll() is None)
               or stage.report.get("inspectionWorkerStillRunning") for stage in self.stages):
            self.report["detachedStateUncertain"] = True
            raise LabError("Previous server or worker remains; image retained without offline access")
        if any(stage.server is not None and stage.server.poll() != 0 for stage in self.stages):
            self.report["offlineAccessRefused"] = True
            raise LabError("Previous NTFS server did not exit successfully; no hash or remount allowed")

    def hash_image(self):
        self.assert_detached()
        return super().hash_image()

    def run_stage(self, phase):
        stage = ImageStage(self, phase)
        self.stages.append(stage)
        self.report["stages"].append(stage.report)
        failure = None
        manifest = None
        try:
            candidate = stage.mount()
            stage.prove_ownership(candidate)
            manifest = stage.workload()
        except (Exception, KeyboardInterrupt) as error:
            failure = error
            stage.report["error"] = type(error).__name__ + ": " + str(error)[:500]
        finally:
            try:
                clean = stage.cleanup()
                stage.report["cleanupSucceeded"] = clean
            except (Exception, KeyboardInterrupt) as error:
                clean = False
                stage.report.update({"cleanupRequired": True, "cleanupError": str(error)[:500]})
            try:
                stage.log_tail()
            except OSError as error:
                failure = failure or error
                stage.report["logReadError"] = str(error)[:500]
            if not clean or not stage.report.get("cleanUnmount"):
                failure = failure or LabError("Stage did not complete an explicit normal unmount")
        if failure is not None:
            raise LabError(phase.upper() + " stage failed: " + str(failure)[:500]) from failure
        self.assert_detached()
        stage.report["status"] = "passed"
        return manifest

    def execute(self):
        self.prepare()  # Fixed mkntfs/ntfscp calls write only this new private image.
        baseline = self.original_hash
        write_manifest = self.run_stage("rw")
        after_write = self.hash_image()
        self.report.update({"imageSHA256AfterWriteUnmount": after_write,
                            "imageChangedByWriteStage": after_write != baseline})
        if after_write == baseline:
            raise LabError("Successful write workload did not change the disposable image")
        read_manifest = self.run_stage("ro")
        final_hash = self.hash_image()
        self.report.update({"imageSHA256AfterReadOnlyUnmount": final_hash,
                            "readOnlyImageUnchanged": final_hash == after_write,
                            "fixtureManifestsMatch": read_manifest == write_manifest})
        if read_manifest != write_manifest or final_hash != after_write:
            raise LabError("Read-only persistence verification changed the image or fixture manifest")
        self.report["checks"].append("Writable fixtures persisted through clean unmount and independent read-only remount")


def success_evidence(report):
    stages = report.get("stages", [])
    return (len(stages) == 2 and [stage.get("phase") for stage in stages] == ["rw", "ro"]
            and all(stage.get("status") == "passed" and stage.get("ownershipVerified")
                    and stage.get("workloadPassed") and stage.get("cleanUnmount")
                    and stage.get("serverExitCode") == 0 for stage in stages)
            and report.get("imageChangedByWriteStage") and report.get("readOnlyImageUnchanged")
            and report.get("fixtureManifestsMatch"))


def main():
    global INSPECTION_WORKER_STILL_RUNNING
    INSPECTION_WORKER_STILL_RUNNING = False
    if sys.argv[1:] not in ([], ["--execute"]):
        print("Only --execute is accepted; no image, device, target or option can be supplied.", file=sys.stderr)
        return 2
    execute = sys.argv[1:] == ["--execute"]
    report = {"schemaVersion": 1, "kind": "kernel-ntfs-write-image-diagnostic", "status": "failed",
              "createdAt": datetime.now(timezone.utc).isoformat(), "mode": "execute" if execute else "prepare",
              "backend": "kernel", "architecture": platform.machine(), "macOS": platform.mac_ver()[0],
              "physicalDeviceTargetAccepted": False, "kernelLoadingPerformed": False,
              "mountAttemptPerformed": False, "mountedWriteWorkloadRequested": False,
              "writableMountRequested": False,
              "authorizationRequested": False, "certifiesFilesystemSafety": False, "readyToExecute": False}
    lab = None
    try:
        tools, record = preflight(execute)
        report.update({"build": record, "effectiveUID": os.geteuid(),
                       "readyToExecute": bool(record["kernelReadiness"].get("backendLoaded"))})
        if not execute:
            report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = WriteImageLab(tools, report)
            lab.execute()
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            report["cleanupRequired"] = bool(report.get("detachedStateUncertain") or
                any(stage.report.get("cleanupRequired", False) for stage in lab.stages))
            report["inspectionWorkerStillRunning"] = any(
                stage.report.get("inspectionWorkerStillRunning", False) for stage in lab.stages)
            report["mountAttemptPerformed"] = any(stage.report.get("mountAttemptPerformed", False) for stage in lab.stages)
            report["mountedWriteWorkloadRequested"] = any(
                stage.report.get("mountedWriteWorkloadRequested", False) for stage in lab.stages)
            report["writableMountRequested"] = any(
                stage.phase == "rw" and stage.report.get("mountAttemptPerformed", False) for stage in lab.stages)
            if report["cleanupRequired"] or report["inspectionWorkerStillRunning"]:
                report["status"] = "failed"
            if report["status"] == "passed" and not success_evidence(report):
                report.update({"status": "failed", "error": "Required two-stage persistence evidence is incomplete"})
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
