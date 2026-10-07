#!/usr/bin/env python3
"""Mocked FSKit harness tests. Never starts an engine or mounts a filesystem."""
import contextlib
import errno
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("fskit-image-lab.py")
SPEC = importlib.util.spec_from_file_location("nativol_fskit_image_lab", SCRIPT)
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class FskitImageLabTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="nativol-fskit-harness-check-")
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.image = self.work / "disposable.ntfs"
        self.mountpoint = self.work / "mount"
        self.mountpoint.mkdir()
        self.expected = b"Unpredictable fixture stand-in\n"
        (self.mountpoint / "fixture.bin").write_bytes(self.expected)
        self.entry = {"source": str(self.image), "mountPoint": str(self.mountpoint),
                      "filesystemType": "macfuse-local", "flags": LAB.MNT_RDONLY,
                      "extendedFlags": LAB.MNT_EXT_FSKIT, "ownerUID": os.getuid(),
                      "filesystemID": [1234, 5678]}

    def fake_lab(self):
        private = self.work / "private"
        private.mkdir()
        with mock.patch.object(LAB.tempfile, "mkdtemp", return_value=str(private)):
            lab = LAB.ImageLab({}, {})
        lab.mountpoint = self.mountpoint
        lab.image = self.image
        lab.server = mock.Mock()
        lab.server.poll.return_value = None
        return lab

    def test_unverified_source_backend_or_writable_mount_cannot_pass(self):
        variants = [
            {"source": "/dev/disk2s1"},
            {"source": str(self.work / "another-image")},
            {"mountPoint": "/Volumes/Valuable"},
            {"filesystemType": "apfs"},
            {"extendedFlags": 0},
            {"flags": 0},
        ]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(LAB.LabError):
                LAB.validate_mount(dict(self.entry, **change), self.image, self.mountpoint)
        LAB.validate_mount(self.entry, self.image, self.mountpoint)

    def test_only_erofs_counts_as_readonly_enforcement(self):
        real_open = os.open
        attempts = []

        def reject_creation(path, flags, *args, **kwargs):
            if str(path).startswith("write-must-fail-"):
                attempts.append((path, flags, kwargs))
                raise OSError(errno.EROFS, "Read-only filesystem")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                mock.patch.object(LAB.os, "open", side_effect=reject_creation):
            checks = LAB.inspect_fixture(self.image, self.mountpoint, self.expected, self.entry)
        self.assertEqual(len(checks), 2)
        self.assertEqual(len(attempts), 1)
        self.assertIn("dir_fd", attempts[0][2], "Creation must stay bound to verified filesystem descriptor")
        self.assertTrue(attempts[0][1] & os.O_EXCL)
        self.assertTrue(attempts[0][1] & os.O_NOFOLLOW)

        def denied_creation(path, flags, *args, **kwargs):
            if str(path).startswith("write-must-fail-"):
                raise OSError(errno.EACCES, "Permission denied")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                mock.patch.object(LAB.os, "open", side_effect=denied_creation), \
                self.assertRaisesRegex(LAB.LabError, "other than EROFS"):
            LAB.inspect_fixture(self.image, self.mountpoint, self.expected, self.entry)

    def test_corrupt_fixture_stops_before_any_creation_attempt(self):
        (self.mountpoint / "fixture.bin").write_bytes(b"Wrong data")
        real_open = os.open

        def guard_creation(path, flags, *args, **kwargs):
            self.assertFalse(str(path).startswith("write-must-fail-"))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                mock.patch.object(LAB.os, "open", side_effect=guard_creation), \
                self.assertRaisesRegex(LAB.LabError, "readback mismatch"):
            LAB.inspect_fixture(self.image, self.mountpoint, self.expected, self.entry)

    def test_replaced_filesystem_stops_before_fixture_access(self):
        replacement = dict(self.entry, filesystemID=[9999, 1])
        with mock.patch.object(LAB, "descriptor_mount", return_value=replacement), \
                self.assertRaisesRegex(LAB.LabError, "identity changed"):
            LAB.inspect_fixture(self.image, self.mountpoint, self.expected, self.entry)

    def test_unknown_mount_is_retained_without_unmount_or_server_termination(self):
        lab = self.fake_lab()
        with mock.patch.object(lab, "entry", return_value=self.entry), \
                mock.patch.object(LAB.subprocess, "run") as process:
            self.assertFalse(lab.cleanup())
        process.assert_not_called()
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["cleanupRequired"])
        self.assertTrue(lab.report["imageRetained"])

    def test_changed_mount_is_not_unmounted_using_previous_verification(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        changed = dict(self.entry, source="/dev/disk2s1")
        with mock.patch.object(lab, "entry", return_value=changed), \
                mock.patch.object(LAB.subprocess, "run") as process:
            self.assertFalse(lab.cleanup())
        process.assert_not_called()
        lab.server.terminate.assert_not_called()

    def test_busy_unmount_retains_image_and_running_server(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        with mock.patch.object(lab, "entry", return_value=self.entry), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as process:
            self.assertFalse(lab.cleanup())
        self.assertEqual(process.call_args.args[0], ["/usr/sbin/diskutil", "unmount", str(self.mountpoint)])
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["imageRetained"])

    def test_unmount_timeout_retains_image_and_running_server(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        with mock.patch.object(lab, "entry", return_value=self.entry), \
                mock.patch.object(LAB.subprocess, "run", side_effect=subprocess.TimeoutExpired("diskutil", 30)):
            self.assertFalse(lab.cleanup())
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["cleanupRequired"])

    def test_normal_unmount_must_disappear_before_server_is_stopped(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        with mock.patch.object(lab, "entry", side_effect=[self.entry, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertTrue(lab.cleanup())
        lab.server.terminate.assert_called_once()
        self.assertTrue(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["cleanupRequired"])

    def test_vanished_verified_mount_is_not_a_successful_unmount(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        lab.report["inspectionPassed"] = True
        lab.server.poll.return_value = 0
        self.image.write_bytes(b"Retained image stand-in")
        lab.identity = (self.image.stat().st_dev, self.image.stat().st_ino)
        lab.original_hash = LAB.digest(self.image)
        with mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(lab, "verify_image"), \
                mock.patch.object(LAB.subprocess, "run") as process:
            self.assertFalse(lab.cleanup())
        process.assert_not_called()
        lab.server.terminate.assert_not_called()
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["cleanupRequired"], "Known absent mount with exited server needs no active cleanup")
        self.assertEqual(lab.report["imageSHA256AfterUnmount"], lab.original_hash)
        self.assertTrue(lab.report["imageRetained"])
        self.assertTrue(self.image.exists())
        self.assertIn("disappeared", lab.report["cleanupError"])
        self.assertNotIn("Normal unmount and unchanged image SHA-256", lab.report["checks"])

    def test_vanished_mount_stops_owned_server_but_keeps_lifecycle_failure(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        lab.report["inspectionPassed"] = True
        with mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(LAB.subprocess, "run") as process:
            self.assertFalse(lab.cleanup())
        process.assert_not_called()
        lab.server.terminate.assert_called_once()
        lab.server.wait.assert_called_once_with(timeout=10)
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertTrue(lab.report["imageRetained"])

    def test_no_target_or_writable_mode_cli_is_available(self):
        for extra in [["--device", "/dev/disk2s1"], ["--image", str(self.image)],
                      ["--mountpoint", str(self.mountpoint)], ["--mode", "rw"], ["--options", "rw"]]:
            argv = [str(SCRIPT), "--engine-prefix", str(self.work), *extra]
            with self.subTest(extra=extra), mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(LAB, "tools_for") as tools, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                LAB.main()
            tools.assert_not_called()

    def test_root_is_rejected_before_dependency_loading(self):
        argv = [str(SCRIPT), "--engine-prefix", str(self.work)]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(LAB.os, "geteuid", return_value=0), \
                mock.patch.object(LAB, "tools_for") as tools, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            LAB.main()
        tools.assert_not_called()

    def test_zero_driver_exit_without_a_mount_is_still_failure(self):
        # Observed on Sequoia: MFMount refused the disabled extension but the
        # NTFS driver exited 0. Process status must never certify a mount.
        lab = self.fake_lab()
        lab.mountpoint = lab.work / "absent-mountpoint"
        lab.tools = {"ntfs-3g": self.work / "unused-driver"}
        server = lab.server
        server.poll.return_value = 0
        try:
            with mock.patch.object(lab, "verify_image"), \
                    mock.patch.object(lab, "entry", return_value=None), \
                    mock.patch.object(LAB.subprocess, "Popen", return_value=server), \
                    self.assertRaisesRegex(LAB.LabError, "before a verified mount"):
                lab.mount()
            self.assertEqual(lab.report["driverExitCode"], 0)
            self.assertIsNone(lab.verified_mount)
            self.assertNotIn("inspectionPassed", lab.report)
        finally:
            if lab.server_log is not None:
                lab.server_log.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
