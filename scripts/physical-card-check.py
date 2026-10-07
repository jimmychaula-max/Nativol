#!/usr/bin/env python3
"""Personal-card fixture checks through a verified, already-mounted helper session.

This tool never mounts, unmounts, formats, repairs or opens a raw device. Only the
root helper's fixed personal target profile is accepted. The image laboratory's
guards are not changed: this module reuses its descriptor operations and data,
with a separate physical-session authority proof.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import uuid


SPEC = importlib.util.spec_from_file_location(
    "nativol_physical_fixture_operations", Path(__file__).with_name("kernel-write-fixtures.py"))
FIXTURES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FIXTURES
SPEC.loader.exec_module(FIXTURES)
REFERENCE, LabError = FIXTURES.REFERENCE, FIXTURES.LabError
STATE_ROOT = Path("/Library/Application Support/Nativol/State")
VERSION_ROOT = Path("/Library/Application Support/Nativol/Versions")
PROFILE = "private-profile-unconfigured-v1"
CARD_MANIFEST = "Nativol-Windows-Check.json"
MAX_STATUS, MAX_JSON = 16384, 65536
WORKER_STILL_RUNNING = False
PROCESS_DIAGNOSTIC_PATH = (Path(__file__).resolve().parent.parent
    / ".local-engine/diagnostics/libnativol-process-identity.dylib")
PROCESS_DIAGNOSTIC_SHA256 = "5036234d56a50ce3f700a322493fc29557c877c4f50016a4fe755798558a35f5"
PROCESS_DIAGNOSTIC = None


def encoded(value):
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_JSON:
        raise LabError("Diagnostic JSON exceeds the bounded size")
    return data


def canonical_uuid(value):
    if not isinstance(value, str):
        raise LabError("Operation identifier is not a UUID")
    try:
        normalized = str(uuid.UUID(value))
    except ValueError as error:
        raise LabError("Operation identifier is not a UUID") from error
    if value.lower() != normalized:
        raise LabError("Operation UUID must use its canonical hyphenated form")
    return normalized


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def protected_path(path, regular=True):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise LabError("Protected paths must be absolute without traversal")
    parts = list(reversed(path.parents)) + [path]
    for index, item in enumerate(parts):
        info = item.lstat()
        last = index == len(parts) - 1
        correct_type = stat.S_ISREG(info.st_mode) if last and regular else stat.S_ISDIR(info.st_mode)
        if (not correct_type or info.st_uid != 0 or info.st_mode & 0o022
                or (last and regular and info.st_nlink != 1)):
            raise LabError("Root-protected path contains a link, writable component or unexpected owner")
    return info


def read_protected(path, maximum):
    before = protected_path(path)
    if not 0 < before.st_size <= maximum:
        raise LabError("Protected file exceeds its size limit")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if file_identity(opened) != file_identity(before):
            raise LabError("Protected file changed during opening")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if (len(data) != before.st_size or file_identity(after) != file_identity(before)
                or file_identity(protected_path(path)) != file_identity(before)):
            raise LabError("Protected file changed during reading")
        return bytes(data), file_identity(before)
    finally:
        os.close(fd)


def read_status(path, mode):
    path = Path(path)
    if path.parent != STATE_ROOT or path.suffix != ".json":
        raise LabError("Status must be an operation JSON file in the fixed helper State directory")
    operation = canonical_uuid(path.stem)
    data, identity = read_protected(path, MAX_STATUS)
    record = json.loads(data)
    if not isinstance(record, dict):
        raise LabError("Helper status must be a JSON object")
    if (type(record.get("schemaVersion")) is not int or record.get("schemaVersion") != 1 or record.get("state") != "mounted"
            or record.get("mode") != mode or canonical_uuid(record.get("operationId")) != operation
            or record.get("ownerUID") != os.getuid() or record.get("targetProfile") != PROFILE
            or record.get("source") != "/dev/fd/3" or record.get("filesystem") != "macfuse"):
        raise LabError("Helper status does not authorize this user's requested personal-card phase")
    mountpoint = record.get("mountPath")
    if (not isinstance(mountpoint, str) or Path(mountpoint).parent != Path("/Volumes")
            or not Path(mountpoint).name.startswith("Nativol-")
            or canonical_uuid(Path(mountpoint).name[len("Nativol-"):]) != operation):
        raise LabError("Helper mountpoint differs from its operation UUID")
    fsid = record.get("fsid")
    if (not isinstance(fsid, list) or len(fsid) != 2 or fsid == [0, 0]
            or any(type(value) is not int or not -(2 ** 31) <= value < 2 ** 31 for value in fsid)):
        raise LabError("Helper status lacks a nonzero kernel filesystem ID")
    for key in ("partitionRegistryID", "mediaRegistryID"):
        if not isinstance(record.get(key), str) or not re.fullmatch(r"[1-9][0-9]{0,19}", record[key]):
            raise LabError("Helper status lacks registry identity: " + key)
    if not isinstance(record.get("volumeSerial"), str) or not re.fullmatch(r"[0-9a-fA-F]{16}", record["volumeSerial"]):
        raise LabError("Helper status lacks the full 64-bit NTFS volume serial")
    for key in ("driverPID", "helperPID"):
        if type(record.get(key)) is not int or record[key] <= 1:
            raise LabError("Helper status lacks a process identifier: " + key)
    if record["driverPID"] == record["helperPID"]:
        raise LabError("Driver and helper processes must be distinct")
    driver = record.get("driverExecutable")
    if (not isinstance(driver, str)
            or not re.fullmatch(re.escape(str(VERSION_ROOT)) + r"/[0-9a-f]{64}/bin/ntfs-3g", driver)
            or not isinstance(record.get("driverSHA256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["driverSHA256"])):
        raise LabError("Driver identity is not a protected versioned Nativol executable")
    return record, identity


class ProcBSDInfo(ctypes.Structure):
    # Public Darwin SDK sys/proc_info.h, PROC_PIDTBSDINFO (3).
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid",
                 "svuid", "svgid", "reserved")] + [
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid")] + [
        ("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]


class ProcessRecord(ctypes.Structure):
    # Our diagnostic's fixed-width ABI; Darwin's kinfo_proc is parsed only by C
    # compiled against the installed SDK, never by this Python layout.
    _fields_ = [("version", ctypes.c_uint32), ("byte_size", ctypes.c_uint32),
        ("pid", ctypes.c_int32), ("ppid", ctypes.c_int32), ("uid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32), ("status", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
        ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64),
        ("path", ctypes.c_char * 4096)]


class ProcessDiagnostic:
    def __init__(self):
        path = PROCESS_DIAGNOSTIC_PATH
        if path.resolve(strict=True) != path:
            raise LabError("Process diagnostic path contains a symbolic link")
        for item in list(reversed(path.parents)):
            info = item.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or info.st_mode & 0o022):
                raise LabError("Process diagnostic has an untrusted parent directory")
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 1024 * 1024):
            raise LabError("Process diagnostic must be a bounded owned mode-0600 regular library")
        self.identity = file_identity(info)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if file_identity(os.fstat(descriptor)) != self.identity:
                raise LabError("Process diagnostic changed during opening")
            data = bytearray()
            while len(data) < info.st_size:
                chunk = os.read(descriptor, info.st_size - len(data))
                if not chunk:
                    raise LabError("Short process diagnostic read")
                data.extend(chunk)
            if file_identity(os.fstat(descriptor)) != self.identity:
                raise LabError("Process diagnostic changed during reading")
        finally:
            os.close(descriptor)
        if hashlib.sha256(data).hexdigest() != PROCESS_DIAGNOSTIC_SHA256:
            raise LabError("Process diagnostic hash differs from the reviewed build; rebuild and review its pin")
        self.guard()
        self.library = ctypes.CDLL(str(path), use_errno=True)
        self.library.nativol_process_identity.argtypes = [ctypes.c_int32, ctypes.POINTER(ProcessRecord), ctypes.c_size_t]
        self.library.nativol_process_identity.restype = ctypes.c_int
        self.guard()

    def guard(self):
        if file_identity(PROCESS_DIAGNOSTIC_PATH.lstat()) != self.identity:
            raise LabError("Process diagnostic library changed")

    def query(self, pid):
        self.guard()
        info = ProcessRecord()
        error = self.library.nativol_process_identity(pid, ctypes.byref(info), ctypes.sizeof(info))
        self.guard()
        if error:
            raise LabError("Public helper process metadata query failed: " + os.strerror(error))
        if (ctypes.sizeof(info) != 4144 or info.version != 1 or info.byte_size != ctypes.sizeof(info)
                or info.pid != pid or info.status <= 0 or info.status == 5 or info.reserved != 0
                or info.uid != 0 or info.ruid != 0 or info.ppid < 0
                or info.start_seconds <= 0 or info.start_microseconds >= 1000000
                or not info.path.startswith(b"/")):
            raise LabError("Public helper metadata lacks a live root process identity")
        return {"pid": pid, "uid": info.uid, "ruid": info.ruid, "ppid": info.ppid,
            "path": info.path.decode("utf-8"), "start": [info.start_seconds, info.start_microseconds]}


def process_identity(pid, privileged=False):
    global PROCESS_DIAGNOSTIC
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    info = ProcBSDInfo()
    ctypes.set_errno(0)
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        # Only the protected status's root helper may use this fallback, and
        # only for macOS's explicit EPERM denial. Driver and all other errors
        # keep the original fail-closed behavior.
        if privileged and ctypes.get_errno() == errno.EPERM:
            if PROCESS_DIAGNOSTIC is None:
                PROCESS_DIAGNOSTIC = ProcessDiagnostic()
            return PROCESS_DIAGNOSTIC.query(pid)
        raise LabError("Cannot establish live helper/driver process identity")
    path = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, path, len(path)) <= 0 or info.pid != pid or info.status == 5:
        raise LabError("Helper/driver is missing, inaccessible or a zombie")
    return {"pid": pid, "uid": info.uid, "ruid": info.ruid, "ppid": info.ppid,
            "path": path.value.decode("utf-8"), "start": [info.start_seconds, info.start_microseconds]}


class Authority:
    def __init__(self, status_path, mode):
        self.path, self.mode = Path(status_path), mode
        self.record, self.status_identity = read_status(self.path, mode)
        driver_data, self.driver_identity = read_protected(self.record["driverExecutable"], 16 * 1024 * 1024)
        if hashlib.sha256(driver_data).hexdigest() != self.record["driverSHA256"]:
            raise LabError("Protected driver hash differs from helper status")
        driver = process_identity(self.record["driverPID"])
        helper = process_identity(self.record["helperPID"], privileged=True)
        if (driver["uid"] != os.getuid() or driver["ruid"] != os.getuid()
                or driver["path"] != self.record["driverExecutable"]
                or driver["ppid"] != self.record["helperPID"]
                or helper["uid"] != 0 or helper["ruid"] != 0
                or helper["path"] != str(Path(self.record["driverExecutable"]).with_name("NativolHelper"))):
            raise LabError("Helper/driver process path or user identity differs from the root authority")
        for process in (driver, helper):
            start_ns = process["start"][0] * 1000000000 + process["start"][1] * 1000
            if self.status_identity[3] < start_ns:
                raise LabError("Helper status predates the currently running process")
        self.helper_identity = file_identity(protected_path(helper["path"]))
        self.processes = (driver, helper)
        self.guard()

    def guard(self):
        if file_identity(protected_path(self.path)) != self.status_identity:
            raise LabError("Root helper status changed during card verification")
        if file_identity(protected_path(self.record["driverExecutable"])) != self.driver_identity:
            raise LabError("Protected driver executable changed")
        for index, expected in enumerate(self.processes):
            if process_identity(expected["pid"], privileged=index == 1) != expected:
                raise LabError("Helper or driver process identity changed")
        if file_identity(protected_path(self.processes[1]["path"])) != self.helper_identity:
            raise LabError("Protected helper executable changed")


def validate_mount(entry, authority):
    record = authority.record
    REFERENCE.validate_identity(entry, record["mountPath"])
    if (entry["source"] != "/dev/fd/3" or entry["filesystemID"] != record["fsid"]
            or bool(entry["flags"] & REFERENCE.MNT_RDONLY) != (authority.mode == "ro")):
        raise LabError("Mounted descriptor differs from the helper's physical-card session")


class PhysicalAnchor(FIXTURES._Anchor):
    def __init__(self, descriptor, candidate, authority):
        self.authority = authority
        super().__init__(descriptor, candidate)

    def guard(self):
        self.authority.guard()
        super().guard()
        validate_mount(self.candidate, self.authority)


def target_identity(status):
    return {"targetProfile": PROFILE, "partitionRegistryID": status["partitionRegistryID"],
            "mediaRegistryID": status["mediaRegistryID"], "volumeSerial": status["volumeSerial"].lower()}


def nonce_data(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise LabError("Invalid physical-test nonce")
    return bytes.fromhex(nonce) + b"Nativol personal physical-card verification\n"


def make_manifest(nonce, status):
    expected = nonce_data(nonce)
    files = [{"path": "fixture-" + nonce + ".bin", "size": len(expected),
              "sha256": hashlib.sha256(expected).hexdigest()}]
    for path, data in FIXTURES._data(expected).items():
        files.append({"path": FIXTURES.NAMESPACE + "/" + path, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    result = {"schemaVersion": 1, "targetKind": "physical-ntfs-validation",
              "testRoot": "Nativol-Check-" + nonce, "files": files,
              "absent": [FIXTURES.NAMESPACE + "/" + name for name in
                         ("before-rename.bin", "before-directory", "replacement.tmp", "deleted.bin", "deleted-directory")],
              "macTarget": target_identity(status)}
    if "windowsVolumeSerial" in status:
        serial = status["windowsVolumeSerial"]
        if not isinstance(serial, str) or not re.fullmatch(r"[0-9a-fA-F]{8}", serial):
            raise LabError("Optional Windows volume serial is invalid")
        result["expectedVolumeSerial"] = serial.upper()
    return result


def validate_manifest(manifest, status):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("testRoot"), str):
        raise LabError("Local manifest is not a physical-card test record")
    root = manifest["testRoot"]
    if not root.startswith("Nativol-Check-"):
        raise LabError("Local manifest lacks a unique physical test root")
    nonce = root[len("Nativol-Check-"):]
    if manifest != make_manifest(nonce, status):
        raise LabError("Local manifest bytes, paths or target identity differ from the deterministic test")
    return nonce


def exercise_files(area, expected):
    """The same fixed 14-file workload, under a newly created physical namespace."""
    content = FIXTURES._data(expected)
    for name in list(content)[:8]:
        area.create(name, content[name])
    area.guard()
    try:
        collision = os.open("tiny-1.bin", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                            0o600, dir_fd=area.fd)
    except FileExistsError:
        pass
    else:
        os.close(collision)
        raise LabError("Exclusive creation unexpectedly replaced an existing file")
    area.read("tiny-1.bin", content["tiny-1.bin"])
    seed, block = hashlib.sha256(expected).digest(), content["large.bin"][:4096]
    area.create("random-access.bin", block * 2)
    fd = area.open_existing("random-access.bin", writable=True)
    try:
        identity = area.files["random-access.bin"]
        for offset, data in ((3, seed[:31]), (4093, block[:73]), (8192, b"append:" + seed)):
            area.write(fd, data, offset, identity)
        area.sync(fd, identity)
    finally:
        os.close(fd)
    for name, zero in (("shortened.bin", False), ("rewritten.bin", True)):
        area.create(name, block)
        fd = area.open_existing(name, writable=True)
        try:
            identity = area.files[name]
            if zero:
                area.truncate(fd, 0, identity)
                area.sync(fd, identity)
                if os.fstat(fd).st_size != 0:
                    raise LabError("Truncation to zero failed")
            area.write(fd, content[name], 0, identity)
            area.truncate(fd, len(content[name]), identity)
            area.sync(fd, identity)
        finally:
            os.close(fd)
    area.create("before-rename.bin", content["renamed.bin"])
    area.rename_file("before-rename.bin", "renamed.bin")
    area.create("atomic.bin", b"old atomic:" + seed)
    area.create("replacement.tmp", content["atomic.bin"])
    area.rename_file("replacement.tmp", "atomic.bin", replace=True)
    child = area.directory("before-directory", create=True)
    try:
        child.create("nested.bin", content["renamed-directory/nested.bin"])
    finally:
        os.close(child.fd)
    area.rename_directory("before-directory", "renamed-directory")
    area.create("deleted.bin", b"delete:" + seed)
    area.unlink("deleted.bin")
    child = area.directory("deleted-directory", create=True)
    try:
        child.create("temporary.bin", b"temporary:" + seed)
        child.unlink("temporary.bin")
    finally:
        os.close(child.fd)
    area.rmdir("deleted-directory")


def perform(mode, status_path, manifest):
    authority = Authority(status_path, mode)
    nonce = validate_manifest(manifest, authority.record)
    expected = nonce_data(nonce)
    fixture_name = "fixture-" + nonce + ".bin"
    root = check = area = None
    fd = os.open(authority.record["mountPath"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        candidate = REFERENCE.descriptor_mount(fd)
        validate_mount(candidate, authority)
        root = PhysicalAnchor(fd, candidate, authority)
        if mode == "rw":
            # Refuse an old kit before creating any new card files.
            root.absent(CARD_MANIFEST)
        check = root.directory(manifest["testRoot"], create=mode == "rw")
        if mode == "rw":
            check.create(fixture_name, expected)
        check.read(fixture_name, expected)
        area = check.directory(FIXTURES.NAMESPACE, create=mode == "rw")
        if mode == "rw":
            exercise_files(area, expected)
        result = FIXTURES._result(check, area, fixture_name, expected, "physical-" + mode)
        expected_files = manifest["files"][1:]
        if result["manifest"]["files"] != expected_files or result["manifest"]["absent"] != manifest["absent"]:
            raise LabError("Mounted data differs from the local deterministic manifest")
        if mode == "rw":
            root.create(CARD_MANIFEST, encoded(manifest))
        root.read(CARD_MANIFEST, encoded(manifest))
        authority.guard()
        return {"status": "passed", "mode": mode, "operationId": authority.record["operationId"],
                "target": target_identity(authority.record), "verifiedMount": candidate,
                "testRoot": manifest["testRoot"], "windowsManifestSHA256": hashlib.sha256(encoded(manifest)).hexdigest(),
                "filesVerified": len(manifest["files"]), "absencesVerified": len(manifest["absent"]),
                "checks": result["checks"], "certifiesFilesystemSafety": False,
                "mountedWritesRequested": mode == "rw",
                "helperProcessMetadata": {"fallbackUsed": PROCESS_DIAGNOSTIC is not None,
                    "fallbackAPI": "sysctl(KERN_PROC_PID) + proc_pidpath",
                    "diagnosticSHA256": PROCESS_DIAGNOSTIC_SHA256 if PROCESS_DIAGNOSTIC is not None else None}}
    finally:
        for anchor in (area, check):
            if anchor is not None:
                os.close(anchor.fd)
        os.close(fd)


class LocalDirectory:
    """Exclusive host-side output, with no writes to a mounted test filesystem."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.resolve(strict=True) != self.path:
            raise LabError("Local output directory must be absolute without symlink components")
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise LabError("Local output directory must be owned and not group/world writable")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.candidate = REFERENCE.descriptor_mount(self.fd)
            if self.candidate["filesystemType"] not in {"apfs", "hfs"}:
                raise LabError("Reports and local manifests must use the Mac's APFS/HFS filesystem")
            self.identity = (info.st_dev, info.st_ino)
            self.guard()
        except BaseException:
            os.close(self.fd)
            raise

    def guard(self):
        info = os.fstat(self.fd)
        if ((info.st_dev, info.st_ino) != self.identity or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o022
                or REFERENCE.descriptor_mount(self.fd) != self.candidate):
            raise LabError("Local report directory identity changed")

    def absent(self, name):
        FIXTURES._name(name)
        self.guard()
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise LabError("Local output already exists; choose a new report name")

    def write(self, name, data):
        FIXTURES._name(name)
        self.guard()
        if len(data) > MAX_JSON:
            raise LabError("Local diagnostic file exceeds size limit")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or REFERENCE.descriptor_mount(fd) != self.candidate):
                raise LabError("Local output descriptor is not an owned regular file")
            offset = 0
            while offset < len(data):
                self.guard()
                count = os.write(fd, data[offset:])
                if count <= 0:
                    raise LabError("Local output write made no progress")
                offset += count
            os.fsync(fd)
        finally:
            os.close(fd)

    def read(self, name):
        FIXTURES._name(name)
        self.guard()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= MAX_JSON
                    or REFERENCE.descriptor_mount(fd) != self.candidate):
                raise LabError("Local manifest/result must be a bounded owned mode-0600 regular file")
            data = bytearray()
            while len(data) <= MAX_JSON:
                chunk = os.read(fd, min(65536, MAX_JSON + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != info.st_size or file_identity(os.fstat(fd)) != file_identity(info):
                raise LabError("Local manifest/result changed during reading")
            return json.loads(data)
        finally:
            os.close(fd)


def worker_entry(result_path, mode, status_path, manifest):
    try:
        result = perform(mode, status_path, manifest)
    except (Exception, KeyboardInterrupt) as error:
        result = {"status": "failed", "error": type(error).__name__ + ": " + str(error)[:500]}
    directory = LocalDirectory(Path(result_path).parent)
    try:
        directory.write(Path(result_path).name, encoded(result))
    finally:
        os.close(directory.fd)


def run_worker(mode, status_path, manifest):
    global WORKER_STILL_RUNNING
    work = Path(tempfile.mkdtemp(prefix="nativol-card-check-")).resolve()
    os.chmod(work, 0o700)
    result_path = work / "worker-result.json"
    context = multiprocessing.get_context("spawn")
    worker = context.Process(target=worker_entry, args=(result_path, mode, status_path, manifest))
    try:
        worker.start()
        worker.join(timeout=60)
        if worker.is_alive():
            raise LabError("Physical-card worker exceeded 60 seconds; mounted state retained")
        if worker.exitcode:
            raise LabError("Physical-card worker exited unsuccessfully; mounted state retained")
        directory = LocalDirectory(work)
        try:
            result = directory.read(result_path.name)
        finally:
            os.close(directory.fd)
        if not isinstance(result, dict):
            raise LabError("Physical-card worker returned an invalid result")
        return result
    finally:
        if worker.pid is not None and worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
            WORKER_STILL_RUNNING = worker.is_alive()


def validate_worker_result(result, mode, status, manifest):
    if not isinstance(result, dict) or result.get("status") not in {"passed", "failed"}:
        raise LabError("Physical worker returned no explicit diagnostic status")
    if result["status"] == "failed":
        if not isinstance(result.get("error"), str):
            raise LabError("Failed worker returned no diagnostic explanation")
        return
    if (result.get("mode") != mode or canonical_uuid(result.get("operationId")) != canonical_uuid(status["operationId"])
            or result.get("target") != target_identity(status) or result.get("testRoot") != manifest["testRoot"]
            or result.get("filesVerified") != len(manifest["files"])
            or result.get("absencesVerified") != len(manifest["absent"])
            or result.get("windowsManifestSHA256") != hashlib.sha256(encoded(manifest)).hexdigest()):
        raise LabError("Physical worker success lacks matching card and fixture evidence")
    validate_mount(result.get("verifiedMount", {}), SimpleNamespace(record=status, mode=mode))


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--exercise", action="store_true")
    modes.add_argument("--verify", action="store_true")
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    result = parser.parse_args(argv)
    if bool(result.manifest) != result.verify:
        parser.error("--manifest is required only with --verify")
    return result


def validate_host():
    if (sys.platform != "darwin" or platform.machine() != "x86_64"
            or platform.mac_ver()[0].split(".")[0] != "15"
            or os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid()
            or not sys.flags.isolated or not sys.flags.no_site):
        raise LabError("Run with /usr/bin/python3 -I -S as the ordinary user on the reviewed Intel macOS 15 host")


def main(argv=None):
    global WORKER_STILL_RUNNING
    WORKER_STILL_RUNNING = False
    options = parse_arguments(argv)
    mode = "rw" if options.exercise else "ro"
    report = {"schemaVersion": 1, "kind": "personal-physical-card-check", "status": "failed",
              "mode": mode, "startedAt": datetime.now(timezone.utc).isoformat(),
              "mountOperationsPerformed": False, "certifiesFilesystemSafety": False}
    output = None
    try:
        validate_host()
        if (not options.report.is_absolute() or options.report.suffix != ".json"
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,145}\.json", options.report.name)):
            raise LabError("Report must have a new bounded .json basename in an existing absolute local directory")
        output = LocalDirectory(options.report.parent)
        output.absent(options.report.name)
        status, unused_identity = read_status(options.status, mode)
        if options.exercise:
            manifest_path = options.report.with_name(options.report.stem + ".manifest.json")
            output.absent(manifest_path.name)
            manifest = make_manifest(os.urandom(32).hex(), status)
            output.write(manifest_path.name, encoded(manifest))
        else:
            manifest_path = options.manifest
            local = LocalDirectory(manifest_path.parent)
            try:
                manifest = local.read(manifest_path.name)
            finally:
                os.close(local.fd)
            validate_manifest(manifest, status)
        report.update({"localManifestPath": str(manifest_path), "testRoot": manifest["testRoot"],
                       "target": target_identity(status), "operationId": status["operationId"],
                       "windowsManifestSHA256": hashlib.sha256(encoded(manifest)).hexdigest()})
        result = run_worker(mode, options.status, manifest)
        validate_worker_result(result, mode, status, manifest)
        report.update(result)
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        report["workerStillRunning"] = WORKER_STILL_RUNNING
        if WORKER_STILL_RUNNING:
            report["status"] = "failed"
        report["completedAt"] = datetime.now(timezone.utc).isoformat()
        if output is not None:
            try:
                output.write(options.report.name, encoded(report))
            except (OSError, LabError) as error:
                report.update({"status": "failed", "reportSaveError": str(error)[:300]})
            finally:
                os.close(output.fd)
        print(encoded(report).decode("utf-8"), end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    REFERENCE.finish_supervisor(main(), WORKER_STILL_RUNNING)
