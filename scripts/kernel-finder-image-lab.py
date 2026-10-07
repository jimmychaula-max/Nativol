#!/usr/bin/env python3
"""Default prepare-only; --execute tests Finder metadata on one fresh image.

No physical target, inherited fd source, existing image or custom mount option.
Baseline without streams, native openxattr RW, then new-process native RO.
"""
import ctypes
import errno
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "dist/Nativol.app/Contents/Resources/Backend"
PINS = {"bin/ntfs-3g": "132bc332f75512dab0993393190c42f435b4498fbb816c372cfec99caaa084b5",
        "lib/libfuse.2.dylib": "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42"}
spec = importlib.util.spec_from_file_location("nativol_finder_write", Path(__file__).with_name("kernel-write-image-lab.py"))
WRITE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = WRITE
spec.loader.exec_module(WRITE)
BASE, REF, LabError = WRITE.BASE, WRITE.REFERENCE, WRITE.LabError
ENV = WRITE.ENV
ENV["DYLD_LIBRARY_PATH"] = str(PAYLOAD / "lib")
OLD_VERIFY = BASE.verify_tool
STUCK = False


def verify_payload():
    for relative, digest in PINS.items():
        path = PAYLOAD / relative
        info = path.lstat()
        if (path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_nlink != 1 or REF.digest(path) != digest):
            raise LabError("Packaged runtime differs from reviewed pins")
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(PAYLOAD / "bin/ntfs-3g")],
                            capture_output=True, timeout=10)
    if result.returncode: raise LabError("Packaged driver signature failed")


def verify_tool(name):
    if name != "ntfs-3g": return OLD_VERIFY(name)
    verify_payload()
    return PAYLOAD / "bin/ntfs-3g"


BASE.verify_tool = verify_tool
BASE.TOOL_RECORDS["ntfs-3g"] = ("driver/x86_64/ntfs-3g", PINS["bin/ntfs-3g"])


def xattr(fd, name, value=None):
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    if value is None:
        function = libc.fgetxattr
        function.restype = ctypes.c_ssize_t
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_uint32, ctypes.c_int]
        buffer = ctypes.create_string_buffer(4096)
        result = function(fd, name.encode(), buffer, len(buffer), 0, 0)
        if result >= 0: return buffer.raw[:result]
    else:
        function = libc.fsetxattr
        function.restype = ctypes.c_int
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_uint32, ctypes.c_int]
        buffer = ctypes.create_string_buffer(value)
        result = function(fd, name.encode(), buffer, len(value), 0, 0)
        if result == 0: return None
    number = ctypes.get_errno()
    raise OSError(number, os.strerror(number))


def fixture_values(expected):
    return (expected[:32] + bytes(range(256)) * 16,
            {"com.apple.FinderInfo": b"TEXTttxt" + bytes(24),
             "com.apple.ResourceFork": b"Nativol resource fork\n" + expected[:32] * 8,
             "com.nativol.finder-check": b"Nativol native xattr\n" + expected[:32]})


