#!/bin/bash
# Ordinary-user local IPC tests only; never starts the daemon or any helper.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
test "$(id -u)" != 0
test "$#" = 0
mkdir -p .local-engine/service-tests
work="$(mktemp -d "$PWD/.local-engine/service-tests/test.XXXXXXXX")"
/usr/bin/clang -O1 -fobjc-arc -Wall -Wextra -Wno-unused-function -Wno-unused-variable \
  -Wno-deprecated-declarations -framework Foundation -framework Security \
  -framework SystemConfiguration -lbsm scripts/test-background-service.m -o "$work/service-tests"
"$work/service-tests"
