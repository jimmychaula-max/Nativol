import CryptoKit
import Darwin
import Foundation
import NativolCore
import Security

/// Authenticated local IPC. Only installation invokes administrator authorization.
enum BackgroundService {
    private static let directory = PersonalBackend.base + "/Service"
    private static let socketPath = directory + "/control.sock"
    private static let label = "org.nativol.service"
    private static let stableApp = "/Applications/Nativol.app"
    private static let clientIdentifier = "app.nativol.preview"
    private static let maximumFrame = 32_768
    private typealias Failure = PersonalBackend.Failure

    private static func clientHash() throws -> String {
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else {
            throw Failure(message: "Cannot identify this signed app.")
        }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode else {
            throw Failure(message: "Cannot inspect this app's static signature.")
        }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let values = info as? [String: Any],
              let hash = values[kSecCodeInfoUnique as String] as? Data, hash.count == 20,
              values[kSecCodeInfoIdentifier as String] as? String == clientIdentifier,
              let flags = values[kSecCodeInfoFlags as String] as? NSNumber,
              flags.uint32Value & 0x10000 != 0, // kSecCodeSignatureRuntime, Security/CSCommon.h
              (values[kSecCodeInfoEntitlementsDict as String] as? [String: Any])?.isEmpty != false else {
            throw Failure(message: "Build the signed app with Hardened Runtime before setting up background access.")
        }
        return hash.map { String(format: "%02x", $0) }.joined()
    }

    private static func payload() throws -> (URL, String) {
        guard let source = Bundle.main.resourceURL?.appendingPathComponent("Service"),
              let bytes = try? Data(contentsOf: source.appendingPathComponent("manifest.json")), bytes.count <= 4096,
              let record = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              record["schemaVersion"] as? Int == 1,
              let files = record["files"] as? [String: String], Set(files.keys) == ["NativolService"],
              let expected = files["NativolService"], expected.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              PersonalBackend.hash(try Data(contentsOf: source.appendingPathComponent("NativolService"))) == expected else {
            throw Failure(message: "This app does not contain a verified background service.")
        }
        return (source, expected)
    }

    static var installed: Bool {
        guard Bundle.main.bundleURL.path == stableApp,
              PersonalBackend.isProtected(directory, directory: true),
              PersonalBackend.isProtected(directory + "/NativolService", directory: false),
              PersonalBackend.isProtected(directory + "/config.json", directory: false),
              let (_, serviceHash) = try? payload(), let hash = try? clientHash(),
              let (_, version, _) = try? PersonalBackend.payload(),
              let bytes = try? Data(contentsOf: URL(fileURLWithPath: directory + "/config.json")), bytes.count <= 4096,
              let config = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { return false }
        return config["schemaVersion"] as? Int == 1 && config["backendVersion"] as? String == version
            && config["clientCDHash"] as? String == hash && config["clientIdentifier"] as? String == clientIdentifier
            && config["serviceSHA256"] as? String == serviceHash
    }