def fixture(kind, variant, mountpoint, candidate, fixture_name, expected, helper, helper_hash):
    result = REF.inspect_fixture(mountpoint, fixture_name, expected, candidate)
    if kind == "ownership": return result
    writing = kind == "exercise"
    if kind not in {"exercise", "verify"} or result["effectiveReadOnly"] is writing:
        raise LabError("Wrong fixed fixture action/access")
    if variant not in {"baseline", "native"}: raise LabError("Unknown fixed variant")
    directory = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    namespace = "finder-baseline" if variant == "baseline" else "finder-native"
    try:
        if REF.descriptor_mount(directory) != candidate: raise LabError("Mount changed before metadata fixture")
        if writing: os.mkdir(namespace, 0o700, dir_fd=directory)
        parent = os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            if REF.descriptor_mount(parent) != candidate: raise LabError("Fixture directory escaped mount")
            payload, attributes = fixture_values(expected)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if writing else os.O_RDONLY
            source = os.open("source.bin", flags | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                if REF.descriptor_mount(source) != candidate or not stat.S_ISREG(os.fstat(source).st_mode):
                    raise LabError("Source is not a regular fixture in the proved mount")
                if writing:
                    if os.write(source, payload) != len(payload): raise LabError("Short payload write")
                    if variant == "baseline":
                        errors = {}
                        for action in ("get", "set"):
                            try: xattr(source, "com.apple.FinderInfo", None if action == "get" else attributes["com.apple.FinderInfo"])
                            except OSError as error: errors[action] = error.errno
                        result["baselineXattrErrno"] = errors
                        if errors != {"get": errno.EOPNOTSUPP, "set": errno.EOPNOTSUPP}:
                            raise LabError("Baseline did not reproduce unsupported metadata")
                    else:
                        try: xattr(source, "com.nativol.absent")
                        except OSError as error:
                            if error.errno != 93: raise
                            result["missingAttributeErrno"] = error.errno
                        else: raise LabError("Missing native attribute unexpectedly exists")
                        for name, value in attributes.items(): xattr(source, name, value)
                    os.fsync(source)
            finally: os.close(source)
            if writing:
                if REF.digest(helper) != helper_hash: raise LabError("Native copy helper changed")
                # The child inherits this held directory, not a re-resolved source path.
                os.fchdir(parent)
                copied = subprocess.run([str(helper)], stdin=subprocess.DEVNULL, capture_output=True,
                                        env=ENV, timeout=20)
                if copied.returncode or len(copied.stdout) > 4096: raise LabError("Native copy helper failed")
                result["nativeCopy"] = json.loads(copied.stdout)
                if variant == "native" and not all(result["nativeCopy"].get(key) is True for key in
                        ("foundationCopyPassed", "copyfilePassed", "urlResourcePassed", "apfsToNTFSPassed",
                         "ntfsToAPFSPassed", "apfsCopyfilePassed")):
                    raise LabError("Native copy operation failed: " + json.dumps(result["nativeCopy"]))
            manifest = {}
            names = ("source.bin",) if variant == "baseline" else ("source.bin", "foundation-copy.bin", "copyfile-copy.bin",
                                                                  "apfs-copy.bin", "apfs-copyfile.bin")
            for name in names:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or REF.descriptor_mount(fd) != candidate or info.st_size != len(payload)
                            or os.read(fd, len(payload) + 1) != payload):
                        raise LabError("Copied data is not exact in the proved mount")
                    attrs = {}
                    if variant == "native":
                        if not writing and name == "source.bin":
                            try: xattr(fd, "com.nativol.finder-check", b"forbidden read-only mutation")
                            except OSError as error:
                                if error.errno != errno.EROFS: raise
                                result["readOnlyMutationErrno"] = error.errno
                            else: raise LabError("Read-only mount accepted a metadata mutation")
                        for attr, expected_value in attributes.items():
                            actual = xattr(fd, attr)
                            if actual != expected_value: raise LabError("Metadata did not roundtrip: " + attr)
                            attrs[attr] = hashlib.sha256(actual).hexdigest()
                    manifest[name] = {"size": info.st_size, "sha256": hashlib.sha256(payload).hexdigest(), "xattrs": attrs}
                finally: os.close(fd)
            if variant == "native":
                host = Path(helper).parent
                host_info = host.lstat()
                if (host.resolve() != host or host_info.st_uid != os.getuid()
                        or stat.S_IMODE(host_info.st_mode) != 0o700):
                    raise LabError("Private host fixture directory changed")
                hostdir = os.open(host, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    host_mount = REF.descriptor_mount(hostdir)
                    if host_mount["filesystemType"] != "apfs": raise LabError("Host fixture is not APFS")
                    fd = os.open("finder-native-roundtrip.bin", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=hostdir)
                    try:
                        info = os.fstat(fd)
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                                or REF.descriptor_mount(fd) != host_mount or info.st_size != len(payload)
                                or os.read(fd, len(payload) + 1) != payload):
                            raise LabError("NTFS to APFS roundtrip data differed")
                        for name, value in attributes.items():
                            if xattr(fd, name) != value: raise LabError("NTFS to APFS roundtrip metadata differed")
                        result["apfsRoundtripVerified"] = True
                    finally: os.close(fd)
                finally: os.close(hostdir)
            sidecars = [name for name in os.listdir(parent) if name.startswith("._")]
            result["appleDoubleSidecars"] = sidecars
            if variant == "native" and sidecars: raise LabError("Native mode unexpectedly created AppleDouble sidecars")
            if REF.descriptor_mount(parent) != candidate: raise LabError("Fixture mount changed during operations")
        finally: os.close(parent)
    finally: os.close(directory)
    result.update({"status": "passed", "manifest": manifest})
    return result


