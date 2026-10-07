#!/usr/bin/env python3
"""Mocked administrator harness checks: never mounts or starts an engine."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_admin_lab", Path(__file__).with_name("fskit-admin-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class AdminImageLabChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nativol-admin-harness-check-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.mountpoint = self.work / "mount"
        self.mountpoint.mkdir()
        self.payload = b"Mock unpredictable fixture, never a real device"
        (self.mountpoint / "nonce.bin").write_bytes(self.payload)
        self.entry = {"source": "/dev/disk999", "mountPoint": str(self.mountpoint),
                      "filesystemType": "macfuse-local", "flags": LAB.MNT_RDONLY,
                      "extendedFlags": LAB.MNT_EXT_FSKIT, "ownerUID": 0, "filesystemID": [88, 99]}

    def fake_lab(self):
        lab = LAB.AdminImageLab.__new__(LAB.AdminImageLab)
        lab.work, lab.mountpoint = self.work, self.mountpoint
        lab.image = self.work / "uncreated.ntfs"
        lab.fixture_name = "nonce.bin"
        lab.image_identity = lab.original_hash = lab.server_log = lab.verified_mount = None
        lab.server = mock.Mock()
        lab.server.poll.return_value = None
        lab.report = {"cleanupRequired": True, "imageRetained": True, "checks": []}
        return lab

    def test_nonroot_or_any_argument_is_rejected_before_runtime_verification(self):
        for argv, uid in [(["script", "/dev/disk1"], 0), (["script", "--ro"], 0), (["script"], 501)]:
            with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(LAB.os, "getuid", return_value=uid), \
                    mock.patch.object(LAB, "verified_file") as verify, self.assertRaises(LAB.LabError):
                LAB.validate_invocation()
            verify.assert_not_called()

    def test_nonisolated_python_is_rejected_before_runtime_verification(self):
        for isolated, no_site in [(False, True), (True, False)]:
            with mock.patch.object(sys, "argv", ["script"]), \
                    mock.patch.object(LAB.os, "getuid", return_value=0), \
                    mock.patch.object(LAB.os, "geteuid", return_value=0), \
                    mock.patch.object(LAB.sys, "flags", mock.Mock(isolated=isolated, no_site=no_site)), \
                    mock.patch.object(LAB, "verified_file") as verify, self.assertRaises(LAB.LabError):
                LAB.validate_invocation()
            verify.assert_not_called()

    def test_virtual_device_source_is_accepted_only_after_descriptor_nonce_read(self):
        opened_flags = []
        real_open = os.open

        def record(path, flags, *args, **kwargs):
            opened_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                mock.patch.object(LAB.os, "open", side_effect=record):
            result = LAB.inspect_fixture(self.mountpoint, "nonce.bin", self.payload, self.entry)
        self.assertEqual(result["verifiedMount"], self.entry)
        self.assertTrue(result["effectiveReadOnly"])
        self.assertTrue(all(flags & os.O_NOFOLLOW for flags in opened_flags))
        self.assertTrue(all(not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) for flags in opened_flags))

    def test_ro_capability_does_not_prevent_nonce_ownership_inspection(self):
        candidate = dict(self.entry, flags=0)
        with mock.patch.object(LAB, "descriptor_mount", return_value=candidate):
            result = LAB.inspect_fixture(self.mountpoint, "nonce.bin", self.payload, candidate)
        self.assertEqual(result["verifiedMount"], candidate)
        self.assertFalse(result["effectiveReadOnly"])

    def test_wrong_nonce_or_changed_descriptor_cannot_prove_ownership(self):
        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                self.assertRaisesRegex(LAB.LabError, "does not match"):
            LAB.inspect_fixture(self.mountpoint, "nonce.bin", b"x" * len(self.payload), self.entry)
        changed = dict(self.entry, filesystemID=[1, 2])
        with mock.patch.object(LAB, "descriptor_mount", return_value=changed), \
                self.assertRaisesRegex(LAB.LabError, "identity differs"):
            LAB.inspect_fixture(self.mountpoint, "nonce.bin", self.payload, self.entry)

    def test_backend_mountpoint_and_owner_are_required(self):
        for change in [{"extendedFlags": 0}, {"filesystemType": "apfs"}, {"ownerUID": 501},
                       {"mountPoint": "/Volumes/Valuable"}, {"filesystemID": [0, 0]}]:
            with self.subTest(change=change), self.assertRaises(LAB.LabError):
                LAB.validate_identity(dict(self.entry, **change), self.mountpoint)

    def test_missing_ro_sets_verified_ownership_before_failing(self):
        lab = self.fake_lab()
        candidate = dict(self.entry, flags=0)
        receiver, sender, worker, context = (mock.Mock() for _ in range(4))
        receiver.poll.return_value = True
        receiver.recv.return_value = {"verifiedMount": candidate, "effectiveReadOnly": False, "fixtureSHA256": "fake"}
        worker.is_alive.return_value = False
        worker.exitcode = 0
        context.Pipe.return_value = (receiver, sender)
        context.Process.return_value = worker
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(lab, "entry", return_value=candidate), \
                self.assertRaisesRegex(LAB.LabError, "MNT_RDONLY is missing"):
            lab.inspect(self.payload, candidate)
        self.assertEqual(lab.verified_mount, candidate)
        self.assertTrue(lab.report["ownershipVerified"])
        self.assertNotIn("inspectionPassed", lab.report)

    def test_verified_nonreadonly_mount_can_be_normally_unmounted(self):
        lab = self.fake_lab()
        lab.verified_mount = dict(self.entry, flags=0)
        result = subprocess.CompletedProcess([], 0, b"Unmounted", b"")
        with mock.patch.object(lab, "entry", side_effect=[lab.verified_mount, None, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=result) as run:
            self.assertTrue(lab.cleanup())
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/diskutil", "unmount", str(self.mountpoint)])
        self.assertTrue(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertTrue(lab.report["imageRetained"])
        self.assertNotIn("status", lab.report)

    def test_unverified_or_changed_mount_never_unmounts_or_terminates_driver(self):
        for verified in [None, dict(self.entry, filesystemID=[66, 77])]:
            lab = self.fake_lab()
            lab.verified_mount = verified
            with mock.patch.object(lab, "entry", return_value=self.entry), \
                    mock.patch.object(LAB.subprocess, "run") as run:
                self.assertFalse(lab.cleanup())
            run.assert_not_called()
            lab.server.terminate.assert_not_called()
            self.assertTrue(lab.report["cleanupRequired"])

    def test_normal_unmount_failure_retains_server(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        with mock.patch.object(lab, "entry", return_value=self.entry), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"Busy")):
            self.assertFalse(lab.cleanup())
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["cleanupRequired"])

    def test_vanished_verified_mount_fails_but_has_no_active_cleanup(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        lab.server.poll.return_value = 0
        with mock.patch.object(lab, "entry", return_value=None), mock.patch.object(LAB.subprocess, "run") as run:
            self.assertFalse(lab.cleanup())
        run.assert_not_called()
        lab.server.terminate.assert_not_called()
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertTrue(lab.report["imageRetained"])
        self.assertIn("vanished", lab.report["cleanupError"])

    def test_report_save_failure_prevents_overall_success(self):
        non_directory = self.work / "not-a-directory"
        non_directory.write_bytes(b"retained")

        def fake_factory(bundle, tools, environment, report):
            report["cleanUnmount"] = True
            fake = mock.Mock()
            fake.work = non_directory
            fake.cleanup.return_value = True
            return fake

        output = io.StringIO()
        with mock.patch.object(LAB, "validate_invocation", return_value=(self.work, {}, {}, {"DYLD_LIBRARY_PATH": "private"})), \
                mock.patch.object(LAB, "AdminImageLab", side_effect=fake_factory), \
                mock.patch.object(LAB.os, "umask"), contextlib.redirect_stdout(output):
            code = LAB.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("reportSaveError", report)

    def test_only_explicit_stuck_worker_state_selects_hard_exit(self):
        with mock.patch.object(LAB.os, "_exit") as hard_exit, \
                mock.patch.object(LAB.sys.stdout, "flush") as stdout, \
                mock.patch.object(LAB.sys.stderr, "flush") as stderr:
            LAB.finish_supervisor(0, True)
            hard_exit.assert_called_once_with(1)
            stdout.assert_called_once()
            stderr.assert_called_once()
        with mock.patch.object(LAB.os, "_exit") as hard_exit, self.assertRaises(SystemExit) as raised:
            LAB.finish_supervisor(1, False)
        self.assertEqual(raised.exception.code, 1)
        hard_exit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
