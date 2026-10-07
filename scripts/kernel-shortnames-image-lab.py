#!/usr/bin/env python3
"""Prepare-only unless --execute: preserve NTFS flag 0x0080 on one fresh image.

The inherited packaged-engine harness creates its own private 256 MiB regular
image and held descriptor. Only that image receives the informational flag in
$Volume and its MFT mirror, before any mount. Normal RW/RO stages must preserve
the flag with the dirty bit absent after clean unmount. No physical target is accepted.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys


SPEC = importlib.util.spec_from_file_location(
    "nativol_shortnames_packaged_lab", Path(__file__).with_name("kernel-packaged-fd-image-lab.py"))
PACKAGED = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PACKAGED
SPEC.loader.exec_module(PACKAGED)
FD, LabError = PACKAGED.FD, PACKAGED.FD.LabError
REFERENCE, WRITE = FD.REFERENCE, FD.WRITE
INFORMATIONAL_FLAG = 0x0080
STUCK = False


def read_exact(fd, count, offset):
    if not 0 < count <= 65536 or offset < 0:
        raise LabError("Unbounded image metadata read")
    chunks = bytearray()
    while len(chunks) < count:
        part = os.pread(fd, count - len(chunks), offset + len(chunks))
        if not part:
            raise LabError("Short image metadata read")
        chunks.extend(part)
    return bytes(chunks)


def decode_record(raw, sector_size):
    size = len(raw)
    if raw[:4] != b"FILE" or size % sector_size:
        raise LabError("Unexpected fresh-image $Volume record")
    usa_offset, usa_count = struct.unpack_from("<HH", raw, 4)
    attributes = struct.unpack_from("<H", raw, 20)[0]
    used, allocated = struct.unpack_from("<II", raw, 24)
    if (usa_count != size // sector_size + 1 or usa_offset < 48
            or usa_offset + usa_count * 2 > attributes or attributes % 8
            or attributes < 48 or not attributes + 4 <= used <= size or allocated != size
            or not struct.unpack_from("<H", raw, 22)[0] & 1):
        raise LabError("Invalid MFT bounds or update-sequence metadata")
    logical = bytearray(raw)
    sequence = raw[usa_offset:usa_offset + 2]
    for index in range(1, usa_count):
        tail = index * sector_size - 2
        if raw[tail:tail + 2] != sequence:
            raise LabError("MFT update-sequence check failed")
        logical[tail:tail + 2] = raw[usa_offset + index * 2:usa_offset + index * 2 + 2]
    position, volume = attributes, None
    while position + 4 <= used:
        kind = struct.unpack_from("<I", logical, position)[0]
        if kind == 0xFFFFFFFF:
            break
        if position + 24 > used:
            raise LabError("Truncated resident attribute")
        length = struct.unpack_from("<I", logical, position + 4)[0]
        if length < 24 or length % 8 or position + length > used:
            raise LabError("Invalid attribute bounds")
        if kind == 0x70:
            value_length = struct.unpack_from("<I", logical, position + 16)[0]
            value_offset = struct.unpack_from("<H", logical, position + 20)[0]
            if (volume is not None or logical[position + 8] != 0 or logical[position + 9] != 0
                    or value_length != 12 or value_offset < 24 or value_offset + 12 > length):
                raise LabError("Unexpected $VOLUME_INFORMATION attribute")
            value = position + value_offset
            if logical[value + 8:value + 10] != b"\x03\x01":
                raise LabError("Only the fresh NTFS 3.1 fixture is covered")
            volume = value + 10
        position += length
    else:
        raise LabError("MFT attributes lack an end marker")
    if volume is None:
        raise LabError("Missing resident $VOLUME_INFORMATION")
    return {"raw": raw, "logical": logical, "flagsOffset": volume,
            "flags": struct.unpack_from("<H", logical, volume)[0],
            "usaOffset": usa_offset, "usaCount": usa_count, "sectorSize": sector_size}


def volume_records(fd, image_size):
    boot = read_exact(fd, 512, 0)
    if boot[3:11] != b"NTFS    " or boot[510:512] != b"\x55\xaa":
        raise LabError("Fresh image lacks a valid NTFS boot signature")
    sector = struct.unpack_from("<H", boot, 11)[0]
    sectors_per_cluster = boot[13]
    if (sector not in {512, 1024, 2048, 4096} or sectors_per_cluster == 0
            or sectors_per_cluster > 128 or sectors_per_cluster & (sectors_per_cluster - 1)):
        raise LabError("Unexpected NTFS sector or cluster geometry")
    cluster = sector * sectors_per_cluster
    record_code = struct.unpack_from("b", boot, 64)[0]
    if record_code < -16 or record_code == 0:
        raise LabError("Invalid MFT record size encoding")
    record_size = (1 << -record_code) if record_code < 0 else record_code * cluster
    if not sector <= record_size <= 65536 or record_size % sector:
        raise LabError("Unexpected MFT record size")
    records = []
    for field, label in ((48, "mft"), (56, "mirror")):
        start = struct.unpack_from("<Q", boot, field)[0] * cluster + 3 * record_size
        if not 512 <= start <= image_size - record_size:
            raise LabError("$Volume record is outside this regular image")
        item = decode_record(read_exact(fd, record_size, start), sector)
        item.update({"offset": start, "label": label})
        records.append(item)
    if abs(records[0]["offset"] - records[1]["offset"]) < record_size:
        raise LabError("MFT and mirror locations overlap")
    return records


def flagged_record(record):
    if record["flags"] != 0:
        raise LabError("Only an initially zero-flag freshly formatted image can be seeded")
    result = bytearray(record["logical"])
    struct.pack_into("<H", result, record["flagsOffset"], INFORMATIONAL_FLAG)
    usa = record["usaOffset"]
    sequence = record["raw"][usa:usa + 2]
    for index in range(1, record["usaCount"]):
        tail = index * record["sectorSize"] - 2
        result[usa + index * 2:usa + index * 2 + 2] = result[tail:tail + 2]
        result[tail:tail + 2] = sequence
    decoded = decode_record(bytes(result), record["sectorSize"])
    if decoded["flags"] != INFORMATIONAL_FLAG:
        raise LabError("Flag encoding did not survive the MFT update-sequence round trip")
    # Changing 0x0000 to 0x0080 must change exactly one raw byte. This also
    # detects unintended record edits while preserving the update-sequence tags.
    changed = [index for index, (before, after) in enumerate(zip(record["raw"], result)) if before != after]
    if len(changed) != 1 or record["raw"][changed[0]] != 0 or result[changed[0]] != 0x80:
        raise LabError("Seeding would modify metadata beyond the informational flag")
    return bytes(result)


class ShortnamesLab(PACKAGED.PackagedLab):
    def __init__(self, tools, report):
        super().__init__(tools, report)
        report.update({"informationalFlagUnderTest": INFORMATIONAL_FLAG, "volumeFlagChecks": []})

    def mutation_guard(self):
        self.assert_detached()
        self.verify_image()
        work_info = self.work.lstat()
        held = os.fstat(self.held_fd)
        if (self.stages or not self.renamed or self.image != self.work / "held.ntfs"
                or not self.work.name.startswith("nativol-kernel-image-rw-")
                or not stat.S_ISDIR(work_info.st_mode) or work_info.st_uid != os.getuid()
                or stat.S_IMODE(work_info.st_mode) != 0o700
                or not stat.S_ISREG(held.st_mode) or held.st_uid != os.getuid() or held.st_nlink != 1
                or stat.S_IMODE(held.st_mode) != 0o600 or held.st_size != FD.BASE.IMAGE_BYTES
                or (held.st_dev, held.st_ino) != self.image_identity):
            raise LabError("Flag seeding is restricted to this new private held regular image before stages")

    def inspect_flags(self, phase):
        self.assert_detached()
        self.verify_image()
        records = volume_records(self.held_fd, FD.BASE.IMAGE_BYTES)
        flags = [record["flags"] for record in records]
        result = {"phase": phase, "mftFlags": flags[0], "mirrorFlags": flags[1],
                  "mirrorsAgree": flags[0] == flags[1], "dirtyBitAbsent": not any(value & 1 for value in flags)}
        self.report["volumeFlagChecks"].append(result)
        if flags != [INFORMATIONAL_FLAG, INFORMATIONAL_FLAG]:
            raise LabError("Informational flag changed or dirty/unsupported flags remain after " + phase)
        return result

    def prepare(self):
        # Existing harness owns creation, formatting, baseline nonce, held FD,
        # pathname-replacement proof and the initial zero-flag health check.
        super().prepare()
        self.mutation_guard()
        before = self.hash_image()
        records = volume_records(self.held_fd, FD.BASE.IMAGE_BYTES)
        replacements = [flagged_record(record) for record in records]
        for record, replacement in zip(records, replacements):
            offset = 0
            while offset < len(replacement):
                self.mutation_guard()
                count = os.pwrite(self.held_fd, replacement[offset:], record["offset"] + offset)
                if not 0 < count <= len(replacement) - offset:
                    raise LabError("Short or invalid informational-flag write")
                offset += count
        self.mutation_guard()
        os.fsync(self.held_fd)
        self.inspect_flags("before-mount")
        self.original_hash = self.hash_image()
        self.report.update({"unflaggedImageSHA256": before,
                            "imageSHA256BeforeMount": self.original_hash,
                            "informationalFlagSeeded": True})
        PACKAGED.verify_payload()
        result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-c", FD.TRAMPOLINE,
            str(self.held_fd), str(PACKAGED.PAYLOAD / "bin/nativol-ntfs-health"), "/dev/fd/3"],
            pass_fds=(self.held_fd,), cwd=self.work, env=FD.ENV, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=20)
        if len(result.stdout) > 2048:
            raise LabError("Oversized informational-flag health response")
        health = json.loads(result.stdout)
        after = self.hash_image()
        self.report.update({"shortnamesHealth": health,
                            "shortnamesHealthImageUnchanged": after == self.original_hash})
        if (result.returncode or health.get("readyForWrite") is not True
                or health.get("volumeFlags") != INFORMATIONAL_FLAG
                or health.get("ntfsReadOnlyVerified") is not True or health.get("deniedOperations") != 0
                or after != self.original_hash):
            raise LabError("Packaged read-only health inspector did not accept the informational flag unchanged")

    def run_stage(self, phase):
        manifest = super().run_stage(phase)
        self.inspect_flags("after-" + phase + "-unmount")
        return manifest


def flag_evidence(report):
    checks = report.get("volumeFlagChecks", [])
    return (report.get("informationalFlagSeeded") is True
            and report.get("shortnamesHealthImageUnchanged") is True
            and [item.get("phase") for item in checks] ==
                ["before-mount", "after-rw-unmount", "after-ro-unmount"]
            and all(item.get("mftFlags") == INFORMATIONAL_FLAG and item.get("mirrorFlags") == INFORMATIONAL_FLAG
                    and item.get("mirrorsAgree") is True and item.get("dirtyBitAbsent") is True for item in checks))


def main():
    global STUCK
    STUCK = False
    if sys.argv[1:] not in ([], ["--execute"]):
        print("Only --execute is accepted; no image or physical target can be supplied.", file=sys.stderr)
        return 2
    report = {"schemaVersion": 1, "kind": "kernel-shortnames-flag-image", "status": "failed",
              "physicalDeviceTargetAccepted": False, "authorizationRequested": False,
              "kernelLoadingPerformed": False, "mountAttemptPerformed": False}
    lab = None
    try:
        tools, evidence = WRITE.preflight(bool(sys.argv[1:]))
        evidence["scriptSHA256"]["kernel-shortnames-image-lab.py"] = REFERENCE.digest(Path(__file__))
        evidence["scriptSHA256"]["kernel-fd-image-lab.py"] = REFERENCE.digest(Path(__file__).with_name("kernel-fd-image-lab.py"))
        report["build"] = evidence
        if not sys.argv[1:]:
            report["status"] = "prepared"
        else:
            os.umask(0o077)
            lab = ShortnamesLab(tools, report)
            lab.execute()
            if not WRITE.success_evidence(report) or not flag_evidence(report):
                raise LabError("Incomplete lifecycle or informational-flag preservation evidence")
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:500]
    finally:
        if lab is not None:
            STUCK = any(stage.report.get("inspectionWorkerStillRunning") for stage in lab.stages)
            report["cleanupRequired"] = bool(report.get("detachedStateUncertain") or STUCK
                or any(stage.report.get("cleanupRequired") for stage in lab.stages))
            report["mountAttemptPerformed"] = any(stage.report.get("mountAttemptPerformed") for stage in lab.stages)
            if not report["cleanupRequired"] and lab.held_fd is not None:
                os.close(lab.held_fd)
            if report["cleanupRequired"]:
                report["status"] = "failed"
            try:
                with (lab.work / "shortnames-report.json").open("x") as stream:
                    json.dump(report, stream, indent=2)
            except OSError as error:
                report.update({"status": "failed", "reportSaveError": str(error)[:300]})
        print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    REFERENCE.finish_supervisor(main(), STUCK)
