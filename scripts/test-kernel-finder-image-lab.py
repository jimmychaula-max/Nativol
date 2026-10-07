#!/usr/bin/env python3
"""Finder diagnostic guard tests. No engine execution or mounts."""
import copy
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("nativol_finder_tests", Path(__file__).with_name("kernel-finder-image-lab.py"))
LAB = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = LAB
spec.loader.exec_module(LAB)


class FinderTests(unittest.TestCase):
    def test_default_prepares_without_image(self):
        with patch.object(sys, "argv", ["lab"]), patch.object(LAB.WRITE, "preflight", return_value=({}, {"scriptSHA256": {}})), \
             patch.object(LAB, "FinderLab") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(LAB.main(), 0)
            create.assert_not_called()

    def test_no_target_or_options_cli(self):
        for args in (["/dev/disk2s1"], ["--execute", "/tmp/a.ntfs"], ["--option", "auto_xattr"]):
            with patch.object(sys, "argv", ["lab", *args]), patch.object(LAB.WRITE, "preflight") as preflight:
                self.assertEqual(LAB.main(), 2)
                preflight.assert_not_called()

    def test_failed_preflight_never_creates_image(self):
        with patch.object(sys, "argv", ["lab", "--execute"]), \
             patch.object(LAB.WRITE, "preflight", side_effect=LAB.LabError("not ready")), \
             patch.object(LAB, "FinderLab") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(LAB.main(), 1)
            create.assert_not_called()

    def stage(self):
        stage = object.__new__(LAB.FinderStage)
        stage.image = Path("/private/fresh/disposable.ntfs")
        stage.mountpoint = Path("/Volumes/Nativol-Finder-test")
        return stage

    def test_other_physical_session_is_not_this_image(self):
        stage = self.stage()
        with patch.object(LAB.REF, "mount_table", return_value=[{"source": "/dev/fd/3", "mountPoint": "/Volumes/physical"}]):
            self.assertIsNone(stage.entry())

    def test_same_image_at_other_mount_refused(self):
        stage = self.stage()
        with patch.object(LAB.REF, "mount_table", return_value=[{"source": str(stage.image), "mountPoint": "/Volumes/other"}]):
            with self.assertRaises(LAB.LabError): stage.entry()

    def test_failed_nonce_proof_prevents_metadata_writes(self):
        with patch.object(LAB.REF, "inspect_fixture", side_effect=LAB.LabError("bad nonce")), \
             patch.object(LAB.os, "mkdir") as create, patch.object(LAB.os, "open") as opened:
            with self.assertRaises(LAB.LabError):
                LAB.fixture("exercise", "native", Path("/Volumes/test"), {}, "nonce", b"x", Path("/tmp/helper"), "hash")
            create.assert_not_called(); opened.assert_not_called()

    def test_wrong_access_prevents_metadata_writes(self):
        with patch.object(LAB.REF, "inspect_fixture", return_value={"effectiveReadOnly": True}), \
             patch.object(LAB.os, "open") as opened:
            with self.assertRaises(LAB.LabError):
                LAB.fixture("exercise", "native", Path("/Volumes/test"), {}, "nonce", b"x", Path("/tmp/helper"), "hash")
            opened.assert_not_called()

    def test_stage_override_restored_on_failure(self):
        lab = object.__new__(LAB.FinderLab)
        original = LAB.WRITE.ImageStage
        with patch.object(LAB.WRITE.WriteImageLab, "run_stage", side_effect=LAB.LabError("failed")):
            with self.assertRaises(LAB.LabError): lab.run_stage("rw")
        self.assertIs(LAB.WRITE.ImageStage, original)

    def test_readback_difference_fails(self):
        lab = object.__new__(LAB.FinderLab); lab.report = {}
        with patch.object(lab, "prepare"), patch.object(lab, "run_stage", side_effect=[{}, {"xattr": "old"}, {"xattr": "new"}]), \
             patch.object(lab, "hash_image", return_value="same"):
            with self.assertRaises(LAB.LabError): lab.execute()
        self.assertFalse(lab.report["metadataManifestsMatch"])

    def test_pass_requires_all_clean_unmounts_and_unchanged_ro(self):
        record = {"readOnlyImageUnchanged": True, "metadataManifestsMatch": True,
                  "stages": [{"variant": variant, "phase": phase, "status": "passed", "ownershipVerified": True,
                    "workloadPassed": True, "cleanUnmount": True, "serverExitCode": 0}
                    for variant, phase in (("baseline", "rw"), ("native", "rw"), ("native", "ro"))]}
        self.assertTrue(LAB.success(record))
        for index in range(3):
            bad = copy.deepcopy(record); bad["stages"][index]["cleanUnmount"] = False
            self.assertFalse(LAB.success(bad))
        record["readOnlyImageUnchanged"] = False
        self.assertFalse(LAB.success(record))


if __name__ == "__main__": unittest.main()
