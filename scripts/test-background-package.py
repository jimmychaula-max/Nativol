#!/usr/bin/env python3
"""Check the generated installer without installation, authorization or disks."""
import os
from pathlib import Path
import re
import shlex
import subprocess

assert os.geteuid() != 0, "Run packaging checks as the ordinary user"
root = Path(__file__).resolve().parent.parent
app = root / "dist/Nativol.app"
subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
script = subprocess.run([str(app / "Contents/MacOS/Nativol"), "--print-background-install-command"],
                        check=True, capture_output=True, text=True, timeout=20).stdout
subprocess.run(["/bin/sh", "-n"], input=script, text=True, check=True, timeout=10)
lines = [line.strip() for line in script.splitlines() if line.strip().startswith("/usr/bin/codesign --verify --strict -a ")]
assert len(lines) == 1, "Installer must check one explicitly selected native code identity"
command = shlex.split(lines[0])
assert len(command) == 8
assert command[:4] == ["/usr/bin/codesign", "--verify", "--strict", "-a"]
assert command[4] in ("x86_64", "arm64") and command[5] == "-R"
assert re.fullmatch(r'=identifier "app\.nativol\.preview" and cdhash H"[0-9a-f]{40}"', command[6])
assert command[7] == "/Applications/Nativol.app"
# Exercise the exact inline requirement and native slice on the same staged
# signed bundle that will be copied to Applications. Never execute the installer.
command[7] = str(app)
subprocess.run(command, check=True, timeout=10)
print("PASS: complete bundle signature, installer syntax and exact native app requirement; no installation")
