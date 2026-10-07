#!/usr/bin/env python3
"""Real inspector tests, using only newly created private regular image files."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
INSPECTOR = ROOT / ".local-engine/health/x86_64/nativol-ntfs-health"
TOOLS = ROOT / ".local-engine/bin/x86_64"
PINS = {"mkntfs": "9af42ef92fad65a533dc8a5cbfc802941d7d17baffd71c0bf13c84ad327c388f",
        "ntfscp": "0522188e9cdb137ea63ea074aaa164b90122b4a13546a826719ab92039123b66"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(os.getuid(), 0)
        for name, expected in PINS.items(): self.assertEqual(digest(TOOLS / name), expected)
        self.tmp = tempfile.TemporaryDirectory(prefix="nativol-health-test-")
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "fresh.ntfs"
        with self.image.open("xb") as stream: stream.truncate(256 * 1024 * 1024)
        os.chmod(self.image, 0o600)
        self.tool("mkntfs", "-F", "-Q", self.image)

    def tool(self, name, *args):
        subprocess.run([str(TOOLS / name), *map(str, args)], check=True, capture_output=True, timeout=60)

    def inspect(self):
        before = digest(self.image)
        # The child fixes fd 3 after exec in an isolated Python trampoline;
        # descriptor 3 is the only accepted interface, never a supplied path.
        fd = os.open(self.image, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            trampoline = "import os,sys; fd=int(sys.argv[1]); os.dup2(fd,3,inheritable=True); os.set_inheritable(3,True); os.execv(sys.argv[2],[sys.argv[2],'/dev/fd/3'])"
            result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-c", trampoline, str(fd), str(INSPECTOR)],
                                    pass_fds=(fd,), capture_output=True, timeout=30)
        finally:
            os.close(fd)
        self.assertEqual(before, digest(self.image), "Inspector changed fresh image")
        self.assertLess(len(result.stdout), 2048)
        record = json.loads(result.stdout)
        self.assertTrue(record["readOnly"])
        self.assertEqual(record["deniedOperations"], 0)
        return result.returncode, record

    def hiber(self, payload):
        path = Path(self.tmp.name) / "hiber-fixture"
        path.write_bytes(payload)
        self.tool("ntfscp", self.image, path, "/hiberfil.sys")

    def test_clean_ntfs_and_serial(self):
        code, record = self.inspect()
        self.assertEqual(code, 0, record)
        self.assertTrue(record["readyForWrite"])
        self.assertEqual(record["hibernation"], "absent")
        self.assertEqual((record["ntfsMajor"], record["ntfsMinor"]), (3, 1))
        self.assertEqual(len(record["volumeSerial"]), 16)

    def test_zero_hiber_header_accepted(self):
        self.hiber(bytes(4096))
        code, record = self.inspect()
        self.assertEqual(code, 0, record)
        self.assertEqual(record["hibernation"], "zero-header")

    def test_clean_ntfs_with_4096_byte_sectors(self):
        # Reformat only the private regular image created by setUp. Exercise
        # the second sector size allowed by the general external-drive policy.
        self.tool("mkntfs", "-F", "-Q", "-s", "4096", self.image)
        with self.image.open("rb") as stream:
            self.assertEqual(struct.unpack_from("<H", stream.read(512), 11)[0], 4096)
        code, record = self.inspect()
        self.assertEqual(code, 0, record)
        self.assertTrue(record["readyForWrite"])
        self.assertEqual(record["hibernation"], "absent")

    def test_unknown_nonzero_hiber_refused(self):
        self.hiber(b"unknown" + bytes(4096 - 7))
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])
        self.assertEqual(record["reason"], "hibernated-or-unknown-hiberfile")

    def test_short_hiber_refused(self):
        self.hiber(b"hibr")
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])

    def test_uppercase_hiber_refused(self):
        path = Path(self.tmp.name) / "hiber-fixture"
        path.write_bytes(b"HIBR" + bytes(4092))
        self.tool("ntfscp", self.image, path, "/HIBERFIL.SYS")
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertEqual(record["reason"], "hibernated-or-unknown-hiberfile")

    def test_invalid_ntfs_refused_without_writes(self):
        with self.image.open("r+b") as stream:
            stream.seek(3); stream.write(b"INVALID!")
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])

    def set_volume_flags(self, flags):
        # Change only our new image's resident $Volume flags in both the MFT
        # and mirror. No filesystem tool or external target is accepted.
        with self.image.open("r+b") as stream:
            boot = stream.read(512)
            cluster = struct.unpack_from("<H", boot, 11)[0] * boot[13]
            encoded = struct.unpack_from("b", boot, 64)[0]
            record_size = (1 << -encoded) if encoded < 0 else encoded * cluster
            for field in (48, 56):
                start = struct.unpack_from("<Q", boot, field)[0] * cluster + 3 * record_size
                stream.seek(start); record = bytearray(stream.read(record_size))
                self.assertEqual(record[:4], b"FILE")
                offset = struct.unpack_from("<H", record, 20)[0]
                while struct.unpack_from("<I", record, offset)[0] != 0x70:
                    length = struct.unpack_from("<I", record, offset + 4)[0]
                    self.assertGreater(length, 0); offset += length
                    self.assertLess(offset, record_size - 24)
                self.assertEqual(record[offset + 8], 0)
                value = offset + struct.unpack_from("<H", record, offset + 20)[0]
                struct.pack_into("<H", record, value + 10, flags)
                stream.seek(start); stream.write(record)

    def test_dirty_volume_refused(self):
        self.set_volume_flags(1)
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertEqual(record["reason"], "dirty-or-unsupported-volume-flags")

    def test_disabled_short_names_preserved_and_health_checks_run(self):
        self.set_volume_flags(0x0080)
        code, record = self.inspect()
        self.assertEqual(code, 0, record)
        self.assertTrue(record["readyForWrite"])
        self.assertEqual(record["volumeFlags"], 0x0080)
        self.assertTrue(record["ntfsReadOnlyVerified"])
        self.assertEqual(record["hibernation"], "absent")
        self.assertTrue(record["logfileClean"])

    def test_dirty_with_disabled_short_names_refused(self):
        self.set_volume_flags(0x0081)
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])
        self.assertEqual(record["volumeFlags"], 0x0081)
        self.assertEqual(record["reason"], "dirty-or-unsupported-volume-flags")

    def test_unknown_volume_flag_with_disabled_short_names_refused(self):
        self.set_volume_flags(0x0180)
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])
        self.assertEqual(record["volumeFlags"], 0x0180)
        self.assertEqual(record["reason"], "dirty-or-unsupported-volume-flags")

    def test_disabled_short_names_does_not_bypass_hibernation(self):
        self.hiber(b"hibr" + bytes(4092))
        self.set_volume_flags(0x0080)
        code, record = self.inspect()
        self.assertNotEqual(code, 0)
        self.assertFalse(record["readyForWrite"])
        self.assertEqual(record["volumeFlags"], 0x0080)
        self.assertEqual(record["reason"], "hibernated-or-unknown-hiberfile")
        self.assertEqual(record["hibernation"], "nonzero-header")

    def test_path_cli_refused(self):
        result = subprocess.run([str(INSPECTOR), str(self.image)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["readyForWrite"])


if __name__ == "__main__":
    unittest.main()
