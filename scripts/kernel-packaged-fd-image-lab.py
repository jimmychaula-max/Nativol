#!/usr/bin/env python3
"""Fixed packaged-engine variant of the fresh held-FD image diagnostic.

No helper, authorization, physical target, backend load or installation. Only
--execute performs the fresh image test; default mode verifies pinned payload.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "dist/Nativol.app/Contents/Resources/Backend"
PINS = {
    "bin/ntfs-3g": "132bc332f75512dab0993393190c42f435b4498fbb816c372cfec99caaa084b5",
    "bin/nativol-ntfs-health": "81be251e1bb9170117061dff7a33df77301fafe4811d16fc9a88067f4a654583",
    "lib/libfuse.2.dylib": "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42",
}
spec = importlib.util.spec_from_file_location("nativol_packaged_fd_lab", Path(__file__).with_name("kernel-fd-image-lab.py"))
FD = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = FD
spec.loader.exec_module(FD)
original_verify = FD.BASE.verify_tool
original_preflight = FD.WRITE.preflight
FD.BASE.TOOL_RECORDS["ntfs-3g"] = ("driver/x86_64/ntfs-3g", PINS["bin/ntfs-3g"])
FD.ENV["DYLD_LIBRARY_PATH"] = str(PAYLOAD / "lib")


def verify_payload():
    for relative, expected in PINS.items():
        path = PAYLOAD / relative
        info = path.lstat()
        if (path.resolve() != path or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o022
                or FD.REFERENCE.digest(path) != expected):
            raise FD.LabError("Packaged payload differs from reviewed pins")
        if relative.startswith("bin/"):
            result = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)],
                                    capture_output=True, timeout=10)
            if result.returncode: raise FD.LabError("Packaged executable signature failed")


def verify_tool(name):
    if name != "ntfs-3g": return original_verify(name)
    verify_payload()
    return PAYLOAD / "bin/ntfs-3g"


def preflight(execute):
    verify_payload()
    tools, record = original_preflight(execute)
    record["packagedPayloadSHA256"] = PINS
    record["privateLibraryPath"] = str(PAYLOAD / "lib")
    record["scriptSHA256"][Path(__file__).name] = FD.REFERENCE.digest(Path(__file__))
    return tools, record


class PackagedLab(FD.FDLab):
    def prepare(self):
        super().prepare()
        verify_payload()
        before = self.hash_image()
        result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-c", FD.TRAMPOLINE,
            str(self.held_fd), str(PAYLOAD / "bin/nativol-ntfs-health"), "/dev/fd/3"],
            pass_fds=(self.held_fd,), cwd=self.work, env=FD.ENV, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=20)
        if len(result.stdout) > 2048: raise FD.LabError("Oversized health response")
        health = json.loads(result.stdout)
        after = self.hash_image()
        self.report.update({"packagedHealth": health, "healthImageUnchanged": before == after})
        if result.returncode or health.get("readyForWrite") is not True or before != after:
            raise FD.LabError("Packaged readonly health inspector failed")


FD.BASE.verify_tool = verify_tool
FD.WRITE.preflight = preflight
FD.FDLab = PackagedLab

if __name__ == "__main__":
    FD.REFERENCE.finish_supervisor(FD.main(), FD.STUCK)
