#!/bin/bash
# Local developer bootstrap: no sudo, dependency installation, or device access.
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:---check}"
if [ "$#" -gt 1 ]; then
  echo "Usage: ./setup.sh [--check|--build|--help]" >&2
  exit 2
fi
case "$mode" in
  --help)
    echo "Usage: ./setup.sh [--check|--build]"
    echo "--check (default): check developer tools and run pure validation tests."
    echo "--build: also build the Intel app; see docs/BUILDING.md for prerequisites."
    exit 0 ;;
  --check|--build) ;;
  *) echo "Unknown option. Use ./setup.sh --help." >&2; exit 2 ;;
esac
if [ "$(uname -s)" != Darwin ] || [ "$(id -u)" -eq 0 ]; then
  echo "Run as an ordinary user on macOS. This script does not need administrator access." >&2
  exit 1
fi
if [ "$(uname -m)" != x86_64 ] || [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then
  echo "This beta bootstrap targets native Intel Macs. Apple Silicon work is paused." >&2
  exit 1
fi
for tool in xcrun swift clang python3; do
  command -v "$tool" >/dev/null || { echo "Missing developer tool: $tool. See docs/BUILDING.md." >&2; exit 1; }
done
xcrun --find swiftc >/dev/null
swift --version | python3 -c 'import re,sys; text=sys.stdin.read(); m=re.search(r"Swift version (\d+)\.(\d+)",text); sys.exit(0 if m and tuple(map(int,m.groups())) >= (5,9) else "Swift 5.9 or newer is required.")'
bash scripts/test-core.sh
bash scripts/test-helper.sh
bash scripts/test-background-service.sh
python3 scripts/test-source-package.py
if [ "$mode" = --build ]; then
  bash scripts/build-app.sh --intel
else
  echo "Developer checks passed. Use ./setup.sh --build for the Intel app."
fi
