#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/nativol-core-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
target_arch="$(uname -m)"

# Compile the very same checks used by XCTest. This route needs only Apple's
# Command Line Tools; `swift test` needs the XCTest runtime from full Xcode.
xcrun swiftc \
    -swift-version 5 \
    -parse-as-library \
    -target "${target_arch}-apple-macosx12.0" \
    -D NATIVOL_STANDALONE_CHECKS \
    "$project_root"/Sources/NativolCore/*.swift \
    "$project_root"/Tests/NativolCoreTests/*.swift \
    -framework DiskArbitration \
    -o "$test_dir/core-checks"

"$test_dir/core-checks"
