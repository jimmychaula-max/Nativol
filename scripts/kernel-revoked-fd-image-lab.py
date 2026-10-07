#!/usr/bin/env python3
"""Default prepare-only: use a held FD after revoking its fresh image's mode.

Only the inherited harness's newly created private regular image is chmodded.
No image path, physical target, administrator action or custom option is accepted.
Actual RW/RO stages keep mode 000; guarded detached cleanup restores mode 0600.
"""
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


SPEC = importlib.util.spec_from_file_location(
    "nativol_revoked_shortnames_lab", Path(__file__).with_name("kernel-shortnames-image-lab.py"))
SHORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SHORT
SPEC.loader.exec_module(SHORT)
FD, WRITE, REFERENCE, LabError = SHORT.FD, SHORT.WRITE, SHORT.REFERENCE, SHORT.LabError
STUCK = False


class RevokedLab(SHORT.ShortnamesLab):
    def __init__(self, tools, report):
        self.permissions_revoked = False
        super().__init__(tools, report)
        info = self.work.lstat()
        self.work_identity = (info.st_dev, info.st_ino)
        report.update({"permissionChecks": [], "permissionsRestored": False})

    def verify_image(self):
        if not self.permissions_revoked:
            return super().verify_image()
        # This exception to the inherited mode-0600 check is confined to the
        # exact held inode created by this instance, while its explicit state
        # requires mode 000. It never permits an arbitrary inaccessible path.
        directory = self.work.lstat()
        info, opened = self.image.lstat(), os.fstat(self.held_fd)
        replacement = self.replaced_path.lstat()
        if (not self.renamed or self.image != self.work / "held.ntfs"
                or self.work.resolve(strict=True) != self.work
                or not self.work.name.startswith("nativol-kernel-image-rw-")
                or not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) != 0o700
                or (directory.st_dev, directory.st_ino) != self.work_identity
                or any(not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
                    or item.st_nlink != 1 or item.st_size != FD.BASE.IMAGE_BYTES
                    or stat.S_IMODE(item.st_mode) != 0
                    or (item.st_dev, item.st_ino) != self.image_identity for item in (info, opened))
                or not stat.S_ISREG(replacement.st_mode) or replacement.st_uid != os.getuid()
                or replacement.st_nlink != 1 or replacement.st_size != FD.BASE.IMAGE_BYTES
                or stat.S_IMODE(replacement.st_mode) != 0o600
                or (replacement.st_dev, replacement.st_ino) != self.replacement_identity):
            raise LabError("Revoked fresh image, held descriptor or private namespace identity changed")

    def hash_image(self):
        if not self.permissions_revoked:
            return super().hash_image()
        self.assert_detached()
        self.verify_image()
        digest, offset = hashlib.sha256(), 0
        while offset < FD.BASE.IMAGE_BYTES:
            self.verify_image()
            data = os.pread(self.held_fd, min(1024 * 1024, FD.BASE.IMAGE_BYTES - offset), offset)
            if not data:
                raise LabError("Short read from the revoked held image")
            digest.update(data)
            offset += len(data)
        self.verify_image()
        return digest.hexdigest()

    def check_denied(self, phase):
        self.assert_detached()
        self.verify_image()
        if not self.permissions_revoked or os.geteuid() == 0:
            raise LabError("Permission regression requires a non-root process and mode 000")
        checks = {}
        for label, flags in (("readOnly", os.O_RDONLY), ("readWrite", os.O_RDWR)):
            try:
                descriptor = os.open(self.image, flags | os.O_NOFOLLOW)
            except OSError as error:
                if error.errno != errno.EACCES:
                    raise LabError("Fresh image path open did not fail with EACCES") from error
                checks[label + "PathOpenDenied"] = True
            else:
                os.close(descriptor)
                raise LabError("Fresh image path could still be opened after revocation")
        self.verify_image()
        self.report["permissionChecks"].append({"phase": phase, "imageMode": "000", **checks})

    def prepare(self):
        super().prepare()
        self.mutation_guard()
        os.fchmod(self.held_fd, 0)
        self.permissions_revoked = True
        self.verify_image()
        self.check_denied("before-mount")
        self.report["revokedHeldImageUnchanged"] = self.hash_image() == self.original_hash
        if not self.report["revokedHeldImageUnchanged"]:
            raise LabError("Permission revocation changed the NTFS image bytes")

    def run_stage(self, phase):
        self.check_denied("before-" + phase)
        result = super().run_stage(phase)
        self.check_denied("after-" + phase + "-unmount")
        return result

    def restore_permissions(self):
        if not self.permissions_revoked:
            return
        # An unknown mount, a live worker/server or unsuccessful server exit
        # prevents even retention cleanup from mutating the held image inode.
        self.assert_detached()
        self.verify_image()
        os.fchmod(self.held_fd, 0o600)
        self.permissions_revoked = False
        self.verify_image()
        self.report["permissionsRestored"] = True


