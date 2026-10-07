import AppKit
import CryptoKit
import Darwin
import Foundation
import NativolCore

struct ManagedCardStatus: Decodable, Equatable {
    let schemaVersion: Int
    let operationId: String
    let bootSessionUUID: String
    let state: String
    let mode: String
    let ownerUID: UInt32
    let mountPath: String
    let source: String
    let filesystem: String
    let helperPID: Int32
    let driverPID: Int32?
    let fsid: [Int32]?
    let partitionRegistryID: String
    let mediaRegistryID: String
    let helperVersionDir: String
    let targetProfile: String
    let message: String
    let updatedAt: Double

    var isMounted: Bool { state == "mounted" && hasCurrentMount }
    var isActive: Bool { ["preparing", "mounted", "unmounting", "attention"].contains(state) }
    var isReadOnly: Bool { mode == "ro" }
    var hasCurrentMount: Bool {
        guard let fsid, fsid.count == 2 else { return false }
        var pointer: UnsafeMutablePointer<statfs>?
        let count = getmntinfo(&pointer, MNT_NOWAIT)
        guard let pointer, count > 0 else { return false }
        return (0..<Int(count)).contains { index in
            var m = pointer[index]
            func string<T>(_ value: inout T) -> String {
                withUnsafePointer(to: &value) { $0.withMemoryRebound(to: CChar.self, capacity: MemoryLayout<T>.size) { String(cString: $0) } }
            }
            return string(&m.f_mntonname) == mountPath && string(&m.f_mntfromname) == source
                && string(&m.f_fstypename) == filesystem && m.f_owner == ownerUID
                && m.f_fsid.val.0 == fsid[0] && m.f_fsid.val.1 == fsid[1]
                && ((m.f_flags & UInt32(MNT_RDONLY)) != 0) == isReadOnly
        }
    }

    func matches(_ volume: Volume) -> Bool {
        volume.partitionRegistryID.map(String.init) == partitionRegistryID
            && volume.mediaRegistryID.map(String.init) == mediaRegistryID
    }
}

enum PersonalBackend {
    static let base = "/Library/Application Support/Nativol"
    static let stateDirectory = base + "/State"
    static let privateDirectory = "/private/var/db/nativol"
    static let fuseRuntimePath = "/usr/local/lib/libfuse.2.dylib"
    static let fuseRuntimeHash = "7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42"

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func quote(_ text: String) -> String { "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'" }
    static func bootSession() throws -> UUID {
        var buffer = [CChar](repeating: 0, count: 128)
        var size = buffer.count
        guard sysctlbyname("kern.bootsessionuuid", &buffer, &size, nil, 0) == 0,
              let id = UUID(uuidString: String(cString: buffer)) else { throw Failure(message: "Cannot identify this Mac session.") }
        return id
    }

