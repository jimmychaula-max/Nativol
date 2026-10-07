#!/usr/bin/env python3
"""Mocked reference harness checks; never mounts or starts a FUSE process."""
import contextlib
import hashlib
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
    "nativol_reference_lab", Path(__file__).with_name("fskit-reference-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class ReferenceLabChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-reference-harness-check-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.mountpoint = self.work / "mount"
        self.mountpoint.mkdir()
        self.payload = b"Nativol reference fixture, only a mock regular file\n"
        (self.mountpoint / "fixture.txt").write_bytes(self.payload)
        self.entry = {"source": "/dev/disk999", "mountPoint": str(self.mountpoint),
                      "filesystemType": "macfuse-local", "flags": LAB.MNT_RDONLY,
                      "extendedFlags": LAB.MNT_EXT_FSKIT, "ownerUID": os.getuid(),
                      "filesystemID": [88, 99]}

    def fake_lab(self):
        lab = LAB.ReferenceLab.__new__(LAB.ReferenceLab)
        lab.work, lab.mountpoint = self.work, self.mountpoint
        lab.binary = self.work / "never-executed"
        lab.nonce, lab.fixture_name, lab.expected = "a" * 64, "fixture.txt", self.payload
        lab.server_log = lab.verified_mount = None
        lab.server = mock.Mock()
        lab.server.poll.return_value = None
        lab.report = {"cleanupRequired": True, "checks": []}
        return lab

    def test_root_mixed_identity_and_target_arguments_refused_before_build_access(self):
        cases = [(["script"], 0, 0), (["script"], 501, 0),
                 (["script", "/dev/disk1"], 501, 501),
                 (["script", "--mountpoint", "/Volumes/Valuable"], 501, 501)]
        for argv, uid, euid in cases:
            with self.subTest(argv=argv, uid=uid, euid=euid), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(LAB.os, "getuid", return_value=uid), \
                    mock.patch.object(LAB.os, "geteuid", return_value=euid), \
                    mock.patch.object(LAB, "digest") as digest, self.assertRaises(LAB.LabError):
                LAB.validate_invocation()
            digest.assert_not_called()

    def test_python_startup_isolation_is_required_before_build_access(self):
        for isolated, no_site in [(False, True), (True, False)]:
            with mock.patch.object(sys, "argv", ["script"]), \
                    mock.patch.object(LAB.os, "getuid", return_value=501), \
                    mock.patch.object(LAB.os, "geteuid", return_value=501), \
                    mock.patch.object(LAB.sys, "flags", mock.Mock(isolated=isolated, no_site=no_site)), \
                    mock.patch.object(LAB, "digest") as digest, self.assertRaises(LAB.LabError):
                LAB.validate_invocation()
            digest.assert_not_called()

    def test_nonobject_build_manifest_fails_before_any_process_launch(self):
        directory = self.work / ".local-engine/reference/x86_64"
        directory.mkdir(parents=True)
        binary = directory / "fskit-reference"
        binary.write_bytes(b"Never executed: invalid manifest")
        binary.chmod(0o700)
        (directory / "manifest.json").write_text("[]\n")
        with mock.patch.object(LAB, "ROOT", self.work), \
                mock.patch.object(sys, "argv", ["script"]), \
                mock.patch.object(LAB.platform, "machine", return_value="x86_64"), \
                mock.patch.object(LAB.sys, "flags", mock.Mock(isolated=True, no_site=True)), \
                mock.patch.object(LAB.subprocess, "run") as command, \
                mock.patch.object(LAB.subprocess, "Popen") as launch, \
                self.assertRaises(LAB.LabError):
            LAB.validate_invocation()
        command.assert_not_called()
        launch.assert_not_called()

    def test_nonce_read_is_descriptor_bound_and_performs_no_write_open(self):
        flags_seen = []
        real_open = os.open

        def record(path, flags, *args, **kwargs):
            flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                mock.patch.object(LAB.os, "open", side_effect=record):
            result = LAB.inspect_fixture(self.mountpoint, "fixture.txt", self.payload, self.entry)
        self.assertEqual(result["verifiedMount"], self.entry)
        self.assertEqual(result["fixtureSHA256"], hashlib.sha256(self.payload).hexdigest())
        self.assertTrue(result["effectiveReadOnly"])
        self.assertTrue(flags_seen)
        self.assertTrue(all(flags & os.O_NOFOLLOW for flags in flags_seen))
        self.assertTrue(all(not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) for flags in flags_seen))

    def test_wrong_nonce_or_changed_descriptor_does_not_establish_ownership(self):
        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), \
                self.assertRaisesRegex(LAB.LabError, "Nonce fixture"):
            LAB.inspect_fixture(self.mountpoint, "fixture.txt", b"x" * len(self.payload), self.entry)
        changed = dict(self.entry, filesystemID=[1, 2])
        with mock.patch.object(LAB, "descriptor_mount", return_value=changed), \
                self.assertRaisesRegex(LAB.LabError, "descriptor differs"):
            LAB.inspect_fixture(self.mountpoint, "fixture.txt", self.payload, self.entry)

    def test_symlinked_fixture_cannot_supply_ownership_evidence(self):
        (self.mountpoint / "linked.txt").symlink_to(self.mountpoint / "fixture.txt")
        with mock.patch.object(LAB, "descriptor_mount", return_value=self.entry), self.assertRaises(OSError):
            LAB.inspect_fixture(self.mountpoint, "linked.txt", self.payload, self.entry)

    def test_readonly_capability_is_separate_from_ownership(self):
        candidate = dict(self.entry, flags=0)
        with mock.patch.object(LAB, "descriptor_mount", return_value=candidate):
            result = LAB.inspect_fixture(self.mountpoint, "fixture.txt", self.payload, candidate)
        self.assertEqual(result["verifiedMount"], candidate)
        self.assertFalse(result["effectiveReadOnly"])

    def test_wrong_backend_owner_mountpoint_or_zero_fsid_is_refused(self):
        for change in [{"extendedFlags": 0}, {"filesystemType": "apfs"},
                       {"ownerUID": os.getuid() + 1}, {"mountPoint": "/Volumes/Valuable"},
                       {"filesystemID": [0, 0]}]:
            with self.subTest(change=change), self.assertRaises(LAB.LabError):
                LAB.validate_identity(dict(self.entry, **change), self.mountpoint)

    def fake_worker_context(self, result, stuck=False):
        receiver, sender, worker, context = (mock.Mock() for _ in range(4))
        receiver.poll.return_value = True
        receiver.recv.return_value = result
        worker.is_alive.return_value = stuck
        worker.exitcode = 0
        context.Pipe.return_value = (receiver, sender)
        context.Process.return_value = worker
        return context, worker

    def test_missing_ro_preserves_proven_ownership_but_fails_inspection(self):
        lab = self.fake_lab()
        candidate = dict(self.entry, flags=0)
        context, worker = self.fake_worker_context({"verifiedMount": candidate, "effectiveReadOnly": False})
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(lab, "entry", return_value=candidate), \
                self.assertRaisesRegex(LAB.LabError, "MNT_RDONLY is missing"):
            lab.inspect(candidate)
        self.assertEqual(lab.verified_mount, candidate)
        self.assertTrue(lab.report["ownershipVerified"])
        self.assertNotIn("inspectionPassed", lab.report)
        worker.terminate.assert_not_called()

    def test_changed_mount_after_worker_readback_is_not_authorized_for_cleanup(self):
        lab = self.fake_lab()
        context, _ = self.fake_worker_context({"verifiedMount": self.entry, "effectiveReadOnly": True})
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(lab, "entry", return_value=dict(self.entry, filesystemID=[1, 2])), \
                self.assertRaisesRegex(LAB.LabError, "changed after inspection"):
            lab.inspect(self.entry)
        self.assertIsNone(lab.verified_mount)
        self.assertNotIn("ownershipVerified", lab.report)

    def test_stuck_worker_reports_retained_state_after_bounded_shutdown(self):
        lab = self.fake_lab()
        context, worker = self.fake_worker_context({}, stuck=True)
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                self.assertRaisesRegex(LAB.LabError, "exceeded 20 seconds"):
            lab.inspect(self.entry)
        worker.terminate.assert_called_once()
        worker.kill.assert_called_once()
        self.assertTrue(lab.report["inspectionWorkerStillRunning"])
        self.assertEqual(lab.report["inspectionWorkerPID"], worker.pid)
        self.assertIsNone(lab.verified_mount)
        lab.server.terminate.assert_not_called()

    def test_zero_server_exit_without_mount_fails_and_needs_no_active_cleanup(self):
        lab = self.fake_lab()
        server = lab.server
        server.poll.return_value = 0
        with mock.patch.object(LAB.os.path, "lexists", return_value=False), \
                mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(LAB.subprocess, "Popen", return_value=server) as launch, \
                mock.patch.object(LAB.subprocess, "run") as unmount:
            with self.assertRaisesRegex(LAB.LabError, "without an OS mount"):
                lab.mount()
            self.assertTrue(lab.cleanup())
        self.assertEqual(lab.report["serverExitCode"], 0)
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertEqual(launch.call_args.kwargs["env"], LAB.ENV)
        unmount.assert_not_called()
        server.terminate.assert_not_called()

    def test_verified_mount_even_without_ro_allows_only_normal_unmount(self):
        lab = self.fake_lab()
        lab.verified_mount = dict(self.entry, flags=0)
        with mock.patch.object(lab, "entry", side_effect=[lab.verified_mount, None, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.assertTrue(lab.cleanup())
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/diskutil", "unmount", str(self.mountpoint)])
        self.assertTrue(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertNotIn("status", lab.report)

    def test_unverified_changed_or_ownerless_mount_retains_server(self):
        for verified, server_exit in [(None, None), (dict(self.entry, filesystemID=[66, 77]), None),
                                      (self.entry, 0)]:
            lab = self.fake_lab()
            lab.verified_mount = verified
            lab.server.poll.return_value = server_exit
            with mock.patch.object(lab, "entry", return_value=self.entry), \
                    mock.patch.object(LAB.subprocess, "run") as run:
                self.assertFalse(lab.cleanup())
            run.assert_not_called()
            lab.server.terminate.assert_not_called()
            self.assertTrue(lab.report["cleanupRequired"])

    def test_busy_normal_unmount_preserves_server(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        with mock.patch.object(lab, "entry", return_value=self.entry), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"Busy")):
            self.assertFalse(lab.cleanup())
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["cleanupRequired"])

    def test_vanished_verified_mount_fails_without_claiming_active_cleanup(self):
        lab = self.fake_lab()
        lab.verified_mount = self.entry
        lab.server.poll.return_value = 0
        with mock.patch.object(lab, "entry", return_value=None), mock.patch.object(LAB.subprocess, "run") as run:
            self.assertFalse(lab.cleanup())
        run.assert_not_called()
        lab.server.terminate.assert_not_called()
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertIn("vanished", lab.report["cleanupError"])

    def test_mount_appearing_before_server_shutdown_retains_server(self):
        lab = self.fake_lab()
        with mock.patch.object(lab, "entry", side_effect=[None, self.entry]), \
                mock.patch.object(LAB.subprocess, "run") as run:
            self.assertFalse(lab.cleanup())
        run.assert_not_called()
        lab.server.terminate.assert_not_called()
        self.assertTrue(lab.report["cleanupRequired"])

    def test_inspection_without_explicit_clean_unmount_cannot_pass(self):
        def factory(binary, report):
            report["cleanUnmount"] = False
            lab = mock.Mock()
            lab.work = self.work
            lab.cleanup.return_value = True
            return lab

        output = io.StringIO()
        with mock.patch.object(LAB, "validate_invocation", return_value=(self.work / "never-executed", {})), \
                mock.patch.object(LAB, "ReferenceLab", side_effect=factory), \
                mock.patch.object(LAB.os, "umask"), contextlib.redirect_stdout(output):
            code = LAB.main()
        report = json.loads(output.getvalue())
        self.assertTrue(report["inspectionPassed"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(code, 1)

    def test_report_save_failure_prevents_success(self):
        not_directory = self.work / "not-directory"
        not_directory.write_bytes(b"retained")

        def factory(binary, report):
            report["cleanUnmount"] = True
            lab = mock.Mock()
            lab.work = not_directory
            lab.cleanup.return_value = True
            return lab

        output = io.StringIO()
        with mock.patch.object(LAB, "validate_invocation", return_value=(self.work / "never-executed", {})), \
                mock.patch.object(LAB, "ReferenceLab", side_effect=factory), \
                mock.patch.object(LAB.os, "umask"), contextlib.redirect_stdout(output):
            code = LAB.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("reportSaveError", report)

    def test_only_recorded_stuck_worker_selects_flushed_failure_hard_exit(self):
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
