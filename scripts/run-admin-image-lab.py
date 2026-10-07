#!/usr/bin/env python3
"""Prepare a pinned, fresh-image administrator diagnostic; --execute prompts macOS.

Default mode only prepares private local files and a reviewable root command.
There are no supplied device, image, mountpoint, command, or report arguments.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import stat
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parent.parent
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "en_US.UTF-8"}
PINS = {
    "fskit-admin-image-lab.py": "9ef9316fd7775ab0ed2e5635623b22bd0364f44a37bb460ce11f34748ebc0bc3",
    "mkntfs": "9af42ef92fad65a533dc8a5cbfc802941d7d17baffd71c0bf13c84ad327c388f",
    "ntfscp": "0522188e9cdb137ea63ea074aaa164b90122b4a13546a826719ab92039123b66",
    "ntfs-3g": "7e10c1cc3a750b8d5deece7c8633303f1a1af65b1f2d499152db429f46ab7f43",
    "libfuse.2.dylib": "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42",
}
SOURCES = {
    "fskit-admin-image-lab.py": ROOT / "scripts/fskit-admin-image-lab.py",
    "mkntfs": ROOT / ".local-engine/bin/x86_64/mkntfs",
    "ntfscp": ROOT / ".local-engine/bin/x86_64/ntfscp",
    "ntfs-3g": ROOT / ".local-engine/driver/x86_64/ntfs-3g",
    "libfuse.2.dylib": ROOT / ".local-fuse-inspect/release-pkg/Core.pkg/Payload/usr/local/lib/libfuse.2.dylib",
}
APPLE_SCRIPT = '''on run argv
    return do shell script (item 1 of argv) with administrator privileges
end run'''

# This reviewed bootstrap is passed as code to the protected system Python,
# never loaded as a privileged Python script from the user-writable workspace.
# The only embedded data are freshly prepared fixed paths, UID, and pinned hashes.
BOOTSTRAP = r'''
import hashlib,json,os,pathlib,stat,subprocess,sys,tempfile
os.umask(0o077)
assert os.getuid() == 0 and os.geteuid() == 0
assert sys.flags.isolated and sys.flags.no_site
payload=pathlib.Path(PAYLOAD_VALUE)
pins=PINS_VALUE
requester=UID_VALUE
stage=pathlib.Path(tempfile.mkdtemp(prefix="nativol-admin-ro.",dir="/private/tmp"))
print("NATIVOL_ROOT_STAGE="+str(stage),flush=True)
print("NATIVOL_ROOT_STAGE="+str(stage),file=sys.stderr,flush=True)
clean={"PATH":"/usr/bin:/bin:/usr/sbin:/sbin","LC_ALL":"en_US.UTF-8"}
try:
    temporary=pathlib.Path("/private/tmp").lstat()
    assert stat.S_ISDIR(temporary.st_mode) and temporary.st_uid == 0 and temporary.st_mode & stat.S_ISVTX
    for directory in (stage,stage/"bin",stage/"Frameworks"):
        if directory != stage: directory.mkdir(mode=0o700)
        os.chown(directory,0,0)
        subprocess.run(["/bin/chmod","-N",str(directory)],env=clean,check=True)
        os.chmod(directory,0o700)
        info=directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700
    pfd=os.open(payload,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    pinfo=os.fstat(pfd)
    assert pinfo.st_uid == requester and stat.S_IMODE(pinfo.st_mode) == 0o700
    try:
        for name,expected in pins.items():
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=pfd)
            with os.fdopen(fd,"rb") as stream:
                info=os.fstat(stream.fileno())
                assert stat.S_ISREG(info.st_mode) and info.st_uid == requester and info.st_nlink == 1
                assert stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size < 20*1024*1024
                data=stream.read(20*1024*1024)
                assert hashlib.sha256(data).hexdigest() == expected, "Payload checksum mismatch: "+name
            target=stage/"bin"/name
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,"wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(target,0,0)
            subprocess.run(["/bin/chmod","-N",str(target)],env=clean,check=True)
            os.chmod(target,0o600 if name.endswith(".py") else 0o755)
    finally:
        os.close(pfd)
    # Recheck every protected copy, including the reviewed runner, before execution.
    for name,expected in pins.items():
        target=stage/"bin"/name
        fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb") as stream:
            info=os.fstat(stream.fileno())
            mode=0o600 if name.endswith(".py") else 0o755
            assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            assert stat.S_IMODE(info.st_mode) == mode
            assert hashlib.sha256(stream.read()).hexdigest() == expected, "Staged checksum mismatch: "+name
    # Regular files avoid waiting for EOF from a stuck descendant that inherited
    # pipes after the runner supervisor has deliberately exited.
    with open(stage/"runner.stdout","x") as out, open(stage/"runner.stderr","x") as err:
        result=subprocess.run(["/usr/bin/python3","-I","-S",str(stage/"bin/fskit-admin-image-lab.py")],
                              cwd=str(stage),env=clean,stdin=subprocess.DEVNULL,stdout=out,stderr=err)
    with open(stage/"runner.stdout") as stream: captured_out=stream.read(2*1024*1024)
    with open(stage/"runner.stderr") as stream: captured_err=stream.read(2*1024*1024)
    print("NATIVOL_RESULT="+json.dumps({"rootStage":str(stage),"runnerExit":result.returncode,
                                      "runnerStdout":captured_out,"runnerStderr":captured_err}),flush=True)
    # Preserve expected diagnostic failures through AppleScript, which otherwise
    # discards stdout for a nonzero shell exit. The launcher checks runnerExit.
except BaseException as error:
    print("Bootstrap failed; retained root stage: "+str(stage)+": "+str(error),file=sys.stderr,flush=True)
    raise
'''


def private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022
            or stat.S_IMODE(info.st_mode) != 0o700 or path.resolve() != path):
        raise RuntimeError("Expected canonical user-owned private directory: " + str(path))


def verified_bytes(path, expected):
    if path.resolve(strict=True) != path:
        raise RuntimeError("Source path contains a symlink: " + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or not 0 < info.st_size < 20 * 1024 * 1024):
            raise RuntimeError("Unsafe local payload source: " + str(path))
        data = stream.read(20 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Pinned source checksum mismatch: " + str(path))
    return data


def main():
    if sys.argv[1:] not in ([], ["--execute"]):
        raise RuntimeError("Usage: run-admin-image-lab.py [--execute]; no target arguments accepted")
    if sys.platform != "darwin" or os.getuid() == 0 or os.geteuid() != os.getuid():
        raise RuntimeError("Run as the ordinary logged-in macOS user")
    if platform.machine() != "x86_64":
        raise RuntimeError("This pinned administrator experiment currently supports the Intel development Mac only")
    os.umask(0o077)
    data = {name: verified_bytes(SOURCES[name], expected) for name, expected in PINS.items()}
    # The authorized process must not inherit a Documents working directory or
    # read its input there. Stage verified copies as the ordinary user first.
    temporary = Path("/private/tmp")
    info = temporary.lstat()
    if (temporary.resolve() != temporary or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0 or not info.st_mode & stat.S_ISVTX):
        raise RuntimeError("Expected canonical root-owned sticky /private/tmp")
    payload = Path(tempfile.mkdtemp(prefix="nativol-admin-input-", dir=str(temporary)))
    private_directory(payload)
    for name, content in data.items():
        fd = os.open(payload / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    bootstrap = (BOOTSTRAP.replace("PAYLOAD_VALUE", repr(str(payload)))
                 .replace("PINS_VALUE", repr(PINS)).replace("UID_VALUE", repr(os.getuid())))
    command = shlex.join(["/usr/bin/env", "-i", "PATH=" + ENV["PATH"], "LC_ALL=" + ENV["LC_ALL"],
                          "/usr/bin/python3", "-I", "-S", "-c", bootstrap])
    (payload / "root-command.sh").write_text(command + "\n")
    manifest = {"status": "prepared", "requesterUID": os.getuid(), "architecture": "x86_64",
                "payload": str(payload), "pins": PINS,
                "rootStageTemplate": "/private/tmp/nativol-admin-ro.XXXXXXXX",
                "reviewCommand": str(payload / "root-command.sh")}
    (payload / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)
    if not sys.argv[1:]:
        return 0
    # The prepared command is passed as an AppleScript argument, never embedded
    # into AppleScript source. No environment or command arguments come from CLI.
    result = subprocess.run(["/usr/bin/osascript", "-e", APPLE_SCRIPT, command],
                            cwd="/private/tmp", env=ENV, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True)
    report = dict(manifest, status="bootstrap-failed", authorizationExit=result.returncode,
                  authorizationStdout=result.stdout, authorizationStderr=result.stderr)
    raw_report = payload / "authorization-result.json"
    fd = os.open(raw_report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print("Raw authorization result retained: " + str(raw_report), flush=True)
    exit_code = 1
    if result.returncode == 0:
        messages = [line.removeprefix("NATIVOL_RESULT=") for line in result.stdout.splitlines()
                    if line.startswith("NATIVOL_RESULT=")]
        if len(messages) == 1:
            try:
                envelope = json.loads(messages[0])
                if not isinstance(envelope, dict) or not isinstance(envelope.get("runnerStdout"), str):
                    raise ValueError("Invalid runner envelope")
                report.update(envelope)
                report["diagnostic"] = json.loads(envelope["runnerStdout"])
                if not isinstance(report["diagnostic"], dict):
                    raise ValueError("Invalid diagnostic object")
                passed = (envelope["runnerExit"] == 0 and report["diagnostic"].get("status") == "passed")
                report["status"] = "passed" if passed else "diagnostic-failed"
                exit_code = 0 if passed else 1
            except (ValueError, TypeError, KeyError):
                report["status"] = "invalid-diagnostic-output"
    reports = ROOT / "local-reports"
    if not reports.exists():
        reports.mkdir(mode=0o700)
    if reports.is_symlink() or reports.resolve() != reports:
        raise RuntimeError("Refusing symlinked report directory; output retained in " + str(raw_report))
    destination = reports / ("admin-image-" + uuid.uuid4().hex + ".json")
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print("Diagnostic report: " + str(destination))
    print("Diagnostic status: " + report["status"])
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print("Preparation/launch failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