    static func payload() throws -> (URL, String, [String: String]) {
        guard let root = Bundle.main.resourceURL?.appendingPathComponent("Backend"),
              let data = try? Data(contentsOf: root.appendingPathComponent("manifest.json")),
              let manifest = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let files = manifest["files"] as? [String: String],
              manifest["schemaVersion"] as? Int == 1,
              manifest["profile"] as? String == "intel-external-test-v1" else {
            throw Failure(message: "This app does not contain a valid NTFS backend. Build the complete application bundle.")
        }
        let allowed: Set<String> = ["bin/NativolHelper", "bin/ntfs-3g", "bin/nativol-ntfs-health", "lib/libfuse.2.dylib", "licenses/NTFS-3G-COPYING", "licenses/NTFS-3G-COPYING.LIB"]
        guard Set(files.keys) == allowed else { throw Failure(message: "Unexpected backend package contents.") }
        for (file, digest) in files {
            guard digest.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
                throw Failure(message: "Invalid backend checksum.")
            }
            if file == "lib/libfuse.2.dylib" {
                guard digest == fuseRuntimeHash,
                      !FileManager.default.fileExists(atPath: root.appendingPathComponent(file).path) else {
                    throw Failure(message: "macFUSE must be installed separately from its official installer.")
                }
                continue
            }
            let bytes = try Data(contentsOf: root.appendingPathComponent(file))
            guard hash(bytes) == digest else { throw Failure(message: "The bundled backend checksum differs. Rebuild the app.") }
        }
        return (root, hash(data), files)
    }
    static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static var installed: Bool {
        guard let (_, version, _) = try? payload() else { return false }
        return isProtected(base + "/Versions/" + version + "/bin/NativolHelper", directory: false)
    }
    static var supportedHost: Bool {
        #if arch(x86_64)
        let os = ProcessInfo.processInfo.operatingSystemVersion
        for key in ["hw.optional.arm64", "sysctl.proc_translated"] {
            var value: Int32 = 0
            var size = MemoryLayout<Int32>.size
            if sysctlbyname(key, &value, &size, nil, 0) == 0, value != 0 { return false }
        }
        return os.majorVersion == 15 && os.minorVersion == 7 && os.patchVersion == 9
        #else
        return false
        #endif
    }
    static func isProtected(_ path: String, directory: Bool) -> Bool {
        var s = stat()
        guard lstat(path, &s) == 0, s.st_uid == 0, s.st_mode & 0o022 == 0,
              s.st_mode & S_IFMT == (directory ? S_IFDIR : S_IFREG) else { return false }
        if path == "/" { return true }
        return isProtected((path as NSString).deletingLastPathComponent, directory: true)
    }
    static func statuses() -> [ManagedCardStatus] {
        guard isProtected(stateDirectory, directory: true), let boot = try? bootSession(),
              let urls = try? FileManager.default.contentsOfDirectory(at: URL(fileURLWithPath: stateDirectory), includingPropertiesForKeys: nil) else { return [] }
        return urls.compactMap { url -> ManagedCardStatus? in
            guard url.pathExtension == "json", let id = UUID(uuidString: url.deletingPathExtension().lastPathComponent),
                  isProtected(url.path, directory: false) else { return nil }
            var s = stat()
            guard lstat(url.path, &s) == 0, s.st_size > 0, s.st_size <= 65536,
                  let data = try? Data(contentsOf: url), let value = try? JSONDecoder().decode(ManagedCardStatus.self, from: data),
                  value.schemaVersion == 1, UUID(uuidString: value.operationId) == id,
                  UUID(uuidString: value.bootSessionUUID) == boot, value.ownerUID == getuid(),
                  value.targetProfile == "intel-external-test-v1", value.source == "/dev/fd/3", value.filesystem == "macfuse",
                  value.mountPath == "/Volumes/Nativol-" + id.uuidString,
                  value.helperVersionDir.hasPrefix(base + "/Versions/"),
                  value.helperVersionDir.split(separator: "/").last?.count == 64 else { return nil }
            return value
        }.sorted { $0.updatedAt > $1.updatedAt }
    }

    /// Copies only pinned files into a root-private staging directory, verifies
    /// every digest there, then publishes a protected immutable version.
    static func installCommand() throws -> String {
        let (source, version, files) = try payload()
        // This runs before constructing/executing any privileged setup script.
        // /usr/local/lib can be user managed, so trust the exact pinned content,
        // then verify the copy again inside the root-private staging directory.
        guard let runtime = try? Data(contentsOf: URL(fileURLWithPath: fuseRuntimePath)),
              hash(runtime) == fuseRuntimeHash else {
            throw Failure(message: "Install official macFUSE 5.4.0 before setting up Nativol. The required runtime is missing or differs from the supported version.")
        }
        let final = base + "/Versions/" + version
        var script = """
        set -eu
        umask 077
        check_directory() {
          test -d "$1" && test ! -L "$1"
          test "$(/usr/bin/stat -f %u "$1")" = 0
          mode="$(/usr/bin/stat -f %Lp "$1")"
          test "$((0$mode & 022))" = 0
        }
        check_directory '/Library'
        check_directory '/Library/Application Support'
        check_directory '/private'
        check_directory '/private/var'
        check_directory '/private/var/db'
        """
        for dir in [base, base + "/Versions", stateDirectory, privateDirectory, privateDirectory + "/sessions"] {
            script += "\nif test -e \(quote(dir)) || test -L \(quote(dir)); then check_directory \(quote(dir)); else /bin/mkdir \(quote(dir)); fi\n"
            script += "/bin/chmod \(dir.hasPrefix(privateDirectory) ? "0700" : "0755") \(quote(dir))\n"
        }
        script += "\nif test ! -e \(quote(final)); then\n"
        script += "stage=$(/usr/bin/mktemp -d \(quote(base + "/Versions/.install.XXXXXXXX")))\n"
        script += "/bin/mkdir \"$stage/bin\" \"$stage/lib\" \"$stage/licenses\"\n"
        for (file, digest) in files.sorted(by: { $0.key < $1.key }) {
            let input = file == "lib/libfuse.2.dylib" ? fuseRuntimePath : source.appendingPathComponent(file).path
            script += "/usr/bin/install -m 0600 \(quote(input)) \"$stage/\(file)\"\n"
            script += "test \"$(/usr/bin/shasum -a 256 \"$stage/\(file)\" | /usr/bin/awk '{print $1}')\" = \(quote(digest))\n"
        }
        script += "/usr/bin/install -m 0600 \(quote(source.appendingPathComponent("manifest.json").path)) \"$stage/manifest.json\"\n"
        script += "test \"$(/usr/bin/shasum -a 256 \"$stage/manifest.json\" | /usr/bin/awk '{print $1}')\" = \(quote(version))\n"
        script += "/usr/sbin/chown -R root:wheel \"$stage\"\n/bin/chmod 0755 \"$stage\" \"$stage/bin\" \"$stage/lib\" \"$stage/licenses\"\n/bin/chmod 0555 \"$stage/bin/\"*\n/bin/chmod 0444 \"$stage/manifest.json\" \"$stage/lib/\"* \"$stage/licenses/\"*\n/bin/mv \"$stage\" \(quote(final))\nfi\ncheck_directory \(quote(final))\n"
        script += "/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin " + quote(final + "/bin/NativolHelper") + " verify-installation -\n"
        return script
    }
    static func install() async throws { try await authorize(installCommand()) }
    static func mount(_ request: ExperimentalWriteRequest, readOnly: Bool = false) async throws {
        if BackgroundService.installed || UserDefaults.standard.bool(forKey: "BackgroundAccessConfigured") {
            try await BackgroundService.mount(request, readOnly: readOnly)
            return
        }
        let (_, version, _) = try payload()
        guard installed else { throw Failure(message: "Install the personal backend in Setup first.") }
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        let encoded = try encoder.encode(request).base64EncodedString()
        let identifier = request.operationId.uuidString
        let helper = base + "/Versions/" + version + "/bin/NativolHelper"
        let log = privateDirectory + "/launch-" + identifier + ".log"
        let status = stateDirectory + "/" + identifier + ".json"
        // The supervisor owns its own lifetime; quitting the GUI does not kill it.
        let script = """
        set -eu
        umask 077
        /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin \(quote(helper)) \(readOnly ? "mount-ro" : "mount-rw") \(quote(encoded)) >\(quote(log)) 2>&1 </dev/null &
        child=$!
        count=0
        while test "$count" -lt 100; do
          if test -f \(quote(status)); then exit 0; fi
          if ! /bin/kill -0 "$child" 2>/dev/null; then /bin/cat \(quote(log)); exit 1; fi
          /bin/sleep 0.2
          count=$((count + 1))
        done
        echo 'Helper startup has not completed. Check Nativol Activity before retrying.'
        exit 1
        """
        try await authorize(script)
    }
    static func stop(_ status: ManagedCardStatus, eject: Bool) async throws {
        guard status.isMounted, isProtected(status.helperVersionDir + "/bin/NativolHelper", directory: false) else {
            throw Failure(message: "This managed mount is no longer available. Refresh its status.")
        }
        if BackgroundService.installed || UserDefaults.standard.bool(forKey: "BackgroundAccessConfigured") {
            try await BackgroundService.stop(status, eject: eject)
            return
        }
        try await authorize("/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin " + quote(status.helperVersionDir + "/bin/NativolHelper") + " " + (eject ? "eject" : "unmount") + " " + quote(status.operationId))
    }
    static func authorize(_ command: String) async throws {
        let escaped = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        let script = "with timeout of 300 seconds\n do shell script \"" + escaped + "\" with administrator privileges\nend timeout"
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript"); process.arguments = ["-e", script]
                let output = Pipe(); process.standardOutput = output; process.standardError = output
                do {
                    try process.run()
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    if process.terminationStatus == 0 { continuation.resume() }
                    else { continuation.resume(throwing: Failure(message: String(data: data.prefix(4000), encoding: .utf8) ?? "macOS authorization was cancelled or the helper refused the action.")) }
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
}