def permission_evidence(report):
    checks = report.get("permissionChecks", [])
    return (report.get("revokedHeldImageUnchanged") is True
            and report.get("permissionsRestored") is True
            and [item.get("phase") for item in checks] == ["before-mount", "before-rw",
                "after-rw-unmount", "before-ro", "after-ro-unmount"]
            and all(item.get("imageMode") == "000" and item.get("readOnlyPathOpenDenied") is True
                and item.get("readWritePathOpenDenied") is True for item in checks))


def main():
    global STUCK
    STUCK = False
    if sys.argv[1:] not in ([], ["--execute"]):
        print("Only --execute is accepted; no image or physical target can be supplied.", file=sys.stderr)
        return 2
    report = {"schemaVersion": 1, "kind": "kernel-revoked-held-fd-image", "status": "failed",
        "physicalDeviceTargetAccepted": False, "authorizationRequested": False,
        "kernelLoadingPerformed": False, "mountAttemptPerformed": False}
    lab = None
    try:
        tools, evidence = WRITE.preflight(bool(sys.argv[1:]))
        for name in ("kernel-revoked-fd-image-lab.py", "kernel-shortnames-image-lab.py",
                     "kernel-fd-image-lab.py"):
            evidence["scriptSHA256"][name] = REFERENCE.digest(Path(__file__).with_name(name))
        report["build"] = evidence
        if not sys.argv[1:]:
            report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = RevokedLab(tools, report)
            lab.execute()
            if not WRITE.success_evidence(report) or not SHORT.flag_evidence(report):
                raise LabError("Incomplete lifecycle or informational-flag preservation evidence")
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            STUCK = any(stage.report.get("inspectionWorkerStillRunning") for stage in lab.stages)
            report["cleanupRequired"] = bool(report.get("detachedStateUncertain") or STUCK
                or any(stage.report.get("cleanupRequired") for stage in lab.stages))
            report["mountAttemptPerformed"] = any(stage.report.get("mountAttemptPerformed") for stage in lab.stages)
            if not report["cleanupRequired"]:
                try:
                    lab.restore_permissions()
                except (Exception, KeyboardInterrupt) as error:
                    report.update({"cleanupRequired": True, "permissionRestoreError": str(error)[:500]})
            if report["cleanupRequired"]:
                report["status"] = "failed"
                report["permissionsRestoreDeferred"] = lab.permissions_revoked
            elif lab.held_fd is not None:
                os.close(lab.held_fd)
            if report["status"] == "passed" and not permission_evidence(report):
                report.update({"status": "failed", "error": "Incomplete permission-revocation evidence"})
            try:
                with (lab.work / "revoked-fd-report.json").open("x") as stream:
                    json.dump(report, stream, indent=2)
            except OSError as error:
                report.update({"status": "failed", "reportSaveError": str(error)[:300]})
        print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    REFERENCE.finish_supervisor(main(), STUCK)