    /// The caller first installs the protected stable app and personal backend.
    /// This command accepts no IPC-supplied paths; every value is local package data.
    static func installCommand() throws -> String {
        let (source, serviceHash) = try payload()
        let hash = try clientHash()
        let (_, backendVersion, _) = try PersonalBackend.payload()
        let config: [String: Any] = ["schemaVersion": 1, "backendVersion": backendVersion,
            "clientCDHash": hash, "clientIdentifier": clientIdentifier, "serviceSHA256": serviceHash]
        let configData = try JSONSerialization.data(withJSONObject: config, options: [.sortedKeys])
        let plist: [String: Any] = ["Label": label, "ProgramArguments": [directory + "/NativolService"],
            "RunAtLoad": true, "KeepAlive": true, "ThrottleInterval": 10, "UserName": "root",
            "AbandonProcessGroup": true, "ProcessType": "Background", "Umask": 0o077,
            "EnvironmentVariables": ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"],
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"]
        let plistData = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        let q = PersonalBackend.quote
        let daemonPath = "/Library/LaunchDaemons/" + label + ".plist"
        #if arch(arm64)
        let clientArchitecture = "arm64"
        #else
        let clientArchitecture = "x86_64"
        #endif
        let requirement = "=identifier \"\(clientIdentifier)\" and cdhash H\"\(hash)\""
        return """
        set -eu
        umask 077
        service_check_directory() {
          test -d "$1" && test ! -L "$1"
          test "$(/usr/bin/stat -f %u "$1")" = 0
          service_mode="$(/usr/bin/stat -f %Lp "$1")"
          test "$((0$service_mode & 022))" = 0
        }
        service_check_directory '/Library'
        service_check_directory '/Library/Application Support'
        service_check_directory \(q(PersonalBackend.base))
        service_check_directory '/Library/LaunchDaemons'
        /usr/bin/codesign --verify --strict -a \(clientArchitecture) -R \(q(requirement)) \(q(stableApp))
        if test -e \(q(directory)) || test -L \(q(directory)); then service_check_directory \(q(directory)); else /bin/mkdir -m 0755 \(q(directory)); fi
        service_stage=$(/usr/bin/mktemp -d \(q(directory + "/.install.XXXXXXXX")))
        /usr/bin/install -m 0500 \(q(source.appendingPathComponent("NativolService").path)) "$service_stage/NativolService"
        test "$(/usr/bin/shasum -a 256 "$service_stage/NativolService" | /usr/bin/awk '{print $1}')" = \(q(serviceHash))
        /usr/bin/codesign --verify --strict "$service_stage/NativolService"
        /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin "$service_stage/NativolService" --check-idle
        if /bin/launchctl print system/\(label) >/dev/null 2>&1; then /bin/launchctl bootout system/\(label); fi
        /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin "$service_stage/NativolService" --check-idle
        /usr/bin/printf '%s' \(q(configData.base64EncodedString())) | /usr/bin/base64 -D > "$service_stage/config.json"
        /usr/bin/printf '%s' \(q(plistData.base64EncodedString())) | /usr/bin/base64 -D > "$service_stage/daemon.plist"
        /usr/sbin/chown root:wheel "$service_stage/NativolService" "$service_stage/config.json" "$service_stage/daemon.plist"
        /bin/chmod 0555 "$service_stage/NativolService"
        /bin/chmod 0444 "$service_stage/config.json"
        /bin/chmod 0644 "$service_stage/daemon.plist"
        /bin/mv -f "$service_stage/NativolService" \(q(directory + "/NativolService"))
        /bin/mv -f "$service_stage/config.json" \(q(directory + "/config.json"))
        /bin/mv -f "$service_stage/daemon.plist" \(q(daemonPath))
        /bin/rmdir "$service_stage"
        /bin/launchctl bootstrap system \(q(daemonPath))
        """
    }

    static func status() async throws -> String {
        let response = try await request(["schemaVersion": 1, "verb": "status"])
        guard let value = response["status"] as? String else { throw Failure(message: "Invalid service status.") }
        return value
    }

    static func mount(_ request: ExperimentalWriteRequest, readOnly: Bool) async throws {
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        let body = try JSONSerialization.jsonObject(with: encoder.encode(request))
        _ = try await self.request(["schemaVersion": 1, "verb": readOnly ? "mount-ro" : "mount-rw", "request": body])
    }

    static func stop(_ status: ManagedCardStatus, eject: Bool) async throws {
        _ = try await request(["schemaVersion": 1, "verb": eject ? "eject" : "unmount", "operationId": status.operationId])
    }

    private static func request(_ object: [String: Any]) async throws -> [String: Any] {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        guard data.count > 0, data.count <= maximumFrame else { throw Failure(message: "Service request exceeds its size limit.") }
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do { continuation.resume(returning: try exchange(data)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }

    private static func transfer(_ fd: Int32, buffer: UnsafeMutableRawPointer, count: Int, writing: Bool, deadline: Date) throws {
        var offset = 0
        while offset < count {
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { throw Failure(message: "The background service timed out. Check Activity before retrying.") }
            var item = pollfd(fd: fd, events: Int16(writing ? POLLOUT : POLLIN), revents: 0)
            let ready = poll(&item, 1, Int32(min(remaining * 1000, 35_000)))
            if ready < 0 && errno == EINTR { continue }
            guard ready > 0, item.revents & Int16(POLLERR | POLLNVAL) == 0 else { throw Failure(message: "Background service connection failed.") }
            let n = writing ? send(fd, buffer.advanced(by: offset), count - offset, 0) : recv(fd, buffer.advanced(by: offset), count - offset, 0)
            if n < 0 && (errno == EINTR || errno == EAGAIN) { continue }
            guard n > 0 else { throw Failure(message: "The background service closed its connection.") }
            offset += n
        }
    }

    private static func exchange(_ data: Data) throws -> [String: Any] {
        guard installed else { throw Failure(message: "Set up background access and open Nativol from Applications first.") }
        var info = stat()
        guard lstat(socketPath, &info) == 0, info.st_uid == 0, info.st_mode & S_IFMT == S_IFSOCK else {
            throw Failure(message: "The background service is not running. Open Setup to check its installation.")
        }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw Failure(message: "Cannot create a local service connection.") }
        defer { close(fd) }
        _ = fcntl(fd, F_SETFD, FD_CLOEXEC); _ = fcntl(fd, F_SETFL, O_NONBLOCK)
        var noSignal: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_un(); address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        let pathBytes = Array(socketPath.utf8) + [0]
        guard pathBytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw Failure(message: "Service socket path is too long.") }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: pathBytes) }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        if connected != 0 {
            guard errno == EINPROGRESS else { throw Failure(message: "Cannot connect to the background service.") }
            var item = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            var error: Int32 = 0; var size = socklen_t(MemoryLayout<Int32>.size)
            guard poll(&item, 1, 3000) > 0, getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) == 0, error == 0 else {
                throw Failure(message: "Background service connection timed out.")
            }
        }
        var uid: uid_t = 1, gid: gid_t = 1
        guard getpeereid(fd, &uid, &gid) == 0, uid == 0 else { throw Failure(message: "The service is not owned by macOS administrator setup.") }
        let deadline = Date().addingTimeInterval(35)
        var header = UInt32(data.count).bigEndian
        try withUnsafeMutableBytes(of: &header) { try transfer(fd, buffer: $0.baseAddress!, count: 4, writing: true, deadline: deadline) }
        var message = data
        try message.withUnsafeMutableBytes { try transfer(fd, buffer: $0.baseAddress!, count: data.count, writing: true, deadline: deadline) }
        try withUnsafeMutableBytes(of: &header) { try transfer(fd, buffer: $0.baseAddress!, count: 4, writing: false, deadline: deadline) }
        let length = Int(UInt32(bigEndian: header))
        guard length > 0, length <= maximumFrame else { throw Failure(message: "Service response exceeds its size limit.") }
        var responseData = Data(count: length)
        try responseData.withUnsafeMutableBytes { try transfer(fd, buffer: $0.baseAddress!, count: length, writing: false, deadline: deadline) }
        guard let response = try JSONSerialization.jsonObject(with: responseData) as? [String: Any],
              let ok = response["ok"] as? Bool else { throw Failure(message: "Invalid service response.") }
        guard ok else { throw Failure(message: response["error"] as? String ?? "Background request was refused.") }
        return response
    }
}
