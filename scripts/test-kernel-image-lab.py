#!/usr/bin/env python3
"""Mocked kernel/NTFS image checks; no actual engine, mount or device operation."""
import contextlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_kernel_image_lab", Path(__file__).with_name("kernel-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)
REFERENCE = LAB.REFERENCE


class KernelImageChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-kernel-image-check-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.tools = {name: self.work / ("never-run-" + name) for name in LAB.TOOL_RECORDS}
        self.record = {"kernelReadiness": {"verificationPassed": True, "backendLoaded": True}}

    def lab(self):
        with mock.patch.object(LAB.tempfile, "mkdtemp", return_value=str(self.work)):
            lab = LAB.KernelImageLab(self.tools, {})
        lab.server = mock.Mock()
        lab.server.poll.return_value = None
        return lab

    def candidate(self, lab):
        return {"source": str(lab.image), "mountPoint": str(lab.mountpoint),
                "filesystemType": "macfuse", "extendedFlags": 0, "flags": REFERENCE.MNT_RDONLY,
                "ownerUID": os.getuid(), "filesystemID": [44, 55]}

    def small_image(self, lab):
        lab.image.write_bytes(bytes(4096))
        lab.image.chmod(0o600)
        info = lab.image.stat()
        lab.image_identity = (info.st_dev, info.st_ino)

    def test_default_preparation_never_creates_image_or_starts_engine(self):
        for loaded in (False, True):
            output = io.StringIO()
            record = {"kernelReadiness": {"verificationPassed": True, "backendLoaded": loaded}}
            with self.subTest(loaded=loaded), mock.patch.object(sys, "argv", ["image-lab"]), \
                    mock.patch.object(LAB, "preflight", return_value=(self.tools, record)) as preflight, \
                    mock.patch.object(LAB, "KernelImageLab") as factory, \
                    mock.patch.object(LAB.subprocess, "Popen") as launch, \
                    mock.patch.object(LAB.subprocess, "run") as command, \
                    contextlib.redirect_stdout(output):
                code = LAB.main()
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "prepared")
            self.assertEqual(report["readyToExecute"], loaded)
            self.assertFalse(report["mountAttemptPerformed"])
            self.assertFalse(report["kernelLoadingPerformed"])
            preflight.assert_called_once_with(False)
            factory.assert_not_called()
            launch.assert_not_called()
            command.assert_not_called()

    def test_arbitrary_arguments_fail_before_preflight(self):
        for arguments in (["/dev/disk1"], ["--image", "/tmp/existing.ntfs"], ["--backend", "fskit"],
                          ["--execute", "--execute"], ["--execute", "-o", "rw"]):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(sys, "argv", ["image-lab"] + arguments), \
                    mock.patch.object(LAB, "preflight") as preflight, \
                    mock.patch.object(LAB.subprocess, "Popen") as launch, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(LAB.main(), 2)
            preflight.assert_not_called()
            launch.assert_not_called()

    def test_root_mixed_identity_wrong_host_or_python_startup_refused_before_tools(self):
        cases = [dict(uid=0), dict(euid=0), dict(egid=0), dict(architecture="arm64"),
                 dict(version="26.0"), dict(isolated=False), dict(no_site=False)]
        for change in cases:
            values = dict(uid=501, euid=501, gid=20, egid=20, architecture="x86_64",
                          version="15.7.9", isolated=True, no_site=True)
            values.update(change)
            with self.subTest(change=change), mock.patch.object(sys, "argv", ["image-lab"]), \
                    mock.patch.object(LAB.os, "getuid", return_value=values["uid"]), \
                    mock.patch.object(LAB.os, "geteuid", return_value=values["euid"]), \
                    mock.patch.object(LAB.os, "getgid", return_value=values["gid"]), \
                    mock.patch.object(LAB.os, "getegid", return_value=values["egid"]), \
                    mock.patch.object(LAB.platform, "machine", return_value=values["architecture"]), \
                    mock.patch.object(LAB.platform, "mac_ver", return_value=(values["version"], (), "")), \
                    mock.patch.object(sys, "flags", mock.Mock(isolated=values["isolated"], no_site=values["no_site"])), \
                    mock.patch.object(LAB, "verify_tool") as verify, self.assertRaises(LAB.LabError):
                LAB.preflight(True)
            verify.assert_not_called()

    def test_unloaded_or_unverified_execution_stops_before_private_image_creation(self):
        for verified, loaded in ((True, False), (False, True), (False, False)):
            output = io.StringIO()
            with self.subTest(verified=verified, loaded=loaded), \
                    mock.patch.object(sys, "argv", ["image-lab", "--execute"]), \
                    mock.patch.object(LAB, "validate_host"), \
                    mock.patch.object(LAB, "verify_tool", side_effect=lambda name: self.tools[name]), \
                    mock.patch.object(REFERENCE, "RUNTIME_HASHES", {}), \
                    mock.patch.object(LAB.READINESS, "inspect_readiness", return_value={
                        "verificationPassed": verified, "backendLoaded": loaded}), \
                    mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                    mock.patch.object(LAB, "KernelImageLab") as factory, \
                    mock.patch.object(LAB.subprocess, "Popen") as launch, \
                    contextlib.redirect_stdout(output):
                code = LAB.main()
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")
            factory.assert_not_called()
            launch.assert_not_called()

    def test_tool_hash_mismatch_is_refused_even_with_matching_manifest_claim(self):
        relative, expected = LAB.TOOL_RECORDS["mkntfs"]
        binary = self.work / ".local-engine" / relative
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"Unreviewed executable: never run")
        binary.chmod(0o700)
        binary.with_name("binary-checksums.txt").write_text(expected + "  " + str(binary) + "\n")
        with mock.patch.object(LAB, "ROOT", self.work), \
                mock.patch.object(LAB.subprocess, "run") as run, \
                self.assertRaisesRegex(LAB.LabError, "differs from reviewed hash"):
            LAB.verify_tool("mkntfs")
        run.assert_not_called()

    def test_prepare_creates_only_new_private_regular_image_and_offline_fixture(self):
        self.assertEqual(LAB.IMAGE_BYTES, 256 * 1024 * 1024)
        lab = self.lab()
        with mock.patch.object(LAB, "IMAGE_BYTES", 4096), \
                mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(lab, "run_tool") as tool:
            lab.prepare()
            lab.verify_image()
        info = lab.image.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(info.st_size, 4096)
        self.assertEqual((lab.work / lab.fixture_name).read_bytes(), lab.expected)
        self.assertEqual(tool.call_args_list, [
            mock.call("mkntfs", "-F", "-Q", "-L", "NATIVOL_KERNEL_RO", lab.image),
            mock.call("ntfscp", lab.image, lab.work / lab.fixture_name, "/" + lab.fixture_name)])
        self.assertEqual(lab.report["imageSHA256BeforeMount"], lab.original_hash)
        self.assertTrue(lab.report["imageRetained"])

    def test_preexisting_image_is_never_formatted_or_overwritten(self):
        lab = self.lab()
        lab.image.write_bytes(b"Keep this existing file")
        with mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(lab, "run_tool") as tool, self.assertRaises(FileExistsError):
            lab.prepare()
        tool.assert_not_called()
        self.assertEqual(lab.image.read_bytes(), b"Keep this existing file")

    def test_image_replacement_or_symlink_is_refused_before_offline_tool(self):
        lab = self.lab()
        self.small_image(lab)
        original = lab.image.with_suffix(".original")
        lab.image.rename(original)
        lab.image.symlink_to(original)
        with mock.patch.object(LAB, "IMAGE_BYTES", 4096), \
                mock.patch.object(LAB.subprocess, "run") as run, self.assertRaises(LAB.LabError):
            lab.run_tool("mkntfs", "-F", lab.image)
        run.assert_not_called()
        self.assertEqual(original.read_bytes(), bytes(4096))

    def test_fresh_readiness_is_rechecked_before_driver_launch(self):
        lab = self.lab()
        lab.original_hash = "baseline"
        with mock.patch.object(lab, "verify_image"), \
                mock.patch.object(LAB.READINESS, "inspect_readiness", return_value={
                    "verificationPassed": True, "backendLoaded": False}), \
                mock.patch.object(LAB.subprocess, "Popen") as launch, \
                self.assertRaisesRegex(LAB.LabError, "not loaded"):
            lab.mount()
        launch.assert_not_called()
        self.assertFalse((lab.work / "reference.log").exists())

    def test_mount_options_are_fixed_and_exact_image_source_is_required(self):
        lab = self.lab()
        lab.original_hash = "baseline"
        candidate = dict(self.candidate(lab), source="/dev/disk999")
        with mock.patch.object(lab, "verify_image"), \
                mock.patch.object(LAB, "inspect_readiness", return_value=self.record["kernelReadiness"]), \
                mock.patch.object(LAB, "verify_tool", return_value=self.tools["ntfs-3g"]), \
                mock.patch.object(lab, "entry", side_effect=[None, candidate]), \
                mock.patch.object(LAB.subprocess, "Popen", return_value=lab.server) as launch, \
                self.assertRaisesRegex(LAB.LabError, "source differs"):
            lab.mount()
        self.addCleanup(lab.server_log.close)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[:4], [str(self.tools["ntfs-3g"]), str(lab.image), str(lab.mountpoint), "-o"])
        self.assertEqual(set(arguments[4].split(",")), {"backend=kernel", "local", "no_def_opts", "norecover",
            "ro", "no_detach", "quiet", "uid=" + str(os.getuid()), "gid=" + str(os.getgid()),
            "umask=077", "volname=" + lab.mountpoint.name})
        self.assertEqual(launch.call_args.kwargs["env"], LAB.ENV)
        self.assertIsNone(lab.verified_mount)
        lab.server.terminate.assert_not_called()

    def test_descriptor_nonce_read_has_no_write_open_and_requires_kernel_identity(self):
        lab = self.lab()
        lab.mountpoint = self.work / "mock-mounted-directory"
        lab.mountpoint.mkdir()
        (lab.mountpoint / lab.fixture_name).write_bytes(lab.expected)
        candidate = self.candidate(lab)
        flags_seen, real_open = [], os.open

        def read_open(path, flags, *args, **kwargs):
            flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(REFERENCE, "descriptor_mount", return_value=candidate), \
                mock.patch.object(REFERENCE.os, "open", side_effect=read_open):
            result = REFERENCE.inspect_fixture(lab.mountpoint, lab.fixture_name, lab.expected, candidate)
        self.assertEqual(result["verifiedMount"], candidate)
        self.assertTrue(result["effectiveReadOnly"])
        self.assertTrue(all(flags & os.O_NOFOLLOW for flags in flags_seen))
        self.assertTrue(all(not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) for flags in flags_seen))
        for change in ({"extendedFlags": REFERENCE.MNT_EXT_FSKIT}, {"filesystemType": "apfs"},
                       {"ownerUID": os.getuid() + 1}, {"filesystemID": [0, 0]}):
            with self.subTest(change=change), \
                    mock.patch.object(REFERENCE, "descriptor_mount", return_value=dict(candidate, **change)), \
                    self.assertRaises(LAB.LabError):
                REFERENCE.inspect_fixture(lab.mountpoint, lab.fixture_name, lab.expected, candidate)
        with mock.patch.object(REFERENCE, "descriptor_mount", return_value=candidate), \
                self.assertRaisesRegex(LAB.LabError, "Nonce fixture"):
            REFERENCE.inspect_fixture(lab.mountpoint, lab.fixture_name, b"x" * len(lab.expected), candidate)

    def test_zero_driver_exit_without_mount_fails_and_retains_unchanged_image(self):
        lab = self.lab()
        lab.original_hash = "baseline"
        lab.server.poll.return_value = 0
        with mock.patch.object(lab, "verify_image"), \
                mock.patch.object(LAB, "inspect_readiness", return_value=self.record["kernelReadiness"]), \
                mock.patch.object(LAB, "verify_tool", return_value=self.tools["ntfs-3g"]), \
                mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(LAB.subprocess, "Popen", return_value=lab.server), \
                mock.patch.object(LAB.subprocess, "run") as run, \
                mock.patch.object(lab, "hash_image", return_value="baseline"):
            with self.assertRaisesRegex(LAB.LabError, "without a verified OS mount"):
                lab.mount()
            self.assertTrue(lab.cleanup())
        self.assertEqual(lab.report["serverExitCode"], 0)
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertTrue(lab.report["imageUnchanged"])
        self.assertTrue(lab.report["imageRetained"])
        run.assert_not_called()
        lab.server.terminate.assert_not_called()

    def test_missing_ro_fails_but_proven_ownership_allows_normal_cleanup(self):
        lab = self.lab()
        candidate = dict(self.candidate(lab), flags=0)
        receiver, sender, worker, context = (mock.Mock() for _ in range(4))
        receiver.poll.return_value = True
        receiver.recv.return_value = {"verifiedMount": candidate, "effectiveReadOnly": False}
        worker.is_alive.return_value, worker.exitcode = False, 0
        context.Pipe.return_value, context.Process.return_value = (receiver, sender), worker
        with mock.patch.object(REFERENCE.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(lab, "entry", return_value=candidate), \
                self.assertRaisesRegex(LAB.LabError, "MNT_RDONLY is missing"):
            lab.inspect(candidate)
        self.assertEqual(lab.verified_mount, candidate)
        self.assertTrue(lab.report["ownershipVerified"])
        self.assertTrue(any("NTFS image nonce" in check for check in lab.report["checks"]))
        with mock.patch.object(lab, "entry", side_effect=[candidate, None, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            self.assertTrue(lab.cleanup())
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/diskutil", "unmount", str(lab.mountpoint)])
        self.assertTrue(lab.report["cleanUnmount"])
        self.assertNotIn("inspectionPassed", lab.report)

    def test_unverified_or_changed_mount_is_never_unmounted_or_server_terminated(self):
        for changed in (False, True):
            lab = self.lab()
            candidate = self.candidate(lab)
            lab.report["cleanupRequired"] = True
            lab.original_hash = "baseline"
            lab.verified_mount = dict(candidate, filesystemID=[99, 88]) if changed else None
            with self.subTest(changed=changed), mock.patch.object(lab, "entry", return_value=candidate), \
                    mock.patch.object(LAB.subprocess, "run") as run, \
                    mock.patch.object(lab, "hash_image") as digest:
                self.assertFalse(lab.cleanup())
            run.assert_not_called()
            digest.assert_not_called()
            lab.server.terminate.assert_not_called()
            self.assertTrue(lab.report["cleanupRequired"])

    def test_changed_image_fails_after_clean_unmount_without_inventing_active_cleanup(self):
        lab = self.lab()
        candidate = self.candidate(lab)
        lab.verified_mount, lab.original_hash = candidate, "baseline"
        with mock.patch.object(lab, "entry", side_effect=[candidate, None, None, None]), \
                mock.patch.object(LAB.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")), \
                mock.patch.object(lab, "hash_image", return_value="changed"):
            self.assertFalse(lab.cleanup())
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertTrue(lab.report["cleanUnmount"])
        self.assertFalse(lab.report["imageUnchanged"])
        self.assertIn("imageIntegrityError", lab.report)

    def test_vanished_verified_mount_remains_failed_with_retained_hash_evidence(self):
        lab = self.lab()
        lab.verified_mount, lab.original_hash = self.candidate(lab), "baseline"
        lab.server.poll.return_value = 0
        with mock.patch.object(lab, "entry", return_value=None), \
                mock.patch.object(LAB.subprocess, "run") as run, \
                mock.patch.object(lab, "hash_image", return_value="baseline"):
            self.assertFalse(lab.cleanup())
        self.assertFalse(lab.report["cleanupRequired"])
        self.assertFalse(lab.report["cleanUnmount"])
        self.assertTrue(lab.report["imageUnchanged"])
        self.assertTrue(lab.report["imageRetained"])
        run.assert_not_called()
        lab.server.terminate.assert_not_called()

    def fake_finished_lab(self, report, clean_unmount=True, unchanged=True):
        lab = mock.Mock()
        lab.work = self.work

        def cleanup():
            report.update({"cleanUnmount": clean_unmount, "imageUnchanged": unchanged})
            return True

        lab.cleanup.side_effect = cleanup
        return lab

    def test_pass_requires_inspection_normal_unmount_and_unchanged_image(self):
        for clean_unmount, unchanged in ((False, True), (True, False), (True, True)):
            output = io.StringIO()
            with self.subTest(clean_unmount=clean_unmount, unchanged=unchanged), \
                    mock.patch.object(sys, "argv", ["image-lab", "--execute"]), \
                    mock.patch.object(LAB, "preflight", return_value=(self.tools, self.record)), \
                    mock.patch.object(LAB.os, "umask"), \
                    mock.patch.object(LAB, "KernelImageLab", side_effect=lambda tools, report:
                        self.fake_finished_lab(report, clean_unmount, unchanged)), \
                    contextlib.redirect_stdout(output):
                code = LAB.main()
            report = json.loads(output.getvalue())
            self.assertEqual(code == 0, clean_unmount and unchanged)
            self.assertEqual(report["status"] == "passed", clean_unmount and unchanged)
            self.assertTrue(report["inspectionPassed"])
            self.assertFalse(report["mountedWriteAttemptPerformed"])
            (self.work / "report.json").unlink()

    def test_report_save_failure_turns_otherwise_successful_run_into_failure(self):
        (self.work / "report.json").write_bytes(b"Existing report must remain")
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["image-lab", "--execute"]), \
                mock.patch.object(LAB, "preflight", return_value=(self.tools, self.record)), \
                mock.patch.object(LAB.os, "umask"), \
                mock.patch.object(LAB, "KernelImageLab", side_effect=lambda tools, report:
                    self.fake_finished_lab(report)), contextlib.redirect_stdout(output):
            code = LAB.main()
        self.assertEqual(code, 1)
        self.assertIn("reportSaveError", json.loads(output.getvalue()))
        self.assertEqual((self.work / "report.json").read_bytes(), b"Existing report must remain")

    def test_stuck_worker_uses_bounded_shutdown_and_explicit_hard_exit_flag(self):
        lab = self.lab()
        receiver, sender, worker, context = (mock.Mock() for _ in range(4))
        worker.is_alive.return_value = True
        context.Pipe.return_value, context.Process.return_value = (receiver, sender), worker
        with mock.patch.object(REFERENCE.multiprocessing, "get_context", return_value=context), \
                self.assertRaisesRegex(LAB.LabError, "exceeded 20 seconds"):
            lab.inspect(self.candidate(lab))
        self.assertTrue(lab.report["inspectionWorkerStillRunning"])
        worker.terminate.assert_called_once()
        worker.kill.assert_called_once()
        self.assertEqual(worker.join.call_args_list, [mock.call(timeout=20), mock.call(timeout=2), mock.call(timeout=2)])
        lab.server.terminate.assert_not_called()
        with mock.patch.object(REFERENCE.os, "_exit") as hard_exit, \
                mock.patch.object(sys.stdout, "flush"), mock.patch.object(sys.stderr, "flush"):
            REFERENCE.finish_supervisor(1, lab.report["inspectionWorkerStillRunning"])
        hard_exit.assert_called_once_with(1)

    def test_spawned_worker_imports_kernel_profile_without_mounting(self):
        self.assertIs(sys.modules["nativol_kernel_image_reference"], REFERENCE)
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=REFERENCE.inspect_worker,
            args=(sender, self.work / "missing-private-path", "never-opened", b"", {}))
        try:
            worker.start()
            sender.close()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
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
