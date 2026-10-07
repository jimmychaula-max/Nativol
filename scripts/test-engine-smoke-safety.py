#!/usr/bin/env python3
"""Exercise the image harness safety boundary using mocked engine processes.

No NTFS executable, disk image mount, or device operation is executed. Test files
are confined to newly created temporary directories and removed on completion.
"""
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


SCRIPT = Path(__file__).with_name("engine-smoke.py")
SPEC = importlib.util.spec_from_file_location("nativol_engine_smoke", SCRIPT)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class EngineSmokeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="nativol-harness-check-")
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.prefix = self.work / "engine"
        (self.prefix / "bin").mkdir(parents=True)
        for name in ["mkntfs", "ntfscp", "ntfscat", "ntfsinfo"]:
            tool = self.prefix / "bin" / name
            tool.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            tool.chmod(0o700)
        self.report_path = self.work / "report.json"
        self.calls = []
        self.payloads = {}
        self.image_path = None

    def invoke(self, *, extra=(), runner=None):
        output = io.StringIO()
        argv = [str(SCRIPT), "--engine-prefix", str(self.prefix), "--report", str(self.report_path), *extra]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(SMOKE.subprocess, "run", side_effect=runner or self.fake_engine) as process, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            result = SMOKE.main()
        report = json.loads(output.getvalue())
        self.assertEqual(report, json.loads(self.report_path.read_text(encoding="utf-8")))
        return result, report, process.call_count

    def fake_engine(self, command, **options):
        """Emulate payload storage only; inspect the exact proposed command boundary."""
        self.calls.append(command)
        self.assertIsInstance(command, list)
        self.assertNotIn("shell", options)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertGreater(options["timeout"], 0)
        self.assertLessEqual(options["timeout"], 60)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", options["env"])
        name = Path(command[0]).name
        arguments = command[1:]
        stdout = b""
        returncode = 0
        if name == "mkntfs" and "--version" in arguments:
            stdout = b"mkntfs mock engine for harness tests\n"
        elif name == "mkntfs":
            self.image_path = Path(arguments[-1])
            self.assertTrue(self.image_path.is_file())
            self.assertEqual(self.image_path.stat().st_size, 256 * 1024 * 1024)
            self.assertEqual(self.image_path.parent, Path(options["cwd"]))
        elif name == "ntfscp":
            self.assertEqual(Path(arguments[0]), self.image_path)
            self.payloads[arguments[2]] = Path(arguments[1]).read_bytes()
        elif name == "ntfscat":
            target = Path(arguments[0])
            self.assertEqual(target.parent, self.image_path.parent)
            if target == self.image_path:
                stdout = self.payloads[arguments[1]]
            else:
                self.assertEqual(target.name, "not-ntfs.img")
                self.assertTrue(target.is_file())
                returncode = 1
        elif name == "ntfsinfo":
            self.assertEqual(Path(arguments[-1]), self.image_path)
        else:
            self.fail("Unexpected engine command: " + name)
        self.assertFalse(any(argument.startswith("/dev/") for argument in arguments))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")

    def test_mocked_success_is_limited_to_a_private_unmounted_image(self):
        with mock.patch.dict(os.environ, {"DYLD_INSERT_LIBRARIES": "/tmp/untrusted.dylib"}):
            result, report, count = self.invoke()
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["mountedFilesystemTested"])
        self.assertFalse(report["physicalDevicesAccessed"])
        self.assertGreater(count, 10)
        self.assertFalse(self.image_path.exists(), "Disposable image must be cleaned up")

    def test_root_and_mismatched_effective_user_are_rejected_before_any_process(self):
        for real_uid, effective_uid in [(0, 0), (501, 0), (501, 502)]:
            with self.subTest(real_uid=real_uid, effective_uid=effective_uid), \
                    mock.patch.object(SMOKE.os, "getuid", return_value=real_uid), \
                    mock.patch.object(SMOKE.os, "geteuid", return_value=effective_uid), \
                    mock.patch.object(SMOKE.subprocess, "run") as process:
                with self.assertRaises(SystemExit) as raised:
                    self.invoke()
                self.assertNotEqual(raised.exception.code, 0)
                process.assert_not_called()
        self.assertFalse(self.report_path.exists())

    def test_device_or_existing_file_target_arguments_are_not_accepted(self):
        sentinel = self.work / "valuable-image.img"
        sentinel.write_bytes(b"Keep these files")
        for arguments in [("--target", "/dev/disk4"), ("--image", str(sentinel)), (str(sentinel),)]:
            with self.subTest(arguments=arguments), mock.patch.object(SMOKE.subprocess, "run") as process:
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(extra=arguments)
                self.assertNotEqual(raised.exception.code, 0)
                process.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"Keep these files")

    def test_tool_symlink_cannot_escape_supplied_engine_directory(self):
        outside = self.work / "outside-tool"
        outside.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        outside.chmod(0o700)
        tool = self.prefix / "bin" / "mkntfs"
        tool.unlink()
        tool.symlink_to(outside)
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_timeout_records_failure_and_cleans_up_image(self):
        def timed_out(command, **options):
            self.image_path = Path(command[-1])
            raise subprocess.TimeoutExpired(command, options["timeout"])

        result, report, count = self.invoke(runner=timed_out)
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("TimeoutExpired", report["error"])
        self.assertEqual(count, 1)
        self.assertEqual(report["completedChecks"], 0)
        self.assertFalse(self.image_path.exists())

    def test_replaced_image_is_rejected_before_next_engine_operation(self):
        def substitute(command, **options):
            completed = self.fake_engine(command, **options)
            self.image_path.rename(self.image_path.with_suffix(".original"))
            self.image_path.write_bytes(b"replacement inode")
            return completed

        result, report, count = self.invoke(runner=substitute)
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("identity changed", report["error"])
        self.assertEqual(count, 1, "Replacement must be blocked before a second engine process")

    def test_symlink_substitution_never_follows_a_different_target(self):
        sentinel = self.work / "keep-this-file"
        sentinel.write_bytes(b"Keep these files")

        def substitute(command, **options):
            completed = self.fake_engine(command, **options)
            self.image_path.unlink()
            self.image_path.symlink_to(sentinel)
            return completed

        result, report, count = self.invoke(runner=substitute)
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("identity changed", report["error"])
        self.assertEqual(count, 1)
        self.assertEqual(sentinel.read_bytes(), b"Keep these files")

    def test_corrupt_readback_fails_hash_verification(self):
        def corrupt(command, **options):
            completed = self.fake_engine(command, **options)
            if Path(command[0]).name == "ntfscat":
                completed.stdout = b"not the saved payload"
            return completed

        result, report, _ = self.invoke(runner=corrupt)
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("Readback mismatch", report["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
