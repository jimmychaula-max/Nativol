#!/usr/bin/env python3
"""Test launch-event interpretation without registration, app UI, or disk access."""
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
import AppKit
import CoreServices

@main
struct LoginStartupChecks {
    @MainActor static func main() {
        var checks = 0
        func check(_ actual: Bool, _ expected: Bool) {
            precondition(actual == expected, "Unexpected launch classification at check \(checks + 1)")
            checks += 1
        }
        func event(_ id: AEEventID = kAEOpenApplication) -> NSAppleEventDescriptor {
            NSAppleEventDescriptor(eventClass: kCoreEventClass, eventID: id, targetDescriptor: nil,
                returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID))
        }
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: nil), false)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol", "--background"], event: nil), true)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol", "--background-status"], event: nil), false)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: event()), false)
        let login = event()
        login.setParam(NSAppleEventDescriptor(enumCode: keyAELaunchedAsLogInItem), forKeyword: keyAEPropData)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: login), true)
        let direct = event()
        direct.setParam(NSAppleEventDescriptor(boolean: true), forKeyword: keyAELaunchedAsLogInItem)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: direct), true)
        let reopen = event(kAEReopenApplication)
        reopen.setParam(NSAppleEventDescriptor(enumCode: keyAELaunchedAsLogInItem), forKeyword: keyAEPropData)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: reopen), false)
        let other = event()
        other.setParam(NSAppleEventDescriptor(enumCode: keyAELaunchedAsServiceItem), forKeyword: keyAEPropData)
        check(LoginStartup.launchedInBackground(arguments: ["Nativol"], event: other), false)
        print("Login startup interpretation: \(checks) checks passed")
    }
}
'''


def main():
    if len(sys.argv) != 1 or os.getuid() == 0 or platform.system() != "Darwin":
        raise RuntimeError("Requires ordinary macOS user, no arguments")
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    with tempfile.TemporaryDirectory(prefix="nativol-login-checks-") as directory:
        root = Path(directory)
        source = root / "LoginStartupChecks.swift"
        source.write_text(HARNESS)
        binary = root / "login-startup-checks"
        subprocess.run(["/usr/bin/swiftc", "-target", platform.machine() + "-apple-macosx12.0",
                        str(ROOT / "Sources/NativolApp/LoginStartup.swift"), str(source), "-o", str(binary)],
                       check=True, env=environment, cwd=root, timeout=60)
        subprocess.run([str(binary)], check=True, env=environment, cwd=root, timeout=15)


if __name__ == "__main__":
    main()
