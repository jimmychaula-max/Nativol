#!/usr/bin/env python3
"""Kernel wrapper checks: mock all mount paths; never launch a FUSE process.

The spawn check starts only a Python worker against a missing private path.
It verifies child-import registration without accessing a mounted filesystem.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_kernel_wrapper", Path(__file__).with_name("kernel-reference-lab.py"))
KERNEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KERNEL)
REFERENCE = KERNEL.REFERENCE


class KernelReferenceChecks(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, REFERENCE, "validate_invocation", KERNEL.BUILD_PREFLIGHT)
        temporary = tempfile.TemporaryDirectory(prefix="nativol-kernel-wrapper-check-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.binary = self.work / "never-executed-kernel-reference"
        self.record = {"backend": "kernel", "schemaVersion": 1}

    def fake_lab(self):
        lab = REFERENCE.ReferenceLab.__new__(REFERENCE.ReferenceLab)
        lab.work = self.work
        lab.mountpoint = self.work / "uncreated-mountpoint"
        lab.binary, lab.nonce = self.binary, "a" * 64
        lab.fixture_name, lab.expected = "fixture-" + lab.nonce + ".txt", b"fixture"
        lab.server = mock.Mock()
        lab.server.poll.return_value = None
        lab.server_log = lab.verified_mount = None
        lab.report = {"cleanupRequired": False, "checks": []}
        return lab

    def candidate(self, lab):
        return {"source": REFERENCE.MOUNT_PREFIX + lab.nonce,
                "mountPoint": str(lab.mountpoint), "filesystemType": "macfuse",
                "flags": REFERENCE.MNT_RDONLY, "extendedFlags": 0,
                "ownerUID": os.getuid(), "filesystemID": [31, 47]}

    def test_default_preparation_never_enters_reference_execution_even_when_loaded(self):
        for loaded in (False, True):
            output = io.StringIO()
            with self.subTest(loaded=loaded), mock.patch.object(sys, "argv", ["kernel-wrapper"]), \
                    mock.patch.object(KERNEL, "BUILD_PREFLIGHT", return_value=(self.binary, self.record)), \
                    mock.patch.object(KERNEL.READINESS, "inspect_readiness", return_value={
                        "verificationPassed": True, "backendLoaded": loaded}), \
                    mock.patch.object(REFERENCE, "main") as execute, \
                    mock.patch.object(REFERENCE.subprocess, "Popen") as launch, \
                    contextlib.redirect_stdout(output):
                code = KERNEL.main()
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "prepared")
            self.assertEqual(report["readyToExecute"], loaded)
            self.assertFalse(report["mountAttemptPerformed"])
            self.assertFalse(report["kernelLoadingPerformed"])
            self.assertFalse(report["physicalDeviceTargetAccepted"])
            execute.assert_not_called()
            launch.assert_not_called()

    def test_unverified_preparation_fails_without_entering_reference(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["kernel-wrapper"]), \
                mock.patch.object(KERNEL, "BUILD_PREFLIGHT", return_value=(self.binary, self.record)), \
                mock.patch.object(KERNEL.READINESS, "inspect_readiness", return_value={
                    "verificationPassed": False, "backendLoaded": True}), \
                mock.patch.object(REFERENCE, "main") as execute, \
                mock.patch.object(REFERENCE.subprocess, "Popen") as launch, \
                contextlib.redirect_stdout(output):
            code = KERNEL.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["readyToExecute"])
        execute.assert_not_called()
        launch.assert_not_called()

    def test_execute_refuses_unloaded_or_unverified_before_lab_or_process_creation(self):
        for verified, loaded in ((True, False), (False, True), (False, False)):
            output = io.StringIO()
            with self.subTest(verified=verified, loaded=loaded), \
                    mock.patch.object(sys, "argv", ["kernel-wrapper", "--execute"]), \
                    mock.patch.object(KERNEL, "BUILD_PREFLIGHT", return_value=(self.binary, self.record)), \
                    mock.patch.object(KERNEL.READINESS, "inspect_readiness", return_value={
                        "verificationPassed": verified, "backendLoaded": loaded}), \
                    mock.patch.object(REFERENCE, "ReferenceLab") as lab, \
                    mock.patch.object(REFERENCE.subprocess, "Popen") as launch, \
                    contextlib.redirect_stdout(output):
                code = KERNEL.main()
                self.assertEqual(sys.argv, ["kernel-wrapper", "--execute"])
                self.assertIs(REFERENCE.validate_invocation, KERNEL.BUILD_PREFLIGHT)
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "failed")
            self.assertIn("not loaded", report["error"])
            lab.assert_not_called()
            launch.assert_not_called()

    def test_unknown_arguments_are_rejected_before_preflight(self):
        for arguments in (["/dev/disk1"], ["--kernel"], ["--execute", "--execute"],
                          ["--mountpoint", "/Volumes/Valuable"], ["--execute", "-o", "rw"]):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(sys, "argv", ["kernel-wrapper"] + arguments), \
                    mock.patch.object(KERNEL, "BUILD_PREFLIGHT") as build, \
                    mock.patch.object(KERNEL.READINESS, "inspect_readiness") as readiness, \
                    mock.patch.object(REFERENCE, "main") as execute, \
                    mock.patch.object(REFERENCE.subprocess, "Popen") as launch, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(KERNEL.main(), 2)
            build.assert_not_called()
            readiness.assert_not_called()
            execute.assert_not_called()
            launch.assert_not_called()

    def test_verified_execute_has_only_fixed_kernel_profile_and_consumes_mode_flag(self):
        readiness = {"verificationPassed": True, "backendLoaded": True}

        def no_mount_main():
            self.assertEqual(sys.argv, ["kernel-wrapper"])
            binary, record = REFERENCE.validate_invocation()
            self.assertEqual(binary, self.binary)
            self.assertEqual(record["kernelReadiness"], readiness)
            return 17

        with mock.patch.object(sys, "argv", ["kernel-wrapper", "--execute"]), \
                mock.patch.object(KERNEL, "BUILD_PREFLIGHT", return_value=(self.binary, self.record)), \
                mock.patch.object(KERNEL.READINESS, "inspect_readiness", return_value=readiness), \
                mock.patch.object(REFERENCE, "main", side_effect=no_mount_main), \
                mock.patch.object(REFERENCE.subprocess, "Popen") as launch:
            self.assertEqual(KERNEL.main(), 17)
        self.assertEqual(REFERENCE.BACKEND, "kernel")
        self.assertEqual(REFERENCE.BUILD_SUBDIR, "reference-kernel")
        self.assertEqual(REFERENCE.BINARY_NAME, "kernel-reference")
        self.assertEqual(REFERENCE.MOUNT_PREFIX, "Nativol-Kernel-Reference-")
        self.assertEqual(REFERENCE.ALLOWED_TYPES, {"macfuse"})
        launch.assert_not_called()

    def test_kernel_identity_refuses_fskit_wrong_type_owner_mountpoint_or_zero_fsid(self):
        lab = self.fake_lab()
        entry = self.candidate(lab)
        REFERENCE.validate_identity(entry, lab.mountpoint)
        for change in ({"extendedFlags": REFERENCE.MNT_EXT_FSKIT},
                       {"filesystemType": "macfuse-local"}, {"filesystemType": "apfs"},
                       {"ownerUID": os.getuid() + 1}, {"mountPoint": "/Volumes/Valuable"},
                       {"filesystemID": [0, 0]}):
            with self.subTest(change=change), self.assertRaises(REFERENCE.LabError):
                REFERENCE.validate_identity(dict(entry, **change), lab.mountpoint)

    def test_wrong_source_nonce_is_refused_before_candidate_is_returned(self):
        lab = self.fake_lab()
        entry = dict(self.candidate(lab), source="Nativol-Kernel-Reference-" + "b" * 64)
        with mock.patch.object(REFERENCE.os.path, "lexists", return_value=False), \
                mock.patch.object(lab, "entry", side_effect=[None, entry]), \
                mock.patch.object(REFERENCE.subprocess, "Popen", return_value=lab.server), \
                self.assertRaisesRegex(REFERENCE.LabError, "source does not match"):
            lab.mount()
        self.addCleanup(lab.server_log.close)
        self.assertIsNone(lab.verified_mount)
        self.assertNotIn("ownershipVerified", lab.report)
        lab.server.terminate.assert_not_called()

    def test_valid_kernel_candidate_uses_fixed_binary_arguments_without_backend_cli(self):
        lab = self.fake_lab()
        entry = self.candidate(lab)
        with mock.patch.object(REFERENCE.os.path, "lexists", return_value=False), \
                mock.patch.object(lab, "entry", side_effect=[None, entry]), \
                mock.patch.object(REFERENCE.subprocess, "Popen", return_value=lab.server) as launch:
            self.assertEqual(lab.mount(), entry)
        self.addCleanup(lab.server_log.close)
        self.assertEqual(launch.call_args.args[0], [str(self.binary), "--nonce", lab.nonce,
                                                  "--mountpoint", str(lab.mountpoint)])
        self.assertEqual(launch.call_args.kwargs["env"], REFERENCE.ENV)
        self.assertIsNone(lab.verified_mount)  # The nonce readback must still prove ownership.

    def test_backend_manifest_mismatch_fails_before_runtime_or_signature_commands(self):
        directory = self.work / ".local-engine/reference-kernel/x86_64"
        directory.mkdir(parents=True)
        binary = directory / "kernel-reference"
        binary.write_bytes(b"Never executed: wrong backend manifest")
        binary.chmod(0o700)
        source = self.work / "scripts/fskit-reference.c"
        source.parent.mkdir()
        source.write_bytes(b"mock local source")
        record = {"schemaVersion": 1, "architecture": "x86_64", "backend": "fskit",
                  "binarySHA256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                  "sourceSHA256": hashlib.sha256(source.read_bytes()).hexdigest()}
        (directory / "manifest.json").write_text(json.dumps(record))
        with mock.patch.object(REFERENCE, "ROOT", self.work), \
                mock.patch.object(sys, "argv", ["kernel-wrapper"]), \
                mock.patch.object(REFERENCE.platform, "machine", return_value="x86_64"), \
                mock.patch.object(REFERENCE.sys, "flags", mock.Mock(isolated=True, no_site=True)), \
                mock.patch.object(REFERENCE.subprocess, "run") as command, \
                mock.patch.object(REFERENCE.subprocess, "Popen") as launch, \
                self.assertRaisesRegex(REFERENCE.LabError, "differs from the local build record"):
            KERNEL.BUILD_PREFLIGHT()
        command.assert_not_called()
        launch.assert_not_called()

    def test_spawn_import_registers_worker_module_without_mounting(self):
        self.assertIs(sys.modules["nativol_kernel_reference"], REFERENCE)
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=REFERENCE.inspect_worker,
                                 args=(sender, self.work / "missing-private-path",
                                       "not-opened", b"", {}))
        try:
            worker.start()
            sender.close()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "Python-only import worker exceeded five seconds")
            self.assertEqual(worker.exitcode, 0)
            self.assertTrue(receiver.poll())
            self.assertIn("FileNotFoundError", receiver.recv()["error"])
        finally:
            if worker.pid is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
            sender.close()
            receiver.close()


if __name__ == "__main__":
    unittest.main()