def worker_entry(kind, path, variant, mountpoint, candidate, fixture_name, expected, helper, helper_hash):
    try:
        result = fixture(kind, variant, mountpoint, candidate, fixture_name, expected, helper, helper_hash)
        encoded = json.dumps(result).encode()
        if len(encoded) > WRITE.MAX_RESULT: raise LabError("Oversized result")
    except Exception as error:
        encoded = json.dumps({"error": type(error).__name__ + ": " + str(error)[:500]}).encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream: stream.write(encoded)


class FinderStage(WRITE.ImageStage):
    def __init__(self, owner, phase):
        super().__init__(owner, phase)
        self.variant = owner.variant
        self.mountpoint = Path("/Volumes") / ("Nativol-Finder-" + uuid.uuid4().hex)
        self.log_path = self.work / (self.variant + "-" + phase + ".log")
        self.report.update({"mountPoint": str(self.mountpoint), "variant": self.variant})

    def mount(self):
        self.owner.assert_detached(); self.verify_image()
        self.report["kernelReadinessBeforeMount"] = BASE.inspect_readiness(True)
        if verify_tool("ntfs-3g") != self.tools["ntfs-3g"]: raise LabError("Driver changed")
        if os.path.lexists(self.mountpoint) or self.entry(): raise LabError("Occupied mountpoint")
        options = ["backend=kernel", "local", "no_def_opts", "norecover", self.phase,
                   "no_detach", "quiet", "uid=" + str(os.getuid()), "gid=" + str(os.getgid()),
                   "umask=077", "default_permissions", "usermapping=/dev/null", "windows_names",
                   "nosuid", "nodev", "volname=" + self.mountpoint.name]
        if self.variant == "native": options.append("streams_interface=openxattr")
        elif self.variant != "baseline": raise LabError("Unknown fixed mount variant")
        self.server_log = self.log_path.open("xb")
        self.server = subprocess.Popen([str(self.tools["ntfs-3g"]), str(self.image), str(self.mountpoint),
            "-o", ",".join(options)], cwd=self.work, env=ENV, stdin=subprocess.DEVNULL,
            stdout=self.server_log, stderr=subprocess.STDOUT)
        self.report.update({"requestedOptions": options, "mountAttemptPerformed": True,
                           "serverPID": self.server.pid, "cleanupRequired": True})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            candidate = self.entry()
            if candidate:
                REF.validate_identity(candidate, self.mountpoint)
                if candidate["source"] != str(self.image) or self.server.poll() is not None:
                    raise LabError("Source/server identity differs from fresh image")
                self.report["observedMount"] = candidate
                return candidate
            if self.server.poll() is not None: raise LabError("Driver exited without mount")
            time.sleep(0.1)
        raise LabError("Mount deadline exceeded")

    def run_worker(self, kind, candidate):
        path = self.work / (self.variant + "-" + self.phase + "-" + kind + ".json")
        worker = multiprocessing.get_context("spawn").Process(target=worker_entry,
            args=(kind, path, self.variant, self.mountpoint, candidate, self.fixture_name, self.expected,
                  self.owner.copy_helper, self.owner.copy_helper_hash))
        evidence = {"operation": kind, "resultPath": str(path)}
        self.report["workers"].append(evidence)
        try:
            worker.start(); evidence["pid"] = worker.pid
            worker.join(timeout=40)
            if worker.is_alive(): raise LabError("Metadata worker deadline exceeded")
            if worker.exitcode: raise LabError("Metadata worker exited unsuccessfully")
            return WRITE.read_result(path)
        finally:
            if worker.pid is not None and worker.is_alive():
                worker.terminate(); worker.join(timeout=2)
                if worker.is_alive(): worker.kill(); worker.join(timeout=2)
                self.report["inspectionWorkerStillRunning"] = worker.is_alive()


