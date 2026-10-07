#!/usr/bin/env python3
"""Mocked writable-image lifecycle checks; never starts NTFS or mounts anything."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_kernel_write_image_lab", Path(__file__).with_name("kernel-write-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LAB
SPEC.loader.exec_module(LAB)


class WriteImageChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-write-lifecycle-check-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.tools = {name: self.work / ("never-executed-" + name) for name in LAB.BASE.TOOL_RECORDS}
        with mock.patch.object(LAB.tempfile, "mkdtemp", return_value=str(self.work)):
            self.owner = LAB.WriteImageLab(self.tools, {})
        self.owner.image_identity = (1, 2)
        self.record = {"kernelReadiness": {"backendLoaded": True}, "scriptSHA256": {}}

    def stage(self, phase="rw"):
        stage = LAB.ImageStage(self.owner, phase)
        self.owner.stages.append(stage)
        self.owner.report["stages"].append(stage.report)
        stage.server = mock.Mock(pid=99999)
        stage.server.poll.return_value = None
        return stage

    def candidate(self, stage):
        return {"source": str(stage.image), "mountPoint": str(stage.mountpoint),
                "filesystemType": "macfuse", "extendedFlags": 0,
                "flags": LAB.REFERENCE.MNT_RDONLY if stage.phase == "ro" else 0,
                "ownerUID": os.getuid(), "filesystemID": [12, 34]}

    def proof(self, stage, candidate):
        return {"verifiedMount": candidate,
                "fixtureSHA256": hashlib.sha256(stage.expected).hexdigest(),
                "effectiveReadOnly": stage.phase == "ro"}

    def complete_report(self):
        return {"stages": [{"phase": phase, "status": "passed", "ownershipVerified": True,
                            "workloadPassed": True, "cleanUnmount": True, "serverExitCode": 0}
                           for phase in ("rw", "ro")], "imageChangedByWriteStage": True,
                "readOnlyImageUnchanged": True, "fixtureManifestsMatch": True}

    def test_default_preparation_never_creates_image_or_requests_writable_mount(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["write-image-lab"]), \
                mock.patch.object(LAB, "preflight", return_value=(self.tools, self.record)) as preflight, \
                mock.patch.object(LAB, "WriteImageLab") as factory, \
                mock.patch.object(LAB.subprocess, "Popen") as launch, contextlib.redirect_stdout(output):
            code = LAB.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "prepared")
        for flag in ("mountAttemptPerformed", "mountedWriteWorkloadRequested", "writableMountRequested"):
            self.assertFalse(report[flag])
        preflight.assert_called_once_with(False)
        factory.assert_not_called()
        launch.assert_not_called()

    def test_arbitrary_cli_targets_or_options_are_refused_before_preflight(self):
        for arguments in (["/dev/disk1"], ["--image", "/tmp/existing"], ["--execute", "rw"],
                          ["--execute", "--execute"], ["--backend", "kernel"]):
            with self.subTest(arguments=arguments), mock.patch.object(sys, "argv", ["lab"] + arguments), \
                    mock.patch.object(LAB, "preflight") as preflight, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(LAB.main(), 2)
            preflight.assert_not_called()

    def test_execution_readiness_failure_precedes_image_creation(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["lab", "--execute"]), \
                mock.patch.object(LAB, "preflight", side_effect=LAB.LabError("module not loaded")) as preflight, \
                mock.patch.object(LAB, "WriteImageLab") as factory, \
                mock.patch.object(LAB.subprocess, "Popen") as launch, contextlib.redirect_stdout(output):
            self.assertEqual(LAB.main(), 1)
        preflight.assert_called_once_with(True)
        factory.assert_not_called()
        launch.assert_not_called()

    def test_no_fixture_worker_can_start_without_current_proven_ownership(self):
        stage = self.stage()
        with mock.patch.object(stage, "run_worker") as worker, self.assertRaises(LAB.LabError):
            stage.workload()
        worker.assert_not_called()
        stage.verified_mount = self.candidate(stage)
        with mock.patch.object(stage, "entry", return_value=dict(stage.verified_mount, filesystemID=[9, 9])), \
                mock.patch.object(stage, "run_worker") as worker, self.assertRaises(LAB.LabError):
            stage.workload()
        worker.assert_not_called()

    def test_nonce_proof_mismatch_never_grants_write_ownership(self):
        stage = self.stage()
        candidate = self.candidate(stage)
        bad = dict(self.proof(stage, candidate), fixtureSHA256="wrong")
        with mock.patch.object(stage, "run_worker", return_value=bad), \
                mock.patch.object(stage, "entry", return_value=candidate), self.assertRaises(LAB.LabError):
            stage.prove_ownership(candidate)
        self.assertIsNone(stage.verified_mount)
        self.assertNotIn("ownershipVerified", stage.report)

    def test_capability_failure_retains_nonce_ownership_for_safe_cleanup(self):
        stage = self.stage()
        candidate = dict(self.candidate(stage), flags=LAB.REFERENCE.MNT_RDONLY)
        proof = dict(self.proof(stage, candidate), effectiveReadOnly=True)
        with mock.patch.object(stage, "run_worker", return_value=proof), \
                mock.patch.object(stage, "entry", return_value=candidate), \
                self.assertRaisesRegex(LAB.LabError, "access differs"):
            stage.prove_ownership(candidate)
        self.assertEqual(stage.verified_mount, candidate)
        self.assertTrue(stage.report["ownershipVerified"])

    def test_fixed_stage_options_and_new_mountpoint_process_separation(self):
        rw = self.stage("rw")
        ro = self.stage("ro")
        self.assertNotEqual(rw.mountpoint, ro.mountpoint)
        self.assertNotEqual(rw.log_path, ro.log_path)
        for stage in (rw, ro):
            candidate = self.candidate(stage)
            with self.subTest(phase=stage.phase), mock.patch.object(self.owner, "assert_detached"), \
                    mock.patch.object(stage, "verify_image"), \
                    mock.patch.object(LAB.BASE, "inspect_readiness", return_value={"backendLoaded": True}), \
                    mock.patch.object(LAB.BASE, "verify_tool", return_value=self.tools["ntfs-3g"]), \
                    mock.patch.object(stage, "entry", side_effect=[None, candidate]), \
                    mock.patch.object(LAB.subprocess, "Popen", return_value=stage.server) as launch:
                self.assertEqual(stage.mount(), candidate)
            self.addCleanup(stage.server_log.close)
            arguments = launch.call_args.args[0]
            self.assertEqual(arguments[:4], [str(self.tools["ntfs-3g"]), str(self.owner.image),
                                             str(stage.mountpoint), "-o"])
            options = set(arguments[4].split(","))
            self.assertTrue({"backend=kernel", "local", "no_def_opts", "norecover", stage.phase,
                             "no_detach", "quiet", "umask=077"}.issubset(options))
            self.assertNotIn("ro" if stage.phase == "rw" else "rw", options)
            self.assertEqual(launch.call_args.kwargs["env"], LAB.ENV)
            self.assertIsNone(stage.verified_mount)

    def test_driver_zero_exit_without_mount_is_failure(self):
        stage = self.stage()
        stage.server.poll.return_value = 0
        with mock.patch.object(self.owner, "assert_detached"), mock.patch.object(stage, "verify_image"), \
                mock.patch.object(LAB.BASE, "inspect_readiness", return_value={"backendLoaded": True}), \
                mock.patch.object(LAB.BASE, "verify_tool", return_value=self.tools["ntfs-3g"]), \
                mock.patch.object(stage, "entry", return_value=None), \
                mock.patch.object(LAB.subprocess, "Popen", return_value=stage.server), \
                self.assertRaisesRegex(LAB.LabError, "without an OS mount"):
            stage.mount()
        self.addCleanup(stage.server_log.close)
        self.assertEqual(stage.report["serverExitCode"], 0)
        self.assertNotIn("ownershipVerified", stage.report)

    def test_workload_failure_still_cleans_only_the_previously_proven_stage(self):
        stage = mock.Mock()
        stage.report = {"phase": "rw", "checks": []}
        stage.workload.side_effect = LAB.LabError("fixture failed after ownership")

        def cleanup():
            stage.report["cleanUnmount"] = True
            return True

        stage.cleanup.side_effect = cleanup
        with mock.patch.object(LAB, "ImageStage", return_value=stage), \
                mock.patch.object(self.owner, "assert_detached") as detached, \
                self.assertRaisesRegex(LAB.LabError, "fixture failed"):
            self.owner.run_stage("rw")
        stage.prove_ownership.assert_called_once()
        stage.cleanup.assert_called_once()
        detached.assert_not_called()  # Never proceed to hashing or remount after workload failure.

    def test_natural_server_drain_is_required_after_normal_unmount(self):
        for exit_code in (0, 2):
            stage = self.stage()
            candidate = self.candidate(stage)
            stage.verified_mount = candidate

            def drain(timeout):
                stage.server.poll.return_value = exit_code
                return exit_code

            stage.server.wait.side_effect = drain
            with self.subTest(exit_code=exit_code), \
                    mock.patch.object(stage, "entry", side_effect=[candidate, None, None, None]), \
                    mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
                self.assertEqual(stage.cleanup(), exit_code == 0)
            self.assertTrue(stage.report["cleanUnmount"])
            self.assertFalse(stage.report["cleanupRequired"])
            self.assertEqual(stage.report["serverExitCode"], exit_code)
            stage.server.wait.assert_called_once_with(timeout=15)
            stage.server.terminate.assert_not_called()
            self.assertEqual(run.call_args.args[0], ["/usr/sbin/diskutil", "unmount", str(stage.mountpoint)])

    def test_proven_server_drain_timeout_retains_process_and_forbids_hash_or_remount(self):
        stage = self.stage()
        candidate = self.candidate(stage)
        stage.verified_mount = candidate
        stage.server.wait.side_effect = subprocess.TimeoutExpired("ntfs-3g", 15)
        with mock.patch.object(stage, "entry", side_effect=[candidate, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            self.assertFalse(stage.cleanup())
        self.assertTrue(stage.report["cleanUnmount"])
        self.assertTrue(stage.report["serverDrainPending"])
        self.assertTrue(stage.report["cleanupRequired"])
        stage.server.terminate.assert_not_called()
        with mock.patch.object(LAB.REFERENCE, "mount_table", return_value=[]), \
                mock.patch.object(LAB.BASE.KernelImageLab, "hash_image") as digest, self.assertRaises(LAB.LabError):
            self.owner.hash_image()
        digest.assert_not_called()

    def test_unknown_changed_or_elsewhere_image_mount_is_never_unmounted_or_terminated(self):
        for kind in ("unknown", "changed", "elsewhere"):
            stage = self.stage()
            candidate = self.candidate(stage)
            if kind != "unknown":
                stage.verified_mount = dict(candidate, filesystemID=[8, 8]) if kind == "changed" else candidate
            if kind == "elsewhere":
                candidate = dict(candidate, mountPoint="/Volumes/Other")
            with self.subTest(kind=kind), mock.patch.object(LAB.REFERENCE, "mount_table", return_value=[candidate]), \
                    mock.patch.object(LAB.subprocess, "run") as run:
                self.assertFalse(stage.cleanup())
            self.assertTrue(stage.report["cleanupRequired"])
            run.assert_not_called()
            stage.server.terminate.assert_not_called()

    def test_vanished_verified_stage_fails_even_after_natural_server_exit(self):
        stage = self.stage()
        stage.verified_mount = self.candidate(stage)

        def drain(timeout):
            stage.server.poll.return_value = 0
            return 0

        stage.server.wait.side_effect = drain
        with mock.patch.object(stage, "entry", return_value=None), mock.patch.object(LAB.subprocess, "run") as run:
            self.assertFalse(stage.cleanup())
        self.assertFalse(stage.report["cleanupRequired"])
        self.assertFalse(stage.report["cleanUnmount"])
        self.assertIn("vanished", stage.report["cleanupError"])
        run.assert_not_called()
        stage.server.terminate.assert_not_called()

    def test_offline_hash_refuses_same_image_elsewhere_nonzero_server_or_stuck_worker(self):
        stage = self.stage()
        stage.server.poll.return_value = 0
        for case in ("elsewhere", "nonzero", "worker"):
            stage.server.poll.return_value = 3 if case == "nonzero" else 0
            stage.report["inspectionWorkerStillRunning"] = case == "worker"
            table = [dict(self.candidate(stage), mountPoint="/Volumes/Other")] if case == "elsewhere" else []
            with self.subTest(case=case), mock.patch.object(LAB.REFERENCE, "mount_table", return_value=table), \
                    mock.patch.object(LAB.BASE.KernelImageLab, "hash_image") as digest, self.assertRaises(LAB.LabError):
                self.owner.hash_image()
            digest.assert_not_called()

    def test_persistence_requires_changed_rw_hash_identical_manifests_and_unchanged_ro_hash(self):
        cases = [(["baseline"], [{"files": []}], "did not change"),
                 (["written", "changed-again"], [{"files": [1]}, {"files": [1]}], "persistence"),
                 (["written", "written"], [{"files": [1]}, {"files": [2]}], "persistence")]
        self.owner.original_hash = "baseline"
        for hashes, manifests, error in cases:
            with self.subTest(error=error, hashes=hashes), mock.patch.object(self.owner, "prepare"), \
                    mock.patch.object(self.owner, "run_stage", side_effect=manifests) as stages, \
                    mock.patch.object(self.owner, "hash_image", side_effect=hashes), \
                    self.assertRaisesRegex(LAB.LabError, error):
                self.owner.execute()
            if hashes == ["baseline"]:
                stages.assert_called_once_with("rw")
        with mock.patch.object(self.owner, "prepare"), \
                mock.patch.object(self.owner, "run_stage", side_effect=[{"files": [1]}, {"files": [1]}]), \
                mock.patch.object(self.owner, "hash_image", side_effect=["written", "written"]):
            self.owner.execute()
        self.assertTrue(self.owner.report["fixtureManifestsMatch"])
        self.assertTrue(self.owner.report["readOnlyImageUnchanged"])

    def test_failed_detached_snapshot_blocks_hash_and_records_uncertainty(self):
        with mock.patch.object(LAB.REFERENCE, "mount_table", side_effect=LAB.LabError("snapshot failed")), \
                mock.patch.object(LAB.BASE.KernelImageLab, "hash_image") as digest, \
                self.assertRaisesRegex(LAB.LabError, "snapshot failed"):
            self.owner.hash_image()
        self.assertTrue(self.owner.report["detachedStateUncertain"])
        digest.assert_not_called()

    def test_final_success_requires_every_stage_and_persistence_evidence_field(self):
        complete = self.complete_report()
        self.assertTrue(LAB.success_evidence(complete))
        for field in ("ownershipVerified", "workloadPassed", "cleanUnmount"):
            bad = copy.deepcopy(complete)
            bad["stages"][0][field] = False
            self.assertFalse(LAB.success_evidence(bad))
        for field in ("imageChangedByWriteStage", "readOnlyImageUnchanged", "fixtureManifestsMatch"):
            bad = copy.deepcopy(complete)
            bad[field] = False
            self.assertFalse(LAB.success_evidence(bad))
        for exit_code in (None, 2):
            bad = copy.deepcopy(complete)
            bad["stages"][1]["serverExitCode"] = exit_code
            self.assertFalse(LAB.success_evidence(bad))

    def test_bounded_result_file_rejects_symlinks_and_oversized_payloads(self):
        result = self.work / "result.json"
        result.write_text(json.dumps({"ok": True}))
        result.chmod(0o600)
        self.assertEqual(LAB.read_result(result), {"ok": True})
        link = self.work / "linked.json"
        link.symlink_to(result)
        with self.assertRaises(OSError):
            LAB.read_result(link)
        result.write_bytes(b"x" * (LAB.MAX_RESULT + 1))
        with self.assertRaises(LAB.LabError):
            LAB.read_result(result)

    def test_worker_exception_or_oversize_becomes_small_failed_result(self):
        stage = self.stage()
        for index, value in enumerate((LAB.LabError("fixture failure"), {"oversized": "x" * LAB.MAX_RESULT})):
            result = self.work / ("worker-" + str(index) + ".json")
            kwargs = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
            with mock.patch.object(LAB.FIXTURES, "exercise", **kwargs):
                LAB.worker_entry("exercise", result, stage.mountpoint, self.candidate(stage), stage.fixture_name, stage.expected)
            self.assertLess(result.stat().st_size, 1024)
            with self.assertRaises(LAB.LabError):
                LAB.read_result(result)

    def test_stuck_worker_shutdown_is_bounded_and_never_signals_ntfs_server(self):
        stage = self.stage()
        worker, context = mock.Mock(pid=12345), mock.Mock()
        worker.is_alive.return_value = True
        context.Process.return_value = worker
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                self.assertRaisesRegex(LAB.LabError, "exceeded 60 seconds"):
            stage.run_worker("exercise", self.candidate(stage))
        self.assertTrue(stage.report["inspectionWorkerStillRunning"])
        worker.terminate.assert_called_once()
        worker.kill.assert_called_once()
        self.assertEqual(worker.join.call_args_list, [mock.call(timeout=60), mock.call(timeout=2), mock.call(timeout=2)])
        stage.server.terminate.assert_not_called()
        with mock.patch.object(LAB.REFERENCE.os, "_exit") as hard_exit, \
                mock.patch.object(sys.stdout, "flush"), mock.patch.object(sys.stderr, "flush"):
            LAB.REFERENCE.finish_supervisor(1, True)
        hard_exit.assert_called_once_with(1)

    def test_spawn_uses_private_result_file_without_pipe_or_mount(self):
        stage = self.stage()
        stage.mountpoint = self.work / "missing-private-path"
        with self.assertRaisesRegex(LAB.LabError, "FileNotFoundError"):
            stage.run_worker("ownership", self.candidate(stage))
        result = Path(stage.report["workers"][0]["resultPath"])
        self.assertTrue(result.is_file())
        self.assertEqual(stage.report["workers"][0]["exitCode"], 0)

    def test_existing_final_report_prevents_pass_and_retains_prior_content(self):
        (self.work / "report.json").write_bytes(b"preserve this report")
        fake = mock.Mock(work=self.work, stages=[])

        def factory(tools, report):
            def execute():
                report.update(self.complete_report())
            fake.execute.side_effect = execute
            return fake

        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["lab", "--execute"]), \
                mock.patch.object(LAB, "preflight", return_value=(self.tools, self.record)), \
                mock.patch.object(LAB.os, "umask"), mock.patch.object(LAB, "WriteImageLab", side_effect=factory), \
                contextlib.redirect_stdout(output):
            self.assertEqual(LAB.main(), 1)
        self.assertIn("reportSaveError", json.loads(output.getvalue()))
        self.assertEqual((self.work / "report.json").read_bytes(), b"preserve this report")


if __name__ == "__main__":
    unittest.main()
