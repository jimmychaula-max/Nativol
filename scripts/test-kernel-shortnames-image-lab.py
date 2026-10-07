#!/usr/bin/env python3
"""Fresh synthetic metadata and mocked gates only; no engine or mount execution."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "nativol_shortnames_checks", Path(__file__).with_name("kernel-shortnames-image-lab.py"))
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


def record(flags=0, at_sector_tail=False):
    data = bytearray(1024)
    data[:4] = b"FILE"
    struct.pack_into("<HH", data, 4, 48, 3)
    struct.pack_into("<HH", data, 20, 56, 1)
    length = 472 if at_sector_tail else 40
    struct.pack_into("<II", data, 24, 56 + length + 4, 1024)
    struct.pack_into("<II", data, 56, 0x70, length)
    value_offset = 444 if at_sector_tail else 24
    struct.pack_into("<IH", data, 56 + 16, 12, value_offset)
    value = 56 + value_offset
    data[value + 8:value + 10] = b"\x03\x01"
    struct.pack_into("<H", data, value + 10, flags)
    struct.pack_into("<I", data, 56 + length, 0xFFFFFFFF)
    data[48:50] = b"\x12\x34"
    for index in (1, 2):
        tail = index * 512 - 2
        data[48 + 2 * index:50 + 2 * index] = data[tail:tail + 2]
        data[tail:tail + 2] = b"\x12\x34"
    return bytes(data)


class ShortnamesChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nativol-shortnames-unit-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()

    def image(self):
        path = self.work / "synthetic.ntfs"
        data = bytearray(128 * 1024)
        data[3:11] = b"NTFS    "
        struct.pack_into("<H", data, 11, 512)
        data[13] = 8
        struct.pack_into("<QQ", data, 48, 4, 16)
        struct.pack_into("b", data, 64, -10)
        data[510:512] = b"\x55\xaa"
        for start in (4 * 4096 + 3 * 1024, 16 * 4096 + 3 * 1024):
            data[start:start + 1024] = record()
        path.write_bytes(data)
        return path

    def test_record_flag_round_trip_changes_only_one_raw_byte(self):
        for tail in (False, True):
            with self.subTest(flag_at_sector_tail=tail):
                before = record(at_sector_tail=tail)
                decoded = LAB.decode_record(before, 512)
                after = LAB.flagged_record(decoded)
                self.assertEqual(LAB.decode_record(after, 512)["flags"], 0x80)
                self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)
                self.assertEqual(after[510:512], b"\x12\x34")
                self.assertEqual(after[1022:1024], b"\x12\x34")

    def test_nonzero_initial_flags_refuse_seeding(self):
        for flags in (1, 0x80, 0x81, 0x100):
            with self.subTest(flags=flags), self.assertRaises(LAB.LabError):
                LAB.flagged_record(LAB.decode_record(record(flags), 512))

    def test_invalid_update_sequence_and_attribute_bounds_refused(self):
        for offset, payload in ((510, b"XX"), (60, bytes(4)), (24, struct.pack("<I", 2000)),
                                (76, struct.pack("<H", 1000))):
            raw = bytearray(record())
            raw[offset:offset + len(payload)] = payload
            with self.subTest(offset=offset), self.assertRaises(LAB.LabError):
                LAB.decode_record(bytes(raw), 512)

    def test_both_records_are_read_from_bounded_regular_fixture(self):
        path = self.image()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            entries = LAB.volume_records(descriptor, path.stat().st_size)
        finally:
            os.close(descriptor)
        self.assertEqual([item["label"] for item in entries], ["mft", "mirror"])
        self.assertEqual([item["flags"] for item in entries], [0, 0])
        self.assertEqual([item["offset"] for item in entries], [19456, 68608])

    def test_boot_geometry_overlap_and_out_of_image_offsets_refused(self):
        path = self.image()
        original = path.read_bytes()
        for offset, data in ((13, b"\x03"), (56, struct.pack("<Q", 4)),
                             (56, struct.pack("<Q", 10 ** 8)), (64, b"\x00")):
            content = bytearray(original)
            content[offset:offset + len(data)] = data
            path.write_bytes(content)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with self.subTest(offset=offset, value=data), self.assertRaises(LAB.LabError):
                    LAB.volume_records(descriptor, len(content))
            finally:
                os.close(descriptor)

    def test_short_metadata_read_refused(self):
        path = self.work / "short"
        path.write_bytes(b"short")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            with self.assertRaises(LAB.LabError):
                LAB.read_exact(descriptor, 512, 0)
        finally:
            os.close(descriptor)

    def test_flag_evidence_requires_both_mirrors_and_all_three_phases(self):
        report = {"informationalFlagSeeded": True, "shortnamesHealthImageUnchanged": True,
            "volumeFlagChecks": [{"phase": phase, "mftFlags": 128, "mirrorFlags": 128,
                "mirrorsAgree": True, "dirtyBitAbsent": True}
                for phase in ("before-mount", "after-rw-unmount", "after-ro-unmount")]}
        self.assertTrue(LAB.flag_evidence(report))
        report["volumeFlagChecks"][-1]["mirrorFlags"] = 0
        self.assertFalse(LAB.flag_evidence(report))
        report["volumeFlagChecks"][-1]["mirrorFlags"] = 128
        report["volumeFlagChecks"][-1]["dirtyBitAbsent"] = False
        self.assertFalse(LAB.flag_evidence(report))
        report["volumeFlagChecks"].pop()
        self.assertFalse(LAB.flag_evidence(report))

    def test_default_prepare_does_not_construct_image_or_write(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["shortnames-lab"]), \
                mock.patch.object(LAB.WRITE, "preflight", return_value=({}, {"scriptSHA256": {}})) as preflight, \
                mock.patch.object(LAB, "ShortnamesLab") as factory, \
                mock.patch.object(LAB.os, "pwrite") as write, contextlib.redirect_stdout(output):
            result = LAB.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "prepared")
        preflight.assert_called_once_with(False)
        factory.assert_not_called()
        write.assert_not_called()

    def test_arbitrary_target_is_rejected_before_preflight(self):
        with mock.patch.object(sys, "argv", ["shortnames-lab", "/dev/disk2s1"]), \
                mock.patch.object(LAB.WRITE, "preflight") as preflight, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(LAB.main(), 2)
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
