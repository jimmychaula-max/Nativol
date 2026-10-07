#!/bin/bash
# Compatibility entry point: the public tree produces an Intel testing DMG.
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash scripts/package-test-dmg.sh "$@"
