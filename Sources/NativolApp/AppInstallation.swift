import AppKit
import Foundation

enum AppInstallation {
    static let installedURL = URL(fileURLWithPath: "/Applications/Nativol.app", isDirectory: true)

    /// The administrator approves the exact bundle currently being run. Stage
    /// and verify a complete copy before publishing it at the login item's path.
    static func installCommand() throws -> String {
        let source = Bundle.main.bundleURL
        guard source.pathExtension == "app", Bundle.main.bundleIdentifier == "app.nativol.preview",
              let enumeration = FileManager.default.enumerator(at: source,
                includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey]) else {
            throw PersonalBackend.Failure(message: "Open the complete Nativol application to install background access.")
        }
        var files: [(String, String)] = []
        for case let item as URL in enumeration {
            let facts = try item.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey])
            guard facts.isSymbolicLink != true, facts.isRegularFile == true || facts.isDirectory == true else {
                throw PersonalBackend.Failure(message: "The application contains an unsupported file type.")
            }
            guard facts.isRegularFile == true else { continue }
            let relative = String(item.path.dropFirst(source.path.count + 1))
            guard !relative.isEmpty, !relative.contains("\n"), !relative.contains("\r") else {
                throw PersonalBackend.Failure(message: "Invalid application package path.")
            }
            files.append((relative, PersonalBackend.hash(try Data(contentsOf: item))))
        }
        guard !files.isEmpty else { throw PersonalBackend.Failure(message: "The application package is empty.") }
        let quote = PersonalBackend.quote
        let backup = PersonalBackend.base + "/AppBackups/" + UUID().uuidString + ".app"
        var script = """
        set -eu
        umask 077
        if /usr/bin/pgrep -x NativolHelper >/dev/null || /usr/bin/pgrep -x ntfs-3g >/dev/null; then
          echo 'Finish the active NTFS session before updating background access.' >&2
          exit 1
        fi
        if /bin/launchctl print system/org.nativol.service >/dev/null 2>&1; then
          for service_parent in / /Library '/Library/Application Support' '/Library/Application Support/Nativol' '/Library/Application Support/Nativol/Service'; do
            test -d "$service_parent" && test ! -L "$service_parent"
            test "$(/usr/bin/stat -f %u "$service_parent")" = 0
            service_parent_mode="$(/usr/bin/stat -f %Lp "$service_parent")"
            test "$((0$service_parent_mode & 022))" = 0
          done
          service_executable='/Library/Application Support/Nativol/Service/NativolService'
          test -f "$service_executable" && test ! -L "$service_executable"
          test "$(/usr/bin/stat -f %u "$service_executable")" = 0
          service_file_mode="$(/usr/bin/stat -f %Lp "$service_executable")"
          test "$((0$service_file_mode & 022))" = 0
          /usr/bin/codesign --verify --strict '/Library/Application Support/Nativol/Service/NativolService'
          '/Library/Application Support/Nativol/Service/NativolService' --check-idle
          /bin/launchctl bootout system/org.nativol.service
          if /usr/bin/pgrep -x NativolHelper >/dev/null || /usr/bin/pgrep -x ntfs-3g >/dev/null; then
            echo 'An NTFS session started during setup. Finish it before retrying.' >&2
            exit 1
          fi
        fi
        test -d /Applications && test ! -L /Applications
        test "$(/usr/bin/stat -f %u /Applications)" = 0
        test "$(/usr/bin/stat -f %g /Applications)" = 80
        app_parent_mode="$(/usr/bin/stat -f %Lp /Applications)"
        test "$((0$app_parent_mode & 002))" = 0
        for app_install_dir in '/Library' '/Library/Application Support'; do
          test -d "$app_install_dir" && test ! -L "$app_install_dir"
          test "$(/usr/bin/stat -f %u "$app_install_dir")" = 0
          app_dir_mode="$(/usr/bin/stat -f %Lp "$app_install_dir")"
          test "$((0$app_dir_mode & 022))" = 0
        done
        for app_install_dir in \(quote(PersonalBackend.base)) \(quote(PersonalBackend.base + "/AppBackups")); do
          if test ! -e "$app_install_dir" && test ! -L "$app_install_dir"; then /bin/mkdir -m 0755 "$app_install_dir"; fi
          test -d "$app_install_dir" && test ! -L "$app_install_dir"
          test "$(/usr/bin/stat -f %u "$app_install_dir")" = 0
          app_dir_mode="$(/usr/bin/stat -f %Lp "$app_install_dir")"
          test "$((0$app_dir_mode & 022))" = 0
        done
        app_stage=$(/usr/bin/mktemp -d \(quote(PersonalBackend.base + "/.app-install.XXXXXXXX")))
        trap '/bin/rm -rf "$app_stage"' EXIT
        /usr/bin/ditto \(quote(source.path)) "$app_stage/Nativol.app"
        test -z "$(/usr/bin/find "$app_stage/Nativol.app" ! -type f ! -type d -print -quit)"
        test "$(/usr/bin/find "$app_stage/Nativol.app" -type f | /usr/bin/wc -l | /usr/bin/tr -d ' ')" = \(files.count)
        """
        for (relative, digest) in files.sorted(by: { $0.0 < $1.0 }) {
            script += "\ntest \"$(/usr/bin/shasum -a 256 \"$app_stage/Nativol.app/\"\(quote(relative)) | /usr/bin/awk '{print $1}')\" = \(quote(digest))"
        }
        script += """

        /usr/bin/codesign --verify --deep --strict "$app_stage/Nativol.app"
        /usr/sbin/chown -R root:wheel "$app_stage/Nativol.app"
        /bin/chmod -RN "$app_stage/Nativol.app"
        /bin/chmod -R u=rwX,go=rX "$app_stage/Nativol.app"
        if test -e /Applications/Nativol.app || test -L /Applications/Nativol.app; then
          test -d /Applications/Nativol.app && test ! -L /Applications/Nativol.app
          test "$(/usr/bin/stat -f %u /Applications/Nativol.app)" = 0
          test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' /Applications/Nativol.app/Contents/Info.plist)" = app.nativol.preview
          /bin/mv /Applications/Nativol.app \(quote(backup))
        fi
        /bin/mv "$app_stage/Nativol.app" /Applications/Nativol.app
        /bin/rmdir "$app_stage"
        trap - EXIT

        """
        return script
    }

    @MainActor static func reopenInstalled() {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = ["--setup", "--enable-automation"]
        configuration.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: installedURL, configuration: configuration) { _, error in
            if error == nil { DispatchQueue.main.async { NSApp.terminate(nil) } }
        }
    }
}
