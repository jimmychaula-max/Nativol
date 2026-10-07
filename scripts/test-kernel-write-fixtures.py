#!/usr/bin/env python3
"""Private local-directory tests with mocked mount identity; no mount or engine."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_kernel_write_fixtures_tested", Path(__file__).with_name("kernel-write-fixtures.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class FixtureChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-fixture-check-")
        self.addCleanup(temporary.cleanup)
        self.mountpoint = Path(temporary.name).resolve()
        self.nonce = "3c" * 32
        self.fixture = "fixture-" + self.nonce + ".bin"
        self.expected = bytes.fromhex(self.nonce) + b"private test nonce\n"
        (self.mountpoint / self.fixture).write_bytes(self.expected)
        self.candidate = {"source": str(self.mountpoint / "disposable.ntfs"),
                          "mountPoint": str(self.mountpoint), "filesystemType": "macfuse",
                          "extendedFlags": 0, "flags": 0, "ownerUID": os.getuid(),
                          "filesystemID": [81, 93]}
        patch = mock.patch.object(LAB.REFERENCE, "descriptor_mount", side_effect=lambda fd: self.candidate.copy())
        self.mount_query = patch.start()
        self.addCleanup(patch.stop)
        self.area = self.mountpoint / LAB.NAMESPACE

    def exercise(self):
        return LAB.exercise(self.mountpoint, self.candidate, self.fixture, self.expected)

    def verify(self):
        self.candidate["flags"] = LAB.REFERENCE.MNT_RDONLY
        return LAB.verify(self.mountpoint, self.candidate, self.fixture, self.expected)

    def assertNoWorkload(self):
        self.assertFalse(self.area.exists())
        self.assertEqual((self.mountpoint / self.fixture).read_bytes(), self.expected)

    def test_complete_workload_and_independent_read_only_reopen(self):
        first = self.exercise()
        second = self.verify()
        self.assertEqual(first["status"], "passed")
        self.assertFalse(first["effectiveReadOnly"])
        self.assertTrue(second["effectiveReadOnly"])
        self.assertEqual(first["manifest"], second["manifest"])
        files = {item["path"].split("/", 1)[1]: item for item in first["manifest"]["files"]}
        self.assertEqual(files["large.bin"]["size"], 8 * 1024 * 1024)
        self.assertEqual(files["tiny-0.bin"]["size"], 0)
        self.assertEqual(files["tiny-3.bin"]["size"], 3)
        self.assertEqual(len(files), 14)
        self.assertLess(len(json.dumps(first)), LAB.MAX_REPORT)
        self.assertEqual((self.mountpoint / self.fixture).read_bytes(), self.expected)
        self.assertTrue(all(not (self.mountpoint / name).exists() for name in first["manifest"]["absent"]))

    def test_wrong_kernel_identity_rejected_before_writes(self):
        original = self.candidate.copy()
        variants = [{"filesystemType": "apfs"}, {"extendedFlags": 2},
                    {"ownerUID": os.getuid() + 1}, {"filesystemID": [0, 0]},
                    {"mountPoint": str(self.mountpoint) + "-other"}]
        for variant in variants:
            with self.subTest(variant=variant):
                self.candidate = {**original, **variant}
                with mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaises(LAB.LabError):
                    self.exercise()
                mkdir.assert_not_called()
                self.assertNoWorkload()

    def test_descriptor_mount_differs_from_candidate_before_writes(self):
        self.mount_query.side_effect = lambda fd: {**self.candidate, "filesystemID": [9, 10]}
        with self.assertRaises(LAB.LabError):
            self.exercise()
        self.assertNoWorkload()

    def test_wrong_nonce_rejected_before_writes(self):
        (self.mountpoint / self.fixture).write_bytes(b"bad" + self.expected[3:])
        with mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaises(LAB.LabError):
            self.exercise()
        mkdir.assert_not_called()

    def test_read_only_candidate_rejected_for_exercise(self):
        self.candidate["flags"] = LAB.REFERENCE.MNT_RDONLY
        with self.assertRaises(LAB.LabError):
            self.exercise()
        self.assertNoWorkload()

    def test_writable_candidate_rejected_for_verification(self):
        with self.assertRaises(LAB.LabError):
            LAB.verify(self.mountpoint, self.candidate, self.fixture, self.expected)
        self.assertNoWorkload()

    def test_device_and_unbounded_payload_rejected_before_writes(self):
        for source in ("/dev/disk2s1", "relative/disposable.ntfs", "/tmp/../disposable.ntfs"):
            with self.subTest(source=source):
                self.candidate["source"] = source
                with self.assertRaises(LAB.LabError):
                    self.exercise()
                self.assertNoWorkload()
        self.candidate["source"] = str(self.mountpoint / "disposable.ntfs")
        with self.assertRaises(LAB.LabError):
            LAB.exercise(self.mountpoint, self.candidate, self.fixture, self.expected + bytes(LAB.MAX_EXPECTED))
        self.assertNoWorkload()

    def test_nonce_symlink_is_not_followed(self):
        original = self.mountpoint / self.fixture
        original.unlink()
        target = self.mountpoint / "unrelated"
        target.write_bytes(self.expected)
        original.symlink_to(target)
        with self.assertRaises(OSError):
            self.exercise()
        self.assertFalse(self.area.exists())
        self.assertEqual(target.read_bytes(), self.expected)

    def test_preexisting_namespace_symlink_is_not_followed(self):
        unrelated = self.mountpoint / "unrelated"
        unrelated.mkdir()
        self.area.symlink_to(unrelated, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.exercise()
        self.assertEqual(list(unrelated.iterdir()), [])

    def test_create_does_not_follow_racing_target_symlink(self):
        unrelated = self.mountpoint / "unrelated"
        unrelated.write_bytes(b"preserve")
        real_create = LAB._Anchor.create

        def inject(anchor, name, data):
            if name == "tiny-1.bin":
                os.symlink(str(unrelated), name, dir_fd=anchor.fd)
            return real_create(anchor, name, data)

        with mock.patch.object(LAB._Anchor, "create", inject), self.assertRaises(FileExistsError):
            self.exercise()
        self.assertEqual(unrelated.read_bytes(), b"preserve")

    def test_partial_pwrite_completes_all_expected_bytes(self):
        real_write = os.pwrite
        with mock.patch.object(LAB.os, "pwrite", side_effect=lambda fd, data, offset: real_write(fd, data[:32767], offset)):
            result = self.exercise()
        self.assertEqual(result["manifest"], self.verify()["manifest"])

    def test_zero_pwrite_fails(self):
        with mock.patch.object(LAB.os, "pwrite", return_value=0), self.assertRaises(LAB.LabError):
            self.exercise()

    def test_short_pread_retries_and_eof_fails(self):
        real_read = os.pread
        with mock.patch.object(LAB.os, "pread", side_effect=lambda fd, size, offset: real_read(fd, min(size, 32767), offset)):
            self.exercise()
        with mock.patch.object(LAB.os, "pread", return_value=b""), self.assertRaises(LAB.LabError):
            self.verify()

    def test_corrupt_bytes_and_missing_file_are_detected_after_reopen(self):
        self.exercise()
        target = self.area / "tiny-7.bin"
        original = target.read_bytes()
        target.write_bytes(b"!" * len(original))
        with self.assertRaises(LAB.LabError):
            self.verify()
        target.write_bytes(original)
        target.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_short_file_detected_after_reopen(self):
        self.exercise()
        (self.area / "large.bin").write_bytes(b"short")
        with self.assertRaises(LAB.LabError):
            self.verify()

    def test_unperformed_delete_is_detected(self):
        self.exercise()
        (self.area / "deleted.bin").write_bytes(b"unexpected retained file")
        with self.assertRaises(LAB.LabError):
            self.verify()

    def test_readback_symlink_is_not_followed(self):
        self.exercise()
        target = self.area / "tiny-3.bin"
        contents = target.read_bytes()
        target.unlink()
        unrelated = self.mountpoint / "unrelated"
        unrelated.write_bytes(contents)
        target.symlink_to(unrelated)
        with self.assertRaises(OSError):
            self.verify()
        self.assertEqual(unrelated.read_bytes(), contents)

    def test_changed_regular_file_mount_prevents_pwrite(self):
        actual_fstat = os.fstat

        def identity(fd):
            info = actual_fstat(fd)
            if info.st_size == 0 and not stat.S_ISDIR(info.st_mode):
                return {**self.candidate, "filesystemID": [99, 99]}
            return self.candidate.copy()

        self.mount_query.side_effect = identity
        with mock.patch.object(LAB.os, "pwrite") as write, self.assertRaises(LAB.LabError):
            self.exercise()
        write.assert_not_called()

    def test_changed_file_inode_prevents_truncate(self):
        root = LAB._root(self.mountpoint, self.candidate, self.fixture, self.expected, False)
        try:
            root.create("owned.bin", b"owned")
            fd = root.open_existing("owned.bin", writable=True)
            try:
                identity = root.files["owned.bin"]
                with mock.patch.object(LAB.os, "ftruncate") as truncate, self.assertRaises(LAB.LabError):
                    root.truncate(fd, 0, (identity[0], identity[1] + 1))
                truncate.assert_not_called()
            finally:
                os.close(fd)
        finally:
            os.close(root.fd)

    def test_mount_change_before_namespace_creation_prevents_mkdir(self):
        original = LAB._root

        def changed(*args, **kwargs):
            anchor = original(*args, **kwargs)
            self.mount_query.side_effect = lambda fd: {**self.candidate, "filesystemID": [123, 456]}
            return anchor

        with mock.patch.object(LAB, "_root", changed), \
                mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaises(LAB.LabError):
            self.exercise()
        mkdir.assert_not_called()

    def test_fsync_failure_is_not_reported_as_success(self):
        with mock.patch.object(LAB.os, "fsync", side_effect=OSError("fsync failed")), self.assertRaises(OSError):
            self.exercise()

    def test_direct_cli_has_no_target_interface(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(Path(LAB.__file__)), str(self.mountpoint)],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no command-line targets", result.stderr)
        self.assertNoWorkload()


if __name__ == "__main__":
    unittest.main(verbosity=2)
