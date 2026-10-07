#!/usr/bin/env python3
"""Loaded-state and error regressions; no real commands or kernel operations."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("kernel_readiness", Path(__file__).with_name("kernel-readiness.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)
APPLE_ROW = "1 155 0 0 0 com.apple.kpi.bsd (24.6.0) UUID <>\n"
FUSE_ROW = "227 0 0xffffff8001000000 0x1000 0x1000 " + LAB.BUNDLE_ID + " (5.4.0) UUID <>\n"


class KernelReadinessChecks(unittest.TestCase):
    def inspect(self, result=None, installation_error=None, command_error=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "platform", "darwin"))
            stack.enter_context(mock.patch.object(LAB.platform, "machine", return_value="x86_64"))
            stack.enter_context(mock.patch.object(LAB.platform, "mac_ver", return_value=("15.7.9", (), "")))
            for name, value in [("getuid", 501), ("geteuid", 501), ("getgid", 20), ("getegid", 20)]:
                stack.enter_context(mock.patch.object(LAB.os, name, return_value=value))
            stack.enter_context(mock.patch.object(LAB, "verify_installation", return_value={"version": "5.4.0"},
                                                  side_effect=installation_error))
            command = stack.enter_context(mock.patch.object(LAB, "command", return_value=result,
                                                              side_effect=command_error))
            report = LAB.inspect_readiness()
            command.assert_called_once_with(["/usr/bin/kmutil", "showloaded", "--list-only"])
            return report

    def test_installed_files_do_not_imply_loaded_or_approved(self):
        report = self.inspect(subprocess.CompletedProcess([], 0, APPLE_ROW, ""))
        self.assertTrue(report["verificationPassed"])
        self.assertFalse(report["backendLoaded"])
        self.assertEqual(report["loadedState"], "not-loaded")
        self.assertEqual(report["approvalState"], "unknown")
        self.assertEqual(report["readiness"], "not-ready")

    def test_only_exact_loaded_identity_and_version_enable_candidate_status(self):
        for output, expected in [(APPLE_ROW + FUSE_ROW, True),
                                 (APPLE_ROW + FUSE_ROW.replace(LAB.BUNDLE_ID, LAB.BUNDLE_ID + ".unrelated"), False),
                                 (APPLE_ROW + FUSE_ROW.replace("(5.4.0)", "(5.3.0)"), False)]:
            with self.subTest(output=output):
                report = self.inspect(subprocess.CompletedProcess([], 0, output, ""))
                self.assertEqual(report["backendLoaded"], expected)
                self.assertEqual(report["approvalState"], "unknown")
                self.assertEqual(report["readiness"] == "verified-loaded-candidate", expected)

    def test_failed_or_empty_query_remains_unknown_even_when_files_verify(self):
        for result in [subprocess.CompletedProcess([], 1, FUSE_ROW, "permission denied"),
                       subprocess.CompletedProcess([], 0, "unexpected output\n", "")]:
            with self.subTest(result=result):
                report = self.inspect(result)
                self.assertTrue(report["verificationPassed"])
                self.assertFalse(report["backendLoaded"])
                self.assertEqual(report["loadedState"], "unknown")
                self.assertEqual(report["readiness"], "not-ready")
                self.assertTrue(report["errors"])

    def test_loaded_candidate_does_not_override_failed_installation_verification(self):
        report = self.inspect(subprocess.CompletedProcess([], 0, FUSE_ROW, ""),
                              installation_error=LAB.InspectionError("Signature does not match"))
        self.assertTrue(report["backendLoaded"])
        self.assertFalse(report["verificationPassed"])
        self.assertEqual(report["readiness"], "not-ready")

    def test_timeout_remains_unknown_and_records_no_authorization_or_load(self):
        report = self.inspect(command_error=subprocess.TimeoutExpired("metadata", 15))
        self.assertFalse(report["backendLoaded"])
        self.assertEqual(report["loadedState"], "unknown")
        self.assertFalse(report["loadAttempted"])
        self.assertFalse(report["authorizationRequested"])
        self.assertEqual(report["readiness"], "not-ready")

    def test_arguments_are_refused_before_inspecting_any_state(self):
        with mock.patch.object(sys, "argv", ["script", "/dev/disk2"]), \
                mock.patch.object(LAB, "inspect_readiness") as inspect, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(LAB.main(), 2)
        inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
