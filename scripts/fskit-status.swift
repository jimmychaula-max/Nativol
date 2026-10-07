// Read-only FSKit discovery using Apple's public management API.
// Usage: swift scripts/fskit-status.swift
// An empty third-party list is inconclusive on Sequoia; verify vendor state too.
import Foundation
import FSKit
import Darwin

guard CommandLine.arguments.count == 1 else {
    fputs("This status check accepts no arguments.\n", stderr)
    exit(2)
}
if #available(macOS 15.4, *) {
    DispatchQueue.global().asyncAfter(deadline: .now() + 15) {
        fputs("FSKit discovery timed out.\n", stderr)
        exit(3)
    }
    Task {
        do {
            let extensions = try await FSClient.shared.installedExtensions
            let modules: [[String: Any]] = extensions
                .filter { $0.bundleIdentifier.hasPrefix("io.macfuse.") }
                .map { ["bundleIdentifier": $0.bundleIdentifier,
                        "enabled": $0.isEnabled, "path": $0.url.path] }
            let data = try JSONSerialization.data(withJSONObject: ["modules": modules,
                "reportedModuleCount": extensions.count],
                                                  options: [.prettyPrinted, .sortedKeys])
            print(String(decoding: data, as: UTF8.self))
            exit(0)
        } catch {
            fputs("FSKit discovery failed: \(error)\n", stderr)
            exit(1)
        }
    }
    dispatchMain()
} else {
    fputs("FSKit discovery requires macOS 15.4 or later.\n", stderr)
    exit(2)
}
