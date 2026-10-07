#!/usr/bin/env python3
"""Mocked target/refusal regressions. Does not run an engine or mount."""
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("nativol_fd_image_tests", Path(__file__).with_name("kernel-fd-image-lab.py"))
LAB = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = LAB
spec.loader.exec_module(LAB)


class FDTests(unittest.TestCase):
    def test_default_prepares_without_image_or_mount(self):
        with patch.object(sys, "argv", ["lab"]), patch.object(LAB.WRITE, "preflight", return_value=({}, {"scriptSHA256": {}})), \
             patch.object(LAB, "FDLab") as create, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(LAB.main(), 0)
            create.assert_not_called()

    def test_no_target_cli(self):
        for args in (["/dev/disk2s1"], ["--execute", "/tmp/image"], ["--fd", "3"]):
            with patch.object(sys, "argv", ["lab", *args]), patch.object(LAB.WRITE, "preflight") as preflight:
                self.assertEqual(LAB.main(), 2)
                preflight.assert_not_called()

    def stage(self):
        stage = object.__new__(LAB.FDStage)
        stage.image = Path("/private/held.ntfs")
        stage.mountpoint = Path("/Volumes/Nativol-test")
        stage.owner = MagicMock(replaced_path=Path("/private/disposable.ntfs"))
        return stage

    def test_same_descriptor_source_at_other_mount_refused(self):
        stage = self.stage()
        with patch.object(LAB.REFERENCE, "mount_table", return_value=[{"source": "/dev/fd/3", "mountPoint": "/Volumes/other"}]):
            with self.assertRaises(LAB.LabError): stage.entry()

    def test_ownership_requires_exact_descriptor_source(self):
        stage = self.stage()
        stage.verified_mount = {"source": "/dev/disk2s1"}
        stage.server = MagicMock()
        stage.server.poll.return_value = None
        with patch.object(stage, "entry", return_value=stage.verified_mount):
            with self.assertRaises(LAB.LabError): stage.require_current_ownership()
        stage.owner.verify_image.assert_not_called()

    def test_ownership_requires_live_owned_server(self):
        stage = self.stage()
        stage.verified_mount = {"source": "/dev/fd/3"}
        stage.server = MagicMock()
        stage.server.poll.return_value = 0
        with self.assertRaises(LAB.LabError): stage.require_current_ownership()

    def test_stage_class_override_restored_on_failure(self):
        lab = object.__new__(LAB.FDLab)
        original = LAB.WRITE.ImageStage
        with patch.object(LAB.WRITE.WriteImageLab, "run_stage", side_effect=LAB.LabError("fixture failed")):
            with self.assertRaises(LAB.LabError): lab.run_stage("rw")
        self.assertIs(LAB.WRITE.ImageStage, original)

    def test_replacement_mutation_fails_pass_gate(self):
        lab = object.__new__(LAB.FDLab)
        lab.report = {}
        lab.replaced_path = Path("/private/new-image")
        lab.replacement_hash = "before"
        with patch.object(LAB.WRITE.WriteImageLab, "execute"), patch.object(lab, "assert_detached"), \
             patch.object(LAB.REFERENCE, "digest", return_value="after"):
            with self.assertRaises(LAB.LabError): lab.execute()
        self.assertFalse(lab.report["heldDescriptorBindingPassed"])


if __name__ == "__main__": unittest.main()
