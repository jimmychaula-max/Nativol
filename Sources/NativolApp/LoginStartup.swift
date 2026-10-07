import AppKit
import CoreServices
import ServiceManagement

/// Login registration belongs to the current user's signed application bundle.
/// It is independent of the privileged filesystem helper and its permissions.
@MainActor
enum LoginStartup {
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static var supported: Bool {
        if #available(macOS 13.0, *) {
            return Bundle.main.bundleURL.path == "/Applications/Nativol.app"
                && Bundle.main.bundleIdentifier != nil
        }
        return false
    }

    static var enabled: Bool {
        guard supported else { return false }
        if #available(macOS 13.0, *) { return SMAppService.mainApp.status == .enabled }
        return false
    }

    static var requiresApproval: Bool {
        guard supported else { return false }
        if #available(macOS 13.0, *) { return SMAppService.mainApp.status == .requiresApproval }
        return false
    }

    static func setEnabled(_ enabled: Bool) throws {
        guard supported else {
            throw Failure(message: "Launch at Login requires the installed Nativol app on macOS 13 or later.")
        }
        if #available(macOS 13.0, *) {
            let service = SMAppService.mainApp
            if enabled {
                if service.status == .enabled { return }
                if service.status == .requiresApproval {
                    throw Failure(message: "Allow Nativol in System Settings → General → Login Items before enabling Launch at Login.")
                }
                try service.register()
                guard service.status == .enabled else {
                    throw Failure(message: "macOS has not enabled Launch at Login. Check Nativol in System Settings → General → Login Items.")
                }
            } else if service.status != .notRegistered {
                try service.unregister()
            }
        }
    }

    static func openSettings() {
        if #available(macOS 13.0, *) { SMAppService.openSystemSettingsLoginItems() }
    }

    static func launchedInBackground(arguments: [String], event: NSAppleEventDescriptor?) -> Bool {
        if arguments.contains("--background") { return true }
        guard let event, event.eventID == kAEOpenApplication else { return false }
        return event.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
            || event.paramDescriptor(forKeyword: keyAELaunchedAsLogInItem) != nil
    }
}
