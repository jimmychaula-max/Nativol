#!/usr/bin/env python3
"""Prepare an in-memory kernel-backend reference test, without loading a driver.

Default invocation verifies the local build and reports kernel readiness only.
--execute is reserved for the explicitly selected kernel test after macOS setup;
it still refuses to start unless the exact verified kernel module is loaded.
No device, image, mountpoint, backend choice, or mount option can be supplied.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    # Spawned inspection workers import this wrapper and register the same
    # module names. No filesystem operation occurs during module import.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REFERENCE = load_sibling("nativol_kernel_reference", "fskit-reference-lab.py")
READINESS = load_sibling("nativol_kernel_readiness", "kernel-readiness.py")
REFERENCE.BACKEND = "kernel"
REFERENCE.BUILD_SUBDIR = "reference-kernel"
REFERENCE.BINARY_NAME = "kernel-reference"
REFERENCE.MOUNT_PREFIX = "Nativol-Kernel-Reference-"
REFERENCE.ALLOWED_TYPES = {"macfuse"}
BUILD_PREFLIGHT = REFERENCE.validate_invocation


def execution_preflight():
    binary, record = BUILD_PREFLIGHT()
    readiness = READINESS.inspect_readiness()
    if not readiness.get("verificationPassed") or not readiness.get("backendLoaded"):
        raise REFERENCE.LabError(
            "Exact verified macFUSE kernel module is not loaded; this diagnostic cannot load or approve it")
    return binary, dict(record, kernelReadiness=readiness)


def main():
    arguments = sys.argv[1:]
    if arguments not in ([], ["--execute"]):
        print("Only --execute is accepted; default mode prepares without mounting.", file=sys.stderr)
        return 2
    saved_arguments = sys.argv
    # The shared preflight deliberately accepts no caller-supplied targets.
    # The wrapper consumes its one fixed mode flag before invoking that guard.
    sys.argv = [sys.argv[0]]
    try:
        if arguments == ["--execute"]:
            REFERENCE.validate_invocation = execution_preflight
            return REFERENCE.main()

        report = {"schemaVersion": 1, "kind": "kernel-reference-preparation", "status": "failed",
                  "createdAt": datetime.now(timezone.utc).isoformat(), "backend": "kernel",
                  "mountAttemptPerformed": False, "kernelLoadingPerformed": False,
                  "physicalDeviceTargetAccepted": False, "readyToExecute": False}
        try:
            binary, record = BUILD_PREFLIGHT()
            readiness = READINESS.inspect_readiness()
            report.update({"binary": str(binary), "build": record, "readiness": readiness})
            if not readiness.get("verificationPassed"):
                raise REFERENCE.LabError("Installed kernel backend verification did not pass")
            report.update({"status": "prepared", "readyToExecute": bool(readiness.get("backendLoaded"))})
        except (OSError, ValueError, REFERENCE.LabError, subprocess.SubprocessError) as error:
            report["error"] = type(error).__name__ + ": " + str(error)[:500]
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "prepared" else 1
    finally:
        sys.argv = saved_arguments
        REFERENCE.validate_invocation = BUILD_PREFLIGHT


if __name__ == "__main__":
    code = main()
    REFERENCE.finish_supervisor(code, REFERENCE.INSPECTION_WORKER_STILL_RUNNING)
