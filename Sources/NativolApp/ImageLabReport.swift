import Foundation

/// A local developer test artifact, never an input to mount authorization.
struct ImageLabReport: Decodable {
    struct Check: Decodable, Identifiable {
        var id: String { name }
        let name: String
        let passed: Bool
    }
    let schemaVersion: Int
    let kind: String
    let createdAt: String
    let architecture: String
    let macOS: String
    let status: String
    let physicalDevicesAccessed: Bool
    let mountedFilesystemTested: Bool
    let checks: [Check]
    let engineVersion: String?
    let error: String?

    var passed: Bool {
        status == "passed" && checks.count == 10 && checks.allSatisfy(\.passed)
    }

    static func load() -> (report: ImageLabReport?, error: String?) {
        let path = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Nativol", isDirectory: true).appendingPathComponent("image-lab-report.json")
        guard FileManager.default.fileExists(atPath: path.path) else { return (nil, nil) }
        do {
            let handle = try FileHandle(forReadingFrom: path)
            defer { try? handle.close() }
            guard let data = try handle.read(upToCount: 65_537), data.count <= 65_536 else {
                return (nil, "The local test report is too large to read.")
            }
            let report = try JSONDecoder().decode(ImageLabReport.self, from: data)
            guard report.schemaVersion == 1, report.kind == "unmounted-ntfs-image",
                  !report.physicalDevicesAccessed, !report.mountedFilesystemTested,
                  report.checks.count <= 30 else {
                return (nil, "The local report is not a recognized isolated image test.")
            }
            return (report, nil)
        } catch {
            return (nil, "The local test report could not be read. Re-run the developer image test.")
        }
    }
}
