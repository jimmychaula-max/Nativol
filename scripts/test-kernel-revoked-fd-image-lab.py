#!/usr/bin/env python3
"""Private host-file permission tests; mounts and engine execution are mocked."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_revoked_checks", Path(__file__).with_name("kernel-revoked-fd-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LAB
SPEC.loader.exec_module(LAB)


class RevokedChecks(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("Permission semantics require a non-root test process")
        self.size = 64 * 1024
        self.size_patch = mock.patch.object(LAB.FD.BASE, "IMAGE_BYTES", self.size)
        self.size_patch.start()
        self.addCleanup(self.size_patch.stop)
        self.lab = LAB.RevokedLab({}, {})
        self.addCleanup(self.cleanup)
        self.lab.image = self.lab.work / "held.ntfs"
        self.lab.held_fd = os.open(self.lab.image, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.payload = hashlib.sha256(b"private test fixture").digest() * (self.size // 32)
        self.assertEqual(os.write(self.lab.held_fd, self.payload), self.size)
        info = os.fstat(self.lab.held_fd)
        self.lab.image_identity = (info.st_dev, info.st_ino)
        self.lab.replaced_path = self.lab.work / "disposable.ntfs"
        descriptor = os.open(self.lab.replaced_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.ftruncate(descriptor, self.size)
            info = os.fstat(descriptor)
            self.lab.replacement_identity = (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)
        self.lab.renamed = True
        self.lab.assert_detached = mock.Mock()

    def cleanup(self):
        if self.lab.held_fd is not None:
            os.fchmod(self.lab.held_fd, 0o600)
            os.close(self.lab.held_fd)
        os.chmod(self.lab.work, 0o700)
        shutil.rmtree(self.lab.work)

    def revoke(self):
        self.lab.verify_image()
        os.fchmod(self.lab.held_fd, 0)
        self.lab.permissions_revoked = True
        self.lab.verify_image()

    def test_real_path_permission_denial_and_held_descriptor_readback(self):
        self.revoke()
        self.lab.check_denied("before-mount")
        self.assertEqual(self.lab.hash_image(), hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.lab.report["permissionChecks"], [{"phase": "before-mount", "imageMode": "000",
            "readOnlyPathOpenDenied": True, "readWritePathOpenDenied": True}])
        self.lab.restore_permissions()
        self.assertEqual(stat.S_IMODE(self.lab.image.stat().st_mode), 0o600)
        self.assertTrue(self.lab.report["permissionsRestored"])
        self.assertEqual(self.lab.image.read_bytes(), self.payload)

    def test_revoked_guard_refuses_changed_mode_or_private_directory(self):
        self.revoke()
        os.fchmod(self.lab.held_fd, 0o400)
        with self.assertRaises(LAB.LabError):
            self.lab.verify_image()
        os.fchmod(self.lab.held_fd, 0)
        os.chmod(self.lab.work, 0o755)
        with self.assertRaises(LAB.LabError):
            self.lab.verify_image()

    def test_descriptor_replaced_by_other_inode_is_rejected(self):
        self.revoke()
        original = self.lab.held_fd
        replacement = os.open(self.lab.replaced_path, os.O_RDWR | os.O_NOFOLLOW)
        self.lab.held_fd = replacement
        try:
            with self.assertRaises(LAB.LabError):
                self.lab.verify_image()
        finally:
            self.lab.held_fd = original
            os.close(replacement)

    def test_symlink_image_and_extra_hardlink_are_rejected(self):
        self.revoke()
        moved = self.lab.work / "moved"
        self.lab.image.rename(moved)
        self.lab.image.symlink_to(moved.name)
        with self.assertRaises(LAB.LabError):
            self.lab.verify_image()
        self.lab.image.unlink()
        moved.rename(self.lab.image)
        os.link(self.lab.image, self.lab.work / "extra-link")
        with self.assertRaises(LAB.LabError):
            self.lab.verify_image()

    def test_hash_refuses_short_read_and_handles_partial_reads(self):
        self.revoke()
        original = os.pread
        with mock.patch.object(LAB.os, "pread", side_effect=lambda fd, size, offset: original(fd, min(size, 127), offset)):
            self.assertEqual(self.lab.hash_image(), hashlib.sha256(self.payload).hexdigest())
        with mock.patch.object(LAB.os, "pread", return_value=b""):
            with self.assertRaises(LAB.LabError):
                self.lab.hash_image()

    def test_uncertain_detachment_prevents_restoring_permissions(self):
        self.revoke()
        self.lab.assert_detached.side_effect = LAB.LabError("still mounted")
        with mock.patch.object(LAB.os, "fchmod") as chmod:
            with self.assertRaises(LAB.LabError):
                self.lab.restore_permissions()
        chmod.assert_not_called()
        self.assertTrue(self.lab.permissions_revoked)
        self.assertEqual(stat.S_IMODE(os.fstat(self.lab.held_fd).st_mode), 0)

    def test_unexpected_path_open_success_is_not_accepted(self):
        self.revoke()
        with mock.patch.object(LAB.os, "open", return_value=os.dup(self.lab.held_fd)):
            with self.assertRaises(LAB.LabError):
                self.lab.check_denied("before-rw")
        self.assertFalse(self.lab.report["permissionChecks"])

    def test_each_stage_is_wrapped_with_denial_checks(self):
        self.revoke()
        with mock.patch.object(LAB.SHORT.ShortnamesLab, "run_stage", return_value={"fixture": "ok"}) as stage:
            self.assertEqual(self.lab.run_stage("rw"), {"fixture": "ok"})
        stage.assert_called_once_with("rw")
        self.assertEqual([item["phase"] for item in self.lab.report["permissionChecks"]],
                         ["before-rw", "after-rw-unmount"])
        self.assertEqual(stat.S_IMODE(os.fstat(self.lab.held_fd).st_mode), 0)

    def test_permission_evidence_requires_all_stages_and_restored_mode(self):
        report = {"revokedHeldImageUnchanged": True, "permissionsRestored": True,
            "permissionChecks": [{"phase": phase, "imageMode": "000", "readOnlyPathOpenDenied": True,
                "readWritePathOpenDenied": True} for phase in ("before-mount", "before-rw",
                    "after-rw-unmount", "before-ro", "after-ro-unmount")]}
        self.assertTrue(LAB.permission_evidence(report))
        report["permissionsRestored"] = False
        self.assertFalse(LAB.permission_evidence(report))
        report["permissionsRestored"] = True
        report["permissionChecks"][-1]["readWritePathOpenDenied"] = False
        self.assertFalse(LAB.permission_evidence(report))

    def test_default_prepare_does_not_create_image_or_chmod(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["revoked-lab"]), \
                mock.patch.object(LAB.WRITE, "preflight", return_value=({}, {"scriptSHA256": {}})) as preflight, \
                mock.patch.object(LAB, "RevokedLab") as factory, \
                mock.patch.object(LAB.os, "fchmod") as chmod, contextlib.redirect_stdout(output):
            result = LAB.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "prepared")
        preflight.assert_called_once_with(False)
        factory.assert_not_called()
        chmod.assert_not_called()

    def test_arbitrary_target_refused_before_preflight(self):
        with mock.patch.object(sys, "argv", ["revoked-lab", "/dev/disk2s1"]), \
                mock.patch.object(LAB.WRITE, "preflight") as preflight, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(LAB.main(), 2)
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
