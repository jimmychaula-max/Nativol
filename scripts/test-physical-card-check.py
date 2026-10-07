#!/usr/bin/env python3
"""Private host-directory and mocked-authority checks; no devices or mounts."""
import contextlib
import copy
import ctypes
import errno
import hashlib
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
    "nativol_physical_card_checks_tested", Path(__file__).with_name("physical-card-check.py"))
LAB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LAB
SPEC.loader.exec_module(LAB)


class PhysicalChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-physical-check-unit-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.operation = "12345678-1234-4321-ABCD-123456789ABC"
        self.status_path = LAB.STATE_ROOT / (self.operation + ".json")
        self.version = LAB.VERSION_ROOT / ("a" * 64)
        self.driver_bytes = b"mock executable; never run"
        self.record = {"schemaVersion": 1, "state": "mounted", "mode": "rw",
            "operationId": self.operation, "ownerUID": os.getuid(),
            "mountPath": "/Volumes/Nativol-" + self.operation, "fsid": [71, 98],
            "source": "/dev/fd/3", "filesystem": "macfuse", "driverPID": 2345,
            "helperPID": 1234, "partitionRegistryID": "500001", "mediaRegistryID": "500000",
            "volumeSerial": "0123456789abcdef", "targetProfile": LAB.PROFILE,
            "driverExecutable": str(self.version / "bin/ntfs-3g"),
            "driverSHA256": hashlib.sha256(self.driver_bytes).hexdigest(),
            "helperVersionDir": str(self.version)}
        self.nonce = "ef" * 32
        self.manifest = LAB.make_manifest(self.nonce, self.record)
        self.mountpoint = self.work / "mock-mount"
        self.mountpoint.mkdir()
        self.candidate = {"source": "/dev/fd/3", "mountPoint": str(self.mountpoint),
            "filesystemType": "macfuse", "flags": 0, "extendedFlags": 0,
            "ownerUID": os.getuid(), "filesystemID": [71, 98]}

    def read_status(self, record=None, mode="rw"):
        with mock.patch.object(LAB, "read_protected", return_value=(json.dumps(record or self.record).encode(), (1, 2, 3, 4, 5))):
            return LAB.read_status(self.status_path, mode)

    def authority(self, mode="rw"):
        result = SimpleNamespace(record={**self.record, "mountPath": str(self.mountpoint), "mode": mode},
                                 mode=mode, guard=mock.Mock())
        return result

    def perform(self, mode="rw", manifest=None, authority=None):
        authority = authority or self.authority(mode)
        self.candidate["flags"] = 1 if mode == "ro" else 0
        with mock.patch.object(LAB, "Authority", return_value=authority), \
                mock.patch.object(LAB.REFERENCE, "descriptor_mount", side_effect=lambda fd: self.candidate.copy()):
            return LAB.perform(mode, self.status_path, manifest or self.manifest)

    def fake_info(self, mode=stat.S_IFREG | 0o644, uid=0, nlink=1, size=100, inode=2, mtime=10000000000):
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=nlink, st_size=size,
                               st_dev=1, st_ino=inode, st_mtime_ns=mtime, st_ctime_ns=mtime)

    def test_status_accepts_canonical_uppercase_uuid(self):
        result, identity = self.read_status()
        self.assertEqual(result, self.record)
        self.assertEqual(identity, (1, 2, 3, 4, 5))

    def test_status_rejects_wrong_phase_owner_profile_source_and_operation(self):
        variants = [{"state": "preparing"}, {"mode": "ro"}, {"ownerUID": os.getuid() + 1},
                    {"targetProfile": "any-drive"}, {"source": "/dev/disk2s1"},
                    {"filesystem": "ntfs"}, {"schemaVersion": True},
                    {"operationId": "22345678-1234-4321-abcd-123456789abc"}]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(LAB.LabError):
                self.read_status({**self.record, **variant})

    def test_status_rejects_untrusted_location_or_malformed_uuid_before_read(self):
        paths = [self.work / (self.operation + ".json"), LAB.STATE_ROOT / "bad.json",
                 LAB.STATE_ROOT / (self.operation + ".txt")]
        for path in paths:
            with self.subTest(path=path), mock.patch.object(LAB, "read_protected") as read, self.assertRaises(LAB.LabError):
                LAB.read_status(path, "rw")
            read.assert_not_called()

    def test_status_rejects_mountpoint_and_registry_serial_fsid_errors(self):
        variants = [{"mountPath": "/Volumes/Existing"}, {"mountPath": "/tmp/Nativol-" + self.operation},
                    {"fsid": [0, 0]}, {"fsid": [True, 1]}, {"fsid": [1]}, {"fsid": [1, 2 ** 32]},
                    {"partitionRegistryID": "0"}, {"mediaRegistryID": 123}, {"volumeSerial": "unknown"},
                    {"driverPID": True}, {"helperPID": 2345}]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(LAB.LabError):
                self.read_status({**self.record, **variant})

    def test_status_rejects_nonversioned_driver_and_bad_hash(self):
        for variant in ({"driverExecutable": "/usr/local/bin/ntfs-3g"},
                        {"driverExecutable": ""}, {"driverSHA256": "bad"}):
            with self.subTest(variant=variant), self.assertRaises(LAB.LabError):
                self.read_status({**self.record, **variant})

    def test_protected_path_rejects_unowned_writable_linked_or_symlinked_components(self):
        path = Path("/protected/state.json")
        for variant in ({"uid": os.getuid()}, {"mode": stat.S_IFREG | 0o666},
                        {"mode": stat.S_IFLNK | 0o777}, {"nlink": 2}):
            def info(current):
                if current == path:
                    return self.fake_info(**variant)
                return self.fake_info(mode=stat.S_IFDIR | 0o755)
            with self.subTest(variant=variant), mock.patch.object(Path, "lstat", info), self.assertRaises(LAB.LabError):
                LAB.protected_path(path)
        with mock.patch.object(Path, "lstat", return_value=self.fake_info(mode=stat.S_IFDIR | 0o775)), self.assertRaises(LAB.LabError):
            LAB.protected_path(path)

    def test_manifest_is_deterministic_and_includes_nonce_plus_fourteen_files(self):
        self.assertEqual(self.manifest, LAB.make_manifest(self.nonce, self.record))
        self.assertEqual(len(self.manifest["files"]), 15)
        self.assertEqual(len(self.manifest["absent"]), 5)
        self.assertNotIn("expectedVolumeSerial", self.manifest)
        self.assertEqual(LAB.validate_manifest(self.manifest, self.record), self.nonce)
        with_serial = LAB.make_manifest(self.nonce, {**self.record, "windowsVolumeSerial": "1234abcd"})
        self.assertEqual(with_serial["expectedVolumeSerial"], "1234ABCD")

    def test_manifest_rejects_changed_target_and_arbitrary_paths_or_hashes(self):
        mutations = []
        for key, value in (("testRoot", "../../arbitrary"), ("files", []), ("absent", ["../outside"])):
            item = copy.deepcopy(self.manifest)
            item[key] = value
            mutations.append(item)
        item = copy.deepcopy(self.manifest)
        item["files"][1]["sha256"] = "0" * 64
        mutations.append(item)
        for manifest in mutations:
            with self.subTest(manifest=manifest), self.assertRaises(LAB.LabError):
                LAB.validate_manifest(manifest, self.record)
        for key in ("volumeSerial", "partitionRegistryID", "mediaRegistryID"):
            with self.subTest(key=key), self.assertRaises(LAB.LabError):
                LAB.validate_manifest(self.manifest, {**self.record, key: "1111111111111111"})

    def test_authority_rejects_driver_hash_process_uid_parent_or_path_mismatch(self):
        driver = {"pid": 2345, "uid": os.getuid(), "ruid": os.getuid(), "ppid": 1234,
                  "path": self.record["driverExecutable"], "start": [2, 0]}
        helper = {"pid": 1234, "uid": 0, "ruid": 0, "ppid": 1,
                  "path": str(self.version / "bin/NativolHelper"), "start": [1, 0]}
        info = self.fake_info()
        info_identity = LAB.file_identity(info)
        variants = [{"uid": 0}, {"ppid": 999}, {"path": "/tmp/ntfs-3g"}, {"start": [20, 0]}]
        for variant in variants:
            with self.subTest(variant=variant), \
                    mock.patch.object(LAB, "read_status", return_value=(self.record, info_identity)), \
                    mock.patch.object(LAB, "read_protected", return_value=(self.driver_bytes, info_identity)), \
                    mock.patch.object(LAB, "protected_path", return_value=info), \
                    mock.patch.object(LAB, "process_identity", side_effect=lambda pid, privileged=False: {**driver, **variant} if pid == 2345 else helper), \
                    self.assertRaises(LAB.LabError):
                LAB.Authority(self.status_path, "rw")
        with mock.patch.object(LAB, "read_status", return_value=(self.record, info_identity)), \
                mock.patch.object(LAB, "read_protected", return_value=(b"changed", info_identity)), \
                mock.patch.object(LAB, "process_identity") as process, self.assertRaises(LAB.LabError):
            LAB.Authority(self.status_path, "rw")
        process.assert_not_called()

    def test_authority_guard_rejects_status_or_process_change(self):
        info = self.fake_info()
        driver = {"pid": 2345, "uid": os.getuid(), "ruid": os.getuid(), "ppid": 1234,
                  "path": self.record["driverExecutable"], "start": [2, 0]}
        helper = {"pid": 1234, "uid": 0, "ruid": 0, "ppid": 1,
                  "path": str(self.version / "bin/NativolHelper"), "start": [1, 0]}
        with mock.patch.object(LAB, "read_status", return_value=(self.record, LAB.file_identity(info))), \
                mock.patch.object(LAB, "read_protected", return_value=(self.driver_bytes, LAB.file_identity(info))), \
                mock.patch.object(LAB, "protected_path", return_value=info) as protected, \
                mock.patch.object(LAB, "process_identity", side_effect=lambda pid, privileged=False: driver if pid == 2345 else helper) as process:
            authority = LAB.Authority(self.status_path, "rw")
            protected.return_value = self.fake_info(inode=99)
            with self.assertRaises(LAB.LabError):
                authority.guard()
            protected.return_value = info
            process.side_effect = lambda pid, privileged=False: {**driver, "start": [3, 0]} if pid == 2345 else helper
            with self.assertRaises(LAB.LabError):
                authority.guard()

    def test_wrong_mount_source_fsid_owner_type_or_mode_prevents_writes(self):
        original = self.candidate.copy()
        variants = [{"source": "/dev/disk2s1"}, {"filesystemID": [9, 9]},
                    {"ownerUID": os.getuid() + 1}, {"filesystemType": "apfs"},
                    {"extendedFlags": 2}, {"flags": 1}]
        for variant in variants:
            self.candidate = {**original, **variant}
            with self.subTest(variant=variant), mock.patch.object(LAB, "Authority", return_value=self.authority()), \
                    mock.patch.object(LAB.REFERENCE, "descriptor_mount", return_value=self.candidate), \
                    mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaises(LAB.LabError):
                LAB.perform("rw", self.status_path, self.manifest)
            mkdir.assert_not_called()
        self.assertEqual(list(self.mountpoint.iterdir()), [])

    def test_full_mocked_mount_cycle_preserves_existing_unrelated_file(self):
        original = self.mountpoint / "unrelated-user-file.txt"
        original.write_bytes(b"do not touch")
        first = self.perform()
        second = self.perform("ro")
        self.assertEqual(first["status"], "passed")
        self.assertEqual(second["status"], "passed")
        self.assertEqual(second["filesVerified"], 15)
        self.assertEqual(first["windowsManifestSHA256"], second["windowsManifestSHA256"])
        self.assertEqual(original.read_bytes(), b"do not touch")
        self.assertEqual((self.mountpoint / LAB.CARD_MANIFEST).read_bytes(), LAB.encoded(self.manifest))

    def test_existing_card_manifest_refuses_before_new_directory_or_write(self):
        path = self.mountpoint / LAB.CARD_MANIFEST
        path.write_bytes(b"existing")
        with mock.patch.object(LAB.os, "mkdir") as mkdir, mock.patch.object(LAB.os, "pwrite") as write, self.assertRaises(LAB.LabError):
            self.perform()
        mkdir.assert_not_called()
        write.assert_not_called()
        self.assertEqual(path.read_bytes(), b"existing")

    def test_existing_unique_root_or_symlink_is_not_overwritten(self):
        root = self.mountpoint / self.manifest["testRoot"]
        unrelated = self.work / "unrelated-directory"
        unrelated.mkdir()
        root.symlink_to(unrelated, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.perform()
        self.assertEqual(list(unrelated.iterdir()), [])

    def test_root_symlink_is_not_followed(self):
        link = self.work / "linked-mount"
        link.symlink_to(self.mountpoint, target_is_directory=True)
        authority = self.authority()
        authority.record["mountPath"] = str(link)
        with self.assertRaises(OSError):
            self.perform(authority=authority)
        self.assertEqual(list(self.mountpoint.iterdir()), [])

    def test_authority_change_before_first_mutation_refuses(self):
        authority = self.authority()
        authority.guard.side_effect = LAB.LabError("status changed")
        with mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaisesRegex(LAB.LabError, "status changed"):
            self.perform(authority=authority)
        mkdir.assert_not_called()

    def test_verify_rejects_corrupt_nonce_without_writes(self):
        self.perform()
        path = self.mountpoint / self.manifest["testRoot"] / ("fixture-" + self.nonce + ".bin")
        path.write_bytes(b"corrupt")
        with mock.patch.object(LAB.os, "pwrite") as write, mock.patch.object(LAB.os, "mkdir") as mkdir, self.assertRaises(LAB.LabError):
            self.perform("ro")
        write.assert_not_called()
        mkdir.assert_not_called()

    def test_verify_rejects_changed_card_manifest_and_expected_absence(self):
        self.perform()
        card = self.mountpoint / LAB.CARD_MANIFEST
        original = card.read_bytes()
        card.write_bytes(b"modified manifest")
        with self.assertRaises(LAB.LabError):
            self.perform("ro")
        card.write_bytes(original)
        deleted = self.mountpoint / self.manifest["testRoot"] / LAB.FIXTURES.NAMESPACE / "deleted.bin"
        deleted.write_bytes(b"unexpected")
        with self.assertRaises(LAB.LabError):
            self.perform("ro")
        self.assertEqual(deleted.read_bytes(), b"unexpected")

    def test_repeated_exercise_cannot_overwrite_existing_test(self):
        self.perform()
        original = (self.mountpoint / LAB.CARD_MANIFEST).read_bytes()
        with self.assertRaises(LAB.LabError):
            self.perform()
        self.assertEqual((self.mountpoint / LAB.CARD_MANIFEST).read_bytes(), original)

    def test_supervisor_rejects_incomplete_success_evidence(self):
        with self.assertRaises(LAB.LabError):
            LAB.validate_worker_result({"status": "passed"}, "rw", self.record, self.manifest)
        with self.assertRaises(LAB.LabError):
            LAB.validate_worker_result({"status": "failed"}, "rw", self.record, self.manifest)
        LAB.validate_worker_result({"status": "failed", "error": "expected"}, "rw", self.record, self.manifest)

    def test_local_output_refuses_test_filesystem(self):
        with mock.patch.object(LAB.REFERENCE, "descriptor_mount", return_value=self.candidate), self.assertRaises(LAB.LabError):
            LAB.LocalDirectory(self.work)

    def test_local_manifest_rejects_symlink_oversize_and_permissive_mode(self):
        host = {**self.candidate, "filesystemType": "apfs", "mountPoint": "/", "source": "/dev/mock-host"}
        with mock.patch.object(LAB.REFERENCE, "descriptor_mount", return_value=host):
            directory = LAB.LocalDirectory(self.work)
            try:
                path = self.work / "manifest.json"
                target = self.work / "other"
                target.write_bytes(b"{}")
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    directory.read(path.name)
                path.unlink()
                path.write_bytes(b"x" * (LAB.MAX_JSON + 1))
                path.chmod(0o600)
                with self.assertRaises(LAB.LabError):
                    directory.read(path.name)
                path.write_bytes(b"{}")
                path.chmod(0o644)
                with self.assertRaises(LAB.LabError):
                    directory.read(path.name)
            finally:
                os.close(directory.fd)

    def test_local_writes_are_exclusive(self):
        host = {**self.candidate, "filesystemType": "apfs", "mountPoint": "/", "source": "/dev/mock-host"}
        with mock.patch.object(LAB.REFERENCE, "descriptor_mount", return_value=host):
            directory = LAB.LocalDirectory(self.work)
            try:
                directory.write("result.json", b"{}")
                with self.assertRaises(FileExistsError):
                    directory.write("result.json", b"overwrite")
                self.assertEqual((self.work / "result.json").read_bytes(), b"{}")
            finally:
                os.close(directory.fd)

    def test_worker_timeout_escalates_and_records_stuck(self):
        previous = LAB.WORKER_STILL_RUNNING
        self.addCleanup(setattr, LAB, "WORKER_STILL_RUNNING", previous)
        process = mock.Mock(pid=12345, exitcode=None)
        process.is_alive.side_effect = [True, True, True, True]
        context = mock.Mock()
        context.Process.return_value = process
        with mock.patch.object(LAB.multiprocessing, "get_context", return_value=context), \
                mock.patch.object(LAB.tempfile, "mkdtemp", return_value=str(self.work)), self.assertRaisesRegex(LAB.LabError, "60 seconds"):
            LAB.run_worker("rw", self.status_path, self.manifest)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.join.call_args_list, [mock.call(timeout=60), mock.call(timeout=2), mock.call(timeout=2)])
        self.assertTrue(LAB.WORKER_STILL_RUNNING)

    def test_spawned_worker_rejects_private_nonstatus_path_without_mount_access(self):
        result_path = self.work / "spawned-result.json"
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=LAB.worker_entry,
            args=(result_path, "rw", self.work / "not-a-helper-status.json", self.manifest))
        try:
            process.start()
            process.join(timeout=15)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
            if process.pid is not None and not process.is_alive():
                process.close()
        result = json.loads(result_path.read_bytes())
        self.assertEqual(result["status"], "failed")
        self.assertIn("fixed helper State directory", result["error"])
        self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
        self.assertEqual(list(self.mountpoint.iterdir()), [])

    def test_cli_requires_fixed_mode_status_report_and_verify_manifest(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for arguments in ([], ["--exercise", "--verify"],
                              ["--verify", "--status", str(self.status_path), "--report", str(self.work / "r.json")],
                              ["--exercise", "--status", str(self.status_path), "--report", str(self.work / "r.json"), "--manifest", "x"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    LAB.parse_arguments(arguments)


class ProcessIdentityChecks(unittest.TestCase):
    def fake_proc_library(self, error):
        def denied(*unused):
            ctypes.set_errno(error)
            return 0
        return SimpleNamespace(proc_pidinfo=mock.Mock(side_effect=denied), proc_pidpath=mock.Mock())

    def diagnostic(self, changes=None, error=0):
        instance = LAB.ProcessDiagnostic.__new__(LAB.ProcessDiagnostic)
        instance.guard = mock.Mock()
        values = {"version": 1, "byte_size": 4144, "pid": 1234, "ppid": 1, "uid": 0,
            "ruid": 0, "status": 2, "reserved": 0, "start_seconds": 100,
            "start_microseconds": 123456, "path": b"/protected/bin/NativolHelper"}
        values.update(changes or {})
        def query(pid, pointer, size):
            self.assertEqual(pid, 1234)
            self.assertEqual(size, 4144)
            record = ctypes.cast(pointer, ctypes.POINTER(LAB.ProcessRecord)).contents
            for key, value in values.items():
                setattr(record, key, value)
            return error
        instance.library = SimpleNamespace(nativol_process_identity=query)
        return instance

    def test_explicit_helper_eperm_is_the_only_fallback_trigger(self):
        expected = {"pid": 1234, "uid": 0}
        diagnostic = SimpleNamespace(query=mock.Mock(return_value=expected))
        with mock.patch.object(LAB.ctypes, "CDLL", return_value=self.fake_proc_library(errno.EPERM)), \
                mock.patch.object(LAB, "PROCESS_DIAGNOSTIC", None), \
                mock.patch.object(LAB, "ProcessDiagnostic", return_value=diagnostic) as factory:
            self.assertEqual(LAB.process_identity(1234, privileged=True), expected)
            factory.assert_called_once_with()
            diagnostic.query.assert_called_once_with(1234)
        for privileged, error in ((False, errno.EPERM), (True, errno.ESRCH),
                                  (True, errno.EACCES), (True, 0)):
            with self.subTest(privileged=privileged, error=error), \
                    mock.patch.object(LAB.ctypes, "CDLL", return_value=self.fake_proc_library(error)), \
                    mock.patch.object(LAB, "ProcessDiagnostic") as factory, self.assertRaises(LAB.LabError):
                LAB.process_identity(1234, privileged=privileged)
            factory.assert_not_called()

    def test_public_query_returns_full_microsecond_identity(self):
        query = self.diagnostic()
        self.assertEqual(query.query(1234), {"pid": 1234, "uid": 0, "ruid": 0, "ppid": 1,
            "path": "/protected/bin/NativolHelper", "start": [100, 123456]})
        self.assertEqual(query.guard.call_count, 2)

    def test_public_query_errors_and_incomplete_or_nonroot_metadata_fail_closed(self):
        for error in (errno.ESRCH, errno.EPERM, errno.EAGAIN):
            with self.subTest(error=error), self.assertRaises(LAB.LabError):
                self.diagnostic(error=error).query(1234)
        for changed in ({"version": 0}, {"byte_size": 0}, {"pid": 999}, {"status": 5},
                        {"status": 0}, {"reserved": 1}, {"uid": 501}, {"ruid": 501},
                        {"ppid": -1}, {"start_seconds": 0}, {"start_microseconds": 1000000},
                        {"path": b"relative/helper"}):
            with self.subTest(changed=changed), self.assertRaises(LAB.LabError):
                self.diagnostic(changed).query(1234)

    def test_changed_library_refuses_before_query(self):
        diagnostic = self.diagnostic()
        diagnostic.guard.side_effect = LAB.LabError("changed library")
        diagnostic.library.nativol_process_identity = mock.Mock()
        with self.assertRaises(LAB.LabError):
            diagnostic.query(1234)
        diagnostic.library.nativol_process_identity.assert_not_called()

    def test_pinned_native_query_matches_own_process_and_rejects_invalid_pid_or_abi(self):
        diagnostic = LAB.ProcessDiagnostic()
        own = LAB.process_identity(os.getpid())
        record = LAB.ProcessRecord()
        self.assertEqual(diagnostic.library.nativol_process_identity(
            os.getpid(), ctypes.byref(record), ctypes.sizeof(record)), 0)
        self.assertEqual((record.pid, record.uid, record.ruid, record.ppid,
            record.path.decode(), [record.start_seconds, record.start_microseconds]),
            (own["pid"], own["uid"], own["ruid"], own["ppid"], own["path"], own["start"]))
        for pid in (-1, 0, 1, 2147483647):
            record.pid = 99
            self.assertNotEqual(diagnostic.library.nativol_process_identity(
                pid, ctypes.byref(record), ctypes.sizeof(record)), 0)
            self.assertEqual(bytes(record), bytes(ctypes.sizeof(record)))
        self.assertEqual(diagnostic.library.nativol_process_identity(
            os.getpid(), ctypes.byref(record), ctypes.sizeof(record) - 1), errno.EINVAL)

    def test_unreviewed_diagnostic_hash_rejected_before_library_loading(self):
        with mock.patch.object(LAB, "PROCESS_DIAGNOSTIC_SHA256", "0" * 64), \
                mock.patch.object(LAB.ctypes, "CDLL") as load, self.assertRaises(LAB.LabError):
            LAB.ProcessDiagnostic()
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
