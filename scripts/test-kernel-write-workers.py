#!/usr/bin/env python3
"""Worker transport checks using private host files; no engines or mounts."""
import contextlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_kernel_write_workers_tested", Path(__file__).with_name("kernel-write-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LAB
SPEC.loader.exec_module(LAB)


class WorkerChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-worker-check-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.result = self.work / "result.json"
        self.expected = b"a" * 32 + b"private nonce\n"
        self.fixture = "fixture-" + "61" * 32 + ".bin"
        self.candidate = {"source": str(self.work / "disposable.ntfs"),
                          "mountPoint": str(self.work / "never-mounted"),
                          "filesystemType": "macfuse", "extendedFlags": 0, "flags": 0,
                          "ownerUID": os.getuid(), "filesystemID": [71, 89]}

    def write_result(self, data, mode=0o600):
        self.result.write_bytes(data)
        self.result.chmod(mode)

    def stage(self):
        owner = SimpleNamespace(tools={}, work=self.work, image=self.work / "disposable.ntfs",
                                image_identity=None, fixture_name=self.fixture, expected=self.expected,
                                mountpoint=self.work / "never-mounted")
        return LAB.ImageStage(owner, "rw")

    def call_entry(self, kind):
        LAB.worker_entry(kind, self.result, self.work / "never-mounted", self.candidate,
                         self.fixture, self.expected)

    def test_well_formed_private_object_accepted(self):
        expected = {"fixtureSHA256": "f" * 64, "checks": ["test"], "verifiedMount": self.candidate}
        self.write_result(json.dumps(expected).encode())
        self.assertEqual(LAB.read_result(self.result), expected)

    def test_symlink_result_rejected_without_following(self):
        target = self.work / "unrelated.json"
        target.write_bytes(b'{"preserve":true}')
        self.result.symlink_to(target)
        with self.assertRaises(OSError):
            LAB.read_result(self.result)
        self.assertEqual(target.read_bytes(), b'{"preserve":true}')

    def test_fifo_and_directory_result_rejected_without_blocking(self):
        os.mkfifo(self.result, 0o600)
        with self.assertRaises(LAB.LabError):
            LAB.read_result(self.result)
        self.result.unlink()
        self.result.mkdir(mode=0o700)
        with self.assertRaises((LAB.LabError, OSError)):
            LAB.read_result(self.result)

    def test_oversize_empty_and_wrong_permission_results_rejected(self):
        cases = [(b"x" * (LAB.MAX_RESULT + 1), 0o600), (b"", 0o600),
                 (b"{}", 0o644), (b"{}", 0o400)]
        for data, mode in cases:
            with self.subTest(size=len(data), mode=oct(mode)):
                if self.result.exists():
                    self.result.unlink()
                self.write_result(data, mode)
                with self.assertRaises(LAB.LabError):
                    LAB.read_result(self.result)

    def test_hard_link_and_foreign_owner_results_rejected(self):
        self.write_result(b"{}")
        other = self.work / "hard-link"
        os.link(self.result, other)
        with self.assertRaises(LAB.LabError):
            LAB.read_result(self.result)
        other.unlink()
        info = self.result.stat()
        substituted = SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid() + 1,
                                      st_nlink=1, st_size=info.st_size)
        with mock.patch.object(LAB.os, "fstat", return_value=substituted), self.assertRaises(LAB.LabError):
            LAB.read_result(self.result)

    def test_malformed_json_and_nonobject_results_rejected(self):
        for data in (b"{malformed", b"[]", b"null", b"true", b"123", b'"string"'):
            with self.subTest(data=data):
                self.write_result(data)
                with self.assertRaises((ValueError, LAB.LabError)):
                    LAB.read_result(self.result)

    def test_worker_error_propagates(self):
        self.write_result(b'{"error":"Expected readback failure"}')
        with self.assertRaisesRegex(LAB.LabError, "Expected readback failure"):
            LAB.read_result(self.result)

    def test_changed_result_size_is_rejected(self):
        self.write_result(b'{"valid":true}')
        info = self.result.stat()
        substituted = SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid,
                                      st_nlink=1, st_size=info.st_size - 1)
        with mock.patch.object(LAB.os, "fstat", return_value=substituted), self.assertRaises(LAB.LabError):
            LAB.read_result(self.result)

    def test_real_spawned_ownership_worker_on_missing_private_path(self):
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=LAB.worker_entry,
            args=("ownership", self.result, self.work / "never-created", self.candidate,
                  self.fixture, self.expected))
        try:
            worker.start()
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive(), "Missing-path ownership worker must finish promptly")
            self.assertEqual(worker.exitcode, 0)
        finally:
            if worker.pid is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
            if worker.pid is not None and not worker.is_alive():
                worker.close()
        info = self.result.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertLessEqual(info.st_size, LAB.MAX_RESULT)
        with self.assertRaisesRegex(LAB.LabError, "FileNotFoundError"):
            LAB.read_result(self.result)

    def test_internal_unknown_worker_kind_returns_bounded_error(self):
        self.call_entry("unknown")
        with self.assertRaisesRegex(LAB.LabError, "Unknown internal worker operation"):
            LAB.read_result(self.result)

    def test_oversize_worker_output_becomes_bounded_error(self):
        with mock.patch.object(LAB.REFERENCE, "inspect_fixture", return_value={"huge": "x" * LAB.MAX_RESULT}):
            self.call_entry("ownership")
        self.assertLessEqual(self.result.stat().st_size, LAB.MAX_RESULT)
        with self.assertRaisesRegex(LAB.LabError, "exceeds the diagnostic limit"):
            LAB.read_result(self.result)

    def test_result_creation_never_overwrites_existing_or_symlink(self):
        self.write_result(b"preserve")
        with self.assertRaises(FileExistsError):
            self.call_entry("unknown")
        self.assertEqual(self.result.read_bytes(), b"preserve")
        self.result.unlink()
        target = self.work / "unrelated.json"
        target.write_bytes(b"preserve target")
        self.result.symlink_to(target)
        with self.assertRaises(OSError):
            self.call_entry("unknown")
        self.assertEqual(target.read_bytes(), b"preserve target")

    def test_successful_worker_result_is_read_after_join(self):
        stage = self.stage()
        worker = mock.Mock(pid=456, exitcode=0)
        worker.is_alive.return_value = False
        context = mock.Mock()
        context.Process.return_value = worker
        result = {"verifiedMount": self.candidate}
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(LAB, "read_result", return_value=result) as read:
            self.assertEqual(stage.run_worker("ownership", self.candidate), result)
        worker.start.assert_called_once_with()
        worker.join.assert_called_once_with(timeout=20)
        worker.terminate.assert_not_called()
        worker.kill.assert_not_called()
        path = read.call_args.args[0]
        self.assertEqual(path.parent, self.work)
        self.assertEqual(stage.report["workers"][0]["exitCode"], 0)

    def test_worker_failure_exit_does_not_read_result(self):
        stage = self.stage()
        worker = mock.Mock(pid=456, exitcode=7)
        worker.is_alive.return_value = False
        context = mock.Mock()
        context.Process.return_value = worker
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(LAB, "read_result") as read, self.assertRaises(LAB.LabError):
            stage.run_worker("ownership", self.candidate)
        read.assert_not_called()
        worker.terminate.assert_not_called()

    def test_worker_timeout_terminates_then_stops_without_kill(self):
        stage = self.stage()
        worker = mock.Mock(pid=456, exitcode=None)
        worker.is_alive.side_effect = [True, True, False, False]
        context = mock.Mock()
        context.Process.return_value = worker
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(LAB, "read_result") as read, self.assertRaisesRegex(LAB.LabError, "exceeded 20"):
            stage.run_worker("ownership", self.candidate)
        worker.terminate.assert_called_once_with()
        worker.kill.assert_not_called()
        self.assertEqual(worker.join.call_args_list, [mock.call(timeout=20), mock.call(timeout=2)])
        self.assertFalse(stage.report["inspectionWorkerStillRunning"])
        read.assert_not_called()

    def test_worker_timeout_escalates_to_kill_and_records_stuck(self):
        for stuck in (False, True):
            with self.subTest(stuck=stuck):
                stage = self.stage()
                worker = mock.Mock(pid=456, exitcode=None)
                worker.is_alive.side_effect = [True, True, True, stuck]
                context = mock.Mock()
                context.Process.return_value = worker
                with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                        mock.patch.object(LAB, "read_result") as read, self.assertRaisesRegex(LAB.LabError, "exceeded 60"):
                    stage.run_worker("exercise", self.candidate)
                worker.terminate.assert_called_once_with()
                worker.kill.assert_called_once_with()
                self.assertEqual(worker.join.call_args_list,
                                 [mock.call(timeout=60), mock.call(timeout=2), mock.call(timeout=2)])
                self.assertEqual(stage.report["inspectionWorkerStillRunning"], stuck)
                self.assertEqual(stage.report["workers"][0]["stillRunning"], stuck)
                read.assert_not_called()

    def test_private_result_error_propagates_through_run_worker(self):
        stage = self.stage()
        worker = mock.Mock(pid=456, exitcode=0)
        worker.is_alive.return_value = False
        context = mock.Mock()
        context.Process.return_value = worker
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(LAB, "read_result", side_effect=LAB.LabError("fixture mismatch")), \
                self.assertRaisesRegex(LAB.LabError, "fixture mismatch"):
            stage.run_worker("verify", self.candidate)
        worker.join.assert_called_once_with(timeout=60)
        worker.terminate.assert_not_called()

    def test_stuck_worker_state_reaches_supervisor_hard_exit(self):
        previous_flag = LAB.INSPECTION_WORKER_STILL_RUNNING
        self.addCleanup(setattr, LAB, "INSPECTION_WORKER_STILL_RUNNING", previous_flag)
        stage = SimpleNamespace(phase="rw", report={"inspectionWorkerStillRunning": True})
        lab = SimpleNamespace(work=self.work, stages=[stage], execute=mock.Mock(side_effect=LAB.LabError("stuck worker")))
        record = {"kernelReadiness": {"backendLoaded": True}}
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["write-lab", "--execute"]), \
                mock.patch.object(LAB, "preflight", return_value=({}, record)), \
                mock.patch.object(LAB, "WriteImageLab", return_value=lab), \
                mock.patch.object(LAB.platform, "machine", return_value="x86_64"), \
                mock.patch.object(LAB.platform, "mac_ver", return_value=("15.7.9", (), "")), \
                mock.patch.object(LAB.os, "umask"), contextlib.redirect_stdout(output):
            code = LAB.main()
        self.assertEqual(code, 1)
        self.assertTrue(LAB.INSPECTION_WORKER_STILL_RUNNING)
        self.assertTrue(json.loads(output.getvalue())["inspectionWorkerStillRunning"])
        with mock.patch.object(LAB.REFERENCE.os, "_exit") as hard_exit:
            LAB.REFERENCE.finish_supervisor(code, LAB.INSPECTION_WORKER_STILL_RUNNING)
        hard_exit.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
