#!/usr/bin/env python3
"""Bounded fixture operations for the private kernel NTFS image lab.

This module has no command-line target interface and never mounts anything. Its
caller must bind candidate.source to its newly created, verified regular image.
Every operation additionally proves the mounted descriptor and nonce. The
fixtures exercise normal fsync and clean-remount persistence, not power loss.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


_SPEC = importlib.util.spec_from_file_location(
    "nativol_write_fixture_reference", Path(__file__).with_name("fskit-reference-lab.py"))
REFERENCE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = REFERENCE
_SPEC.loader.exec_module(REFERENCE)
REFERENCE.BACKEND = "kernel"
REFERENCE.ALLOWED_TYPES = {"macfuse"}
LabError = REFERENCE.LabError
NAMESPACE = "nativol-write-fixtures"
LARGE_BYTES = 8 * 1024 * 1024
MAX_EXPECTED = 128 * 1024
MAX_REPORT = 32 * 1024


def _name(value):
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or "/" in value or "\x00" in value or len(value.encode("utf-8")) > 200):
        raise LabError("Only fixed, bounded basenames are accepted")
    return value


def _inputs(mountpoint, candidate, fixture_name, expected):
    match = re.fullmatch(r"fixture-([0-9a-f]{64})\.bin", fixture_name)
    if (not match or not isinstance(expected, bytes) or not 32 <= len(expected) <= MAX_EXPECTED
            or expected[:32] != bytes.fromhex(match.group(1))):
        raise LabError("Invalid bounded nonce fixture")
    if (not isinstance(candidate, dict) or not isinstance(candidate.get("source"), str)
            or not Path(candidate["source"]).is_absolute()
            or Path(candidate["source"]).name != "disposable.ntfs"
            or ".." in Path(candidate["source"]).parts
            or candidate["source"].startswith("/dev/")):
        raise LabError("Candidate source must be the caller's fresh regular image")
    REFERENCE.validate_identity(candidate, mountpoint)


def _data(expected):
    seed = hashlib.sha256(expected).digest()
    block = b"".join(hashlib.sha256(seed + index.to_bytes(4, "big")).digest()
                     for index in range(128))
    large = (block * ((LARGE_BYTES + len(block) - 1) // len(block)))[:LARGE_BYTES]
    random_access = bytearray(block * 2)
    random_access[3:3 + 31] = seed[:31]
    cross = block[:73]
    random_access[4093:4093 + len(cross)] = cross
    tail = b"append:" + seed
    return {
        **{"tiny-%d.bin" % size: block[:size] for size in (0, 1, 2, 3, 7, 31)},
        "maandishi-\u00e9-\u6d4b\u8bd5.txt": ("Nativol: Habari, caf\u00e9, \u6d4b\u8bd5, \U0001f4be\n" + seed.hex() + "\n").encode(),
        "large.bin": large,
        "random-access.bin": bytes(random_access) + tail,
        "shortened.bin": b"short:" + seed[:7],
        "rewritten.bin": b"rewrite after zero:" + seed,
        "renamed.bin": b"renamed:" + seed,
        "atomic.bin": b"replacement:" + seed,
        "renamed-directory/nested.bin": b"nested:" + seed,
    }


def _identity(info):
    return (info.st_dev, info.st_ino)


class _Anchor:
    def __init__(self, fd, candidate, root=None):
        self.fd, self.candidate = fd, candidate
        self.root = self if root is None else root
        self.files = {}
        self.directory_identity = _identity(os.fstat(fd))
        self.guard()

    def guard(self):
        if self is not self.root:
            self.root.guard()
        info = os.fstat(self.fd)
        if (not stat.S_ISDIR(info.st_mode) or _identity(info) != self.directory_identity
                or REFERENCE.descriptor_mount(self.fd) != self.candidate):
            raise LabError("Anchored directory or filesystem identity changed")

    def regular(self, fd, identity=None):
        self.guard()
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or REFERENCE.descriptor_mount(fd) != self.candidate
                or (identity is not None and _identity(info) != identity)):
            raise LabError("Opened file is not the expected regular file in this filesystem")
        return info

    def open_existing(self, name, writable=False):
        _name(name)
        self.guard()
        fd = os.open(name, (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW,
                     dir_fd=self.fd)
        try:
            info = self.regular(fd, self.files.get(name))
            self.files.setdefault(name, _identity(info))
            return fd
        except BaseException:
            os.close(fd)
            raise

    def create(self, name, data):
        _name(name)
        self.guard()
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                     dir_fd=self.fd)
        try:
            info = self.regular(fd)
            self.files[name] = _identity(info)
            self.write(fd, data, 0, self.files[name])
            self.sync(fd, self.files[name])
        finally:
            os.close(fd)
        self.read(name, data)

    def write(self, fd, data, offset, identity):
        if not isinstance(data, bytes) or len(data) > LARGE_BYTES or offset < 0:
            raise LabError("Unbounded write payload")
        position = 0
        while position < len(data):
            self.regular(fd, identity)
            count = os.pwrite(fd, data[position:position + 1024 * 1024], offset + position)
            if not isinstance(count, int) or not 0 < count <= min(1024 * 1024, len(data) - position):
                raise LabError("Invalid or zero-length write")
            position += count

    def truncate(self, fd, length, identity):
        self.regular(fd, identity)
        if not 0 <= length <= LARGE_BYTES:
            raise LabError("Unbounded truncate length")
        os.ftruncate(fd, length)

    def sync(self, fd, identity):
        self.regular(fd, identity)
        os.fsync(fd)

    def read(self, name, expected):
        fd = self.open_existing(name)
        try:
            info = self.regular(fd, self.files[name])
            if info.st_size != len(expected):
                raise LabError("Fixture size mismatch: " + name)
            restored = bytearray()
            while len(restored) < len(expected):
                self.regular(fd, self.files[name])
                chunk = os.pread(fd, min(1024 * 1024, len(expected) - len(restored)), len(restored))
                if not chunk:
                    raise LabError("Unexpected short read: " + name)
                restored.extend(chunk)
            self.regular(fd, self.files[name])
            if os.pread(fd, 1, len(expected)) or bytes(restored) != expected:
                raise LabError("Fixture content mismatch: " + name)
            return {"size": len(restored), "sha256": hashlib.sha256(restored).hexdigest()}
        finally:
            os.close(fd)

    def absent(self, name):
        _name(name)
        self.guard()
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise LabError("Removed or renamed fixture still exists: " + name)

    def directory(self, name, create=False):
        _name(name)
        self.guard()
        if create:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            return _Anchor(fd, self.candidate, self.root)
        except BaseException:
            os.close(fd)
            raise

    def _bound_name(self, name, fd, directory=False):
        self.guard()
        info = os.stat(_name(name), dir_fd=self.fd, follow_symlinks=False)
        opened = os.fstat(fd)
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not correct_type or _identity(info) != _identity(opened)
                or REFERENCE.descriptor_mount(fd) != self.candidate):
            raise LabError("Directory entry changed before mutation")

    def rename_file(self, old, new, replace=False):
        fd = self.open_existing(old)
        destination = None
        try:
            self._bound_name(old, fd)
            if replace:
                destination = self.open_existing(new)
                self._bound_name(new, destination)
            else:
                self.absent(new)
            self.guard()
            operation = os.replace if replace else os.rename
            operation(old, new, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            self.files[new] = self.files.pop(old)
        finally:
            os.close(fd)
            if destination is not None:
                os.close(destination)
        self.absent(old)

    def unlink(self, name):
        fd = self.open_existing(name)
        try:
            self._bound_name(name, fd)
            self.guard()
            os.unlink(name, dir_fd=self.fd)
            self.files.pop(name, None)
        finally:
            os.close(fd)
        self.absent(name)

    def rename_directory(self, old, new):
        child = self.directory(old)
        try:
            self._bound_name(old, child.fd, directory=True)
            self.absent(new)
            self.guard()
            os.rename(old, new, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            os.close(child.fd)
        self.absent(old)

    def rmdir(self, name):
        child = self.directory(name)
        try:
            self._bound_name(name, child.fd, directory=True)
            self.guard()
            os.rmdir(name, dir_fd=self.fd)
        finally:
            os.close(child.fd)
        self.absent(name)


def _root(mountpoint, candidate, fixture_name, expected, read_only):
    _inputs(mountpoint, candidate, fixture_name, expected)
    if bool(candidate["flags"] & REFERENCE.MNT_RDONLY) != read_only:
        raise LabError("Candidate has the wrong effective read-only flag for this phase")
    fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root = _Anchor(fd, candidate)
        root.read(fixture_name, expected)
        root.guard()
        return root
    except BaseException:
        os.close(fd)
        raise


def _manifest(area, expected):
    records = []
    for name, content in _data(expected).items():
        if "/" in name:
            directory, basename = name.split("/")
            child = area.directory(directory)
            try:
                record = child.read(basename, content)
            finally:
                os.close(child.fd)
        else:
            record = area.read(name, content)
        records.append({"path": NAMESPACE + "/" + name, **record})
    absent = ["before-rename.bin", "before-directory", "replacement.tmp", "deleted.bin", "deleted-directory"]
    for name in absent:
        area.absent(name)
    return {"files": records, "directories": [NAMESPACE, NAMESPACE + "/renamed-directory"],
            "absent": [NAMESPACE + "/" + name for name in absent]}


def _result(root, area, fixture_name, expected, phase):
    manifest = _manifest(area, expected)
    root.read(fixture_name, expected)
    root.guard()
    result = {"status": "passed", "phase": phase, "verifiedMount": root.candidate,
              "effectiveReadOnly": bool(root.candidate["flags"] & REFERENCE.MNT_RDONLY),
              "fixtureSHA256": hashlib.sha256(expected).hexdigest(), "manifest": manifest,
              "checks": ["All fixture sizes, exact bytes and SHA-256 checksums match",
                         "Removed and renamed original paths are absent", "Original nonce fixture is unchanged"]}
    if len(json.dumps(result).encode()) > MAX_REPORT:
        raise LabError("Fixture report exceeded its bounded size")
    return result


def exercise(mountpoint, candidate, fixture_name, expected):
    """Create and verify fixed workloads only after independent RW nonce proof."""
    root = _root(mountpoint, candidate, fixture_name, expected, read_only=False)
    area = None
    try:
        area = root.directory(NAMESPACE, create=True)
        content = _data(expected)
        for name in list(content)[:8]:
            area.create(name, content[name])
        # Exclusive creation must fail without replacing the existing file.
        area.guard()
        try:
            collision = os.open("tiny-1.bin", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                                0o600, dir_fd=area.fd)
        except FileExistsError:
            pass
        else:
            os.close(collision)
            raise LabError("Exclusive creation unexpectedly replaced an existing fixture")
        area.read("tiny-1.bin", content["tiny-1.bin"])
        seed = hashlib.sha256(expected).digest()
        block = content["large.bin"][:4096]
        area.create("random-access.bin", block * 2)
        fd = area.open_existing("random-access.bin", writable=True)
        try:
            identity = area.files["random-access.bin"]
            area.write(fd, seed[:31], 3, identity)
            area.write(fd, block[:73], 4093, identity)
            area.write(fd, b"append:" + seed, 8192, identity)
            area.sync(fd, identity)
        finally:
            os.close(fd)
        area.read("random-access.bin", content["random-access.bin"])
        for name, zero in (("shortened.bin", False), ("rewritten.bin", True)):
            area.create(name, block)
            fd = area.open_existing(name, writable=True)
            try:
                identity = area.files[name]
                if zero:
                    area.truncate(fd, 0, identity)
                    area.sync(fd, identity)
                    if os.fstat(fd).st_size != 0:
                        raise LabError("Truncation to zero did not take effect")
                area.write(fd, content[name], 0, identity)
                area.truncate(fd, len(content[name]), identity)
                area.sync(fd, identity)
            finally:
                os.close(fd)
            area.read(name, content[name])
        area.create("before-rename.bin", content["renamed.bin"])
        area.rename_file("before-rename.bin", "renamed.bin")
        area.create("atomic.bin", b"old atomic contents:" + seed)
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
        result = _result(root, area, fixture_name, expected, "write-and-readback")
        result["checks"].extend(["Exclusive create collision preserved existing bytes",
                                 "Written files were fsynced, closed and reopened",
                                 "Tiny, Unicode, 8 MiB, partial overwrite, append, truncate, rename, replace and delete workloads passed"])
        if len(json.dumps(result).encode()) > MAX_REPORT:
            raise LabError("Fixture report exceeded its bounded size")
        return result
    finally:
        if area is not None:
            os.close(area.fd)
        os.close(root.fd)


def verify(mountpoint, candidate, fixture_name, expected):
    """Read all expected fixtures after a separate read-only remount."""
    root = _root(mountpoint, candidate, fixture_name, expected, read_only=True)
    area = None
    try:
        area = root.directory(NAMESPACE)
        return _result(root, area, fixture_name, expected, "read-only-remount")
    finally:
        if area is not None:
            os.close(area.fd)
        os.close(root.fd)


if __name__ == "__main__":
    raise SystemExit("Fixture module has no command-line targets; use the fresh-image lab")
