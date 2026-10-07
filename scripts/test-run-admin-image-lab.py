#!/usr/bin/env python3
"""Unprivileged launcher tests; every subprocess call is mocked.

Fixtures are inert bytes, never executable tools. The generated administrator
command is parsed and compiled for syntax only; it is never evaluated.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "admin_launcher", Path(__file__).with_name("run-admin-image-lab.py"))
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)
REAL_MKDTEMP = tempfile.mkdtemp


@unittest.skipUnless(sys.platform == "darwin" and os.getuid() != 0,
                     "Requires an ordinary macOS user; never run as root")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nativol-launcher-test-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Documents" / "Nativol"
        self.root.mkdir(parents=True)
        self.sources, self.pins = {}, {}
        for name in LAUNCHER.PINS:
            content = ("Inert launcher test fixture: " + name + "\n").encode()
            source = self.root / name
            source.write_bytes(content)
            self.sources[name] = source
            self.pins[name] = hashlib.sha256(content).hexdigest()
        self.payloads = []
        self.addCleanup(self.remove_payloads)
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        patches = [patch.object(LAUNCHER, "ROOT", self.root),
                   patch.object(LAUNCHER, "SOURCES", self.sources),
                   patch.object(LAUNCHER, "PINS", self.pins),
                   patch.object(LAUNCHER.platform, "machine", return_value="x86_64")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        call_patch = patch.object(LAUNCHER.subprocess, "run",
                                  side_effect=AssertionError("Unexpected subprocess invocation"))
        self.run_mock = call_patch.start()
        self.addCleanup(call_patch.stop)
        temp_patch = patch.object(LAUNCHER.tempfile, "mkdtemp", side_effect=self.make_payload)
        self.temp_mock = temp_patch.start()
        self.addCleanup(temp_patch.stop)

    def make_payload(self, *args, **kwargs):
        self.assertEqual(args, ())
        self.assertEqual(kwargs, {"prefix": "nativol-admin-input-", "dir": "/private/tmp"})
        path = REAL_MKDTEMP(**kwargs)
        self.payloads.append(Path(path))
        return path

    def remove_payloads(self):
        for payload in self.payloads:
            # Delete only directories created by this test, never a root stage.
            self.assertEqual(payload.parent, Path("/private/tmp"))
            self.assertTrue(payload.name.startswith("nativol-admin-input-"))
            shutil.rmtree(payload)

    def invoke(self, *arguments):
        with patch.object(sys, "argv", ["run-admin-image-lab.py", *arguments]), contextlib.redirect_stdout(io.StringIO()):
            return LAUNCHER.main()

    def mock_result(self, stdout, returncode=0, stderr=""):
        self.run_mock.side_effect = None
        self.run_mock.return_value = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def envelope(self, exit_code=1, diagnostic=None):
        return "NATIVOL_RESULT=" + json.dumps({
            "rootStage": "/private/tmp/nativol-admin-ro.INERT",
            "runnerExit": exit_code,
            "runnerStdout": json.dumps(diagnostic or {"status": "failed", "imageUnchanged": True}),
            "runnerStderr": "inert diagnostic stderr"}) + "\n"

    def raw_result(self):
        path = self.payloads[-1] / "authorization-result.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        return json.loads(path.read_text())

    def latest_report(self):
        reports = list((self.root / "local-reports").glob("admin-image-*.json"))
        return json.loads(max(reports, key=lambda p: p.stat().st_mtime_ns).read_text())

    def test_prepare_only_copies_private_pinned_inputs_without_authorization(self):
        self.assertEqual(self.invoke(), 0)
        self.run_mock.assert_not_called()
        self.temp_mock.assert_called_once_with(prefix="nativol-admin-input-", dir="/private/tmp")
        payload = self.payloads[0]
        self.assertEqual(payload.resolve(), payload)
        self.assertEqual(payload.stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o700)
        for name, expected in self.pins.items():
            target = payload / name
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), expected)
        command = shlex.split((payload / "root-command.sh").read_text())
        self.assertEqual(command[:-1], ["/usr/bin/env", "-i", "PATH=" + LAUNCHER.ENV["PATH"],
                                       "LC_ALL=en_US.UTF-8", "/usr/bin/python3", "-I", "-S", "-c"])
        self.assertNotIn(str(self.root), command[-1])
        compile(command[-1], "<unevaluated administrator bootstrap>", "exec")

    def test_target_arguments_rejected_before_reading_sources(self):
        for arguments in (("/dev/disk2",), ("--image", "test.img"),
                          ("--execute", "/dev/disk2"), ("--report", "elsewhere.json")):
            with self.subTest(arguments=arguments), patch.object(
                    LAUNCHER, "verified_bytes", side_effect=AssertionError("Must reject before reading")):
                with self.assertRaisesRegex(RuntimeError, "no target arguments accepted"):
                    self.invoke(*arguments)
        self.run_mock.assert_not_called()
        self.assertEqual(self.payloads, [])

    def test_changed_source_prevents_authorization(self):
        self.sources["ntfs-3g"].write_text("Changed inert fixture\n")
        with self.assertRaisesRegex(RuntimeError, "Pinned source checksum mismatch"):
            self.invoke("--execute")
        self.run_mock.assert_not_called()
        self.assertEqual(self.payloads, [])

    def test_execute_uses_private_tmp_cwd_and_clean_environment(self):
        self.mock_result(self.envelope(0, {"status": "passed"}))
        self.assertEqual(self.invoke("--execute"), 0)
        args, kwargs = self.run_mock.call_args
        self.assertEqual(args[0][:3], ["/usr/bin/osascript", "-e", LAUNCHER.APPLE_SCRIPT])
        self.assertEqual(kwargs["cwd"], "/private/tmp")
        self.assertEqual(kwargs["env"], LAUNCHER.ENV)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(args[0][3], (self.payloads[-1] / "root-command.sh").read_text().strip())
        self.assertEqual(self.latest_report()["status"], "passed")

    def test_failed_diagnostic_keeps_raw_output_and_returns_failure(self):
        stdout = self.envelope()
        self.mock_result(stdout)
        self.assertEqual(self.invoke("--execute"), 1)
        self.assertEqual(self.raw_result()["authorizationStdout"], stdout)
        self.assertEqual(self.latest_report()["status"], "diagnostic-failed")

    def test_malformed_diagnostics_keep_raw_output(self):
        for value in ("{", "[]", json.dumps({"runnerExit": 0, "runnerStdout": "[]"}),
                      json.dumps({"runnerExit": 0, "runnerStdout": "{"})):
            with self.subTest(value=value):
                stdout = "NATIVOL_RESULT=" + value + "\n"
                self.mock_result(stdout)
                self.assertEqual(self.invoke("--execute"), 1)
                self.assertEqual(self.raw_result()["authorizationStdout"], stdout)
                self.assertEqual(self.latest_report()["status"], "invalid-diagnostic-output")

    def test_bootstrap_failure_keeps_stdout_stderr_and_exit_code(self):
        self.mock_result("NATIVOL_ROOT_STAGE=/private/tmp/nativol-admin-ro.INERT\n", 1, "inert failure")
        self.assertEqual(self.invoke("--execute"), 1)
        self.assertEqual(self.raw_result()["authorizationExit"], 1)
        self.assertEqual(self.raw_result()["authorizationStderr"], "inert failure")
        self.assertEqual(self.latest_report()["status"], "bootstrap-failed")

    def test_report_directory_failure_does_not_lose_raw_output(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / "local-reports").symlink_to(other, target_is_directory=True)
        stdout = self.envelope()
        self.mock_result(stdout)
        with self.assertRaisesRegex(RuntimeError, "output retained in"):
            self.invoke("--execute")
        self.assertEqual(self.raw_result()["authorizationStdout"], stdout)
        self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