class FinderLab(WRITE.WriteImageLab):
    def __init__(self, tools, report):
        super().__init__(tools, report)
        # This runner confines all durable artifacts to the ignored workspace.
        self.work.rmdir()
        base = ROOT / ".local-engine/finder-images"
        base.mkdir(mode=0o700, exist_ok=True)
        if base.resolve() != base: raise LabError("Image workspace is a symlink")
        self.work = Path(tempfile.mkdtemp(prefix="finder-", dir=base)).resolve()
        self.image = self.work / "disposable.ntfs"
        self.variant = "baseline"
        self.copy_helper = self.work / "finder-copy-fixture"
        report.update({"workDirectory": str(self.work), "imagePath": str(self.image)})

    def prepare(self):
        source = Path(__file__).with_name("finder-copy-fixture.m")
        compiled = subprocess.run(["/usr/bin/clang", "-O2", "-Wall", "-Wextra", "-Werror",
            "-fobjc-arc", "-framework", "Foundation", str(source), "-o", str(self.copy_helper)],
            env=ENV, capture_output=True, timeout=30)
        if compiled.returncode: raise LabError("Fixture compilation failed: " + compiled.stderr.decode()[-2000:])
        self.copy_helper_hash = REF.digest(self.copy_helper)
        self.report["copyHelperSHA256"] = self.copy_helper_hash
        super().prepare()
        payload, attributes = fixture_values(self.expected)
        fd = os.open(self.work / "host-source.bin", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if REF.descriptor_mount(fd)["filesystemType"] != "apfs": raise LabError("Host source is not APFS")
            if os.write(fd, payload) != len(payload): raise LabError("Short APFS fixture write")
            for name, value in attributes.items(): xattr(fd, name, value)
            os.fsync(fd)
        finally: os.close(fd)

    def run_stage(self, phase):
        previous = WRITE.ImageStage
        WRITE.ImageStage = FinderStage
        try: return super().run_stage(phase)
        finally: WRITE.ImageStage = previous

    def execute(self):
        self.prepare()
        self.variant = "baseline"
        self.run_stage("rw")
        self.variant = "native"
        written = self.run_stage("rw")
        after_write = self.hash_image()
        read = self.run_stage("ro")
        after_read = self.hash_image()
        self.report.update({"imageSHA256AfterWrite": after_write, "imageSHA256AfterReadOnly": after_read,
            "readOnlyImageUnchanged": after_write == after_read, "metadataManifestsMatch": read == written})
        if read != written or after_write != after_read: raise LabError("Metadata persistence/image integrity failure")


def success(report):
    stages = report.get("stages", [])
    return (len(stages) == 3 and [(s.get("variant"), s.get("phase")) for s in stages] ==
            [("baseline", "rw"), ("native", "rw"), ("native", "ro")]
            and all(s.get("status") == "passed" and s.get("ownershipVerified") and s.get("workloadPassed")
                    and s.get("cleanUnmount") and s.get("serverExitCode") == 0 for s in stages)
            and report.get("readOnlyImageUnchanged") is True and report.get("metadataManifestsMatch") is True)


def main():
    global STUCK
    if sys.argv[1:] not in ([], ["--execute"]): return 2
    report = {"schemaVersion": 1, "kind": "finder-native-metadata-image", "status": "failed",
              "physicalDeviceTargetAccepted": False, "mountAttemptPerformed": False}
    lab = None
    try:
        tools, record = WRITE.preflight(bool(sys.argv[1:]))
        record["packagedPayloadSHA256"] = PINS
        record["scriptSHA256"].update({name: REF.digest(Path(__file__).with_name(name)) for name in
                                       ("kernel-finder-image-lab.py", "finder-copy-fixture.m")})
        report["build"] = record
        if not sys.argv[1:]: report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = FinderLab(tools, report)
            lab.execute()
            if not success(report): raise LabError("Incomplete metadata evidence")
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:1000]
    finally:
        if lab:
            STUCK = any(s.report.get("inspectionWorkerStillRunning") for s in lab.stages)
            report["cleanupRequired"] = bool(report.get("detachedStateUncertain") or STUCK or
                                             any(s.report.get("cleanupRequired") for s in lab.stages))
            report["mountAttemptPerformed"] = any(s.report.get("mountAttemptPerformed") for s in lab.stages)
            if report["cleanupRequired"]: report["status"] = "failed"
            try:
                with (lab.work / "finder-report.json").open("x") as stream: json.dump(report, stream, indent=2)
            except OSError as error:
                report.update({"status": "failed", "reportSaveError": str(error)[:300]})
        print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    REF.finish_supervisor(main(), STUCK)
