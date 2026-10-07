#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
check_dir="$(mktemp -d "${TMPDIR:-/tmp}/nativol-helper-checks.XXXXXX")"
trap 'rm -rf "$check_dir"' EXIT
clang -mmacosx-version-min=12.0 -O1 -fobjc-arc -Wno-deprecated-declarations \
  -framework Foundation -framework DiskArbitration -framework IOKit -framework SystemConfiguration \
  "$project_root/Tests/NativolHelperTests/HelperValidationChecks.m" -o "$check_dir/helper-checks"
"$check_dir/helper-checks"
