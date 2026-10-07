import AppKit
import Combine
import SwiftUI
import NativolCore

@main
enum NativolMain {
    static func main() {
        let arguments = ProcessInfo.processInfo.arguments
        let serviceCommands: Set<String> = ["--background-status", "--background-mount", "--background-stop", "--background-eject", "--install-background"]
        if arguments.dropFirst().contains(where: serviceCommands.contains) {
            Task { @MainActor in
                do {
                    let result = try await runBackgroundCommand(arguments)
                    let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
                    FileHandle.standardOutput.write(data); print("")
                    exit(0)
                } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
            }
            dispatchMain()
        }
        if let index = arguments.firstIndex(of: "--print-card-request"), arguments.indices.contains(index + 1) {
            do {
                guard let volume = VolumeMonitor.snapshotExperimentalVolume(bsdName: arguments[index + 1], sessionID: UUID()) else {
                    throw PersonalBackend.Failure(message: "The external partition is unavailable.")
                }
                let lease = try ExperimentalWriteLease.enroll(volume: volume, bootSessionUUID: PersonalBackend.bootSession(),
                    requestedUID: getuid(), requestedGID: getgid(), isProtected: false,
                    statusIsFresh: true, identityIsUnique: true, userAuthorizedWriting: true)
                let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
                let data = try encoder.encode(lease.request)
                FileHandle.standardOutput.write(data); print("")
            } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
            return
        }
        if ProcessInfo.processInfo.arguments.contains("--print-install-command") {
            do { print(try PersonalBackend.installCommand()) } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
            return
        }
        if arguments.contains("--print-background-install-command") {
            do { print(try AppInstallation.installCommand() + PersonalBackend.installCommand() + "\n" + BackgroundService.installCommand()) }
            catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
            return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        // Stay unobtrusive until the launch event distinguishes a login item
        // from an explicit launch. showWindow() restores normal app behavior.
        app.setActivationPolicy(.accessory)
        app.run()
        withExtendedLifetime(delegate) {}
    }

    /// These diagnostics use the same authenticated service client as the UI.
    /// They never fall back to administrator authorization for card operations.
    @MainActor private static func runBackgroundCommand(_ arguments: [String]) async throws -> [String: Any] {
        guard arguments.count >= 2 else { throw PersonalBackend.Failure(message: "Missing background command.") }
        let command = arguments[1]
        let needsValue = ["--background-mount", "--background-stop", "--background-eject"].contains(command)
        guard arguments.count == (needsValue ? 3 : 2) else {
            throw PersonalBackend.Failure(message: "Use exactly one background command and its required argument.")
        }
        switch command {
        case "--background-status":
            let status = try await BackgroundService.status()
            return ["schemaVersion": 1, "kind": "background-status", "serviceStatus": status,
                "backgroundServiceInstalled": BackgroundService.installed,
                "launchAtLoginEnabled": LoginStartup.enabled,
                "launchAtLoginRequiresApproval": LoginStartup.requiresApproval,
                "automaticMountingEnabled": UserDefaults.standard.bool(forKey: AutomaticMountPolicy.enabledPreferenceKey),
                "appPath": Bundle.main.bundleURL.path,
                "appVersion": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development"]
        case "--install-background":
            let command = try AppInstallation.installCommand() + PersonalBackend.installCommand() + "\n" + BackgroundService.installCommand()
            try await PersonalBackend.authorize(command)
            return ["schemaVersion": 1, "kind": "background-installation", "status": "installed",
                "appPath": AppInstallation.installedURL.path]
        case "--background-mount":
            let bsdName = arguments[2]
            guard PersonalBackend.supportedHost,
                  bsdName.range(of: "^disk[0-9]+s[0-9]+$", options: .regularExpression) != nil,
                  let volume = VolumeMonitor.snapshotExperimentalVolume(bsdName: bsdName, sessionID: UUID()) else {
                throw PersonalBackend.Failure(message: "The external NTFS partition is unavailable on this host.")
            }
            guard !PersonalBackend.statuses().contains(where: { $0.isActive && UInt64($0.mediaRegistryID) == volume.mediaRegistryID }) else {
                throw PersonalBackend.Failure(message: "This physical drive already has an active session.")
            }
            let excludedUUIDs = Set(UserDefaults.standard.stringArray(forKey: "ExcludedLabVolumeUUIDs") ?? [])
            let exclusions = Set(UserDefaults.standard.stringArray(forKey: "ExcludedWritingIdentities") ?? [])
            guard volume.partitionUUID.map({ !excludedUUIDs.contains($0.uppercased()) }) ?? true,
                  AutomaticMountPolicy.exclusionKeys(for: volume).isDisjoint(with: exclusions) else {
                throw PersonalBackend.Failure(message: "This volume is kept read-only in Nativol.")
            }
            let lease = try ExperimentalWriteLease.enroll(volume: volume, bootSessionUUID: PersonalBackend.bootSession(),
                requestedUID: getuid(), requestedGID: getgid(), isProtected: false,
                statusIsFresh: true, identityIsUnique: true, userAuthorizedWriting: true)
            try await BackgroundService.mount(lease.request, readOnly: false)
            return ["schemaVersion": 1, "kind": "background-mount", "status": "request-accepted",
                "operationId": lease.request.operationId.uuidString]
        case "--background-stop", "--background-eject":
            guard let operation = UUID(uuidString: arguments[2]) else {
                throw PersonalBackend.Failure(message: "A valid current card operation UUID is required.")
            }
            let matching = PersonalBackend.statuses().filter { UUID(uuidString: $0.operationId) == operation && $0.isMounted }
            guard matching.count == 1 else {
                throw PersonalBackend.Failure(message: "The operation does not identify one current protected card mount.")
            }
            let eject = command == "--background-eject"
            try await BackgroundService.stop(matching[0], eject: eject)
            return ["schemaVersion": 1, "kind": eject ? "background-eject" : "background-stop",
                "status": "request-accepted", "operationId": operation.uuidString]
        default:
            throw PersonalBackend.Failure(message: "Unknown background command.")
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate {
    private var window: NSWindow!
    private var model: AppModel!
    private var statusItem: NSStatusItem!
    private var modelChanges: AnyCancellable?
    private var backgroundLaunch = false

    func applicationWillFinishLaunching(_ notification: Notification) {
        backgroundLaunch = LoginStartup.launchedInBackground(arguments: ProcessInfo.processInfo.arguments,
            event: NSAppleEventManager.shared().currentAppleEvent)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let arguments = ProcessInfo.processInfo.arguments
        model = AppModel(demo: arguments.contains("--demo"))
        if arguments.contains("--enable-automation"), Bundle.main.bundleURL.path == AppInstallation.installedURL.path {
            UserDefaults.standard.set(true, forKey: "BackgroundAccessConfigured")
            // The old setup flag cannot broaden one-card consent to every drive.
            model.setLaunchAtLogin(true)
        }
        backgroundLaunch = backgroundLaunch || LoginStartup.launchedInBackground(arguments: arguments,
            event: NSAppleEventManager.shared().currentAppleEvent)
        if arguments.contains("--setup") { model.section = .setup }
        if arguments.contains("--activity") { model.section = .activity }
        if arguments.contains("--lab") { model.section = .lab }
        if arguments.contains("--dark") { NSApp.appearance = NSAppearance(named: .darkAqua) }
        if arguments.contains("--light") { NSApp.appearance = NSAppearance(named: .aqua) }
        installMenus()
        modelChanges = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.updateStatusItem() }
        }
        let content = ContentView(model: model)
        let host = NSHostingView(rootView: content)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1040, height: 720),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Nativol"
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: 920, height: 650)
        window.contentView = host
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        if !arguments.contains("--snapshot") { window.setFrameAutosaveName("NativolMainWindow") }
        if !backgroundLaunch || arguments.contains("--snapshot") { showWindow() }
        updateStatusItem()

        if let index = arguments.firstIndex(of: "--snapshot"), arguments.indices.contains(index + 1) {
            let path = arguments[index + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                host.layoutSubtreeIfNeeded()
                guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { exit(2) }
                host.cacheDisplay(in: host.bounds, to: rep)
                guard let data = rep.representation(using: .png, properties: [:]) else { exit(3) }
                do { try data.write(to: URL(fileURLWithPath: path)) } catch { exit(4) }
                NSApp.terminate(nil)
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard window != nil else { return false }
        showWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func windowWillClose(_ notification: Notification) {
        // The status item and volume observer remain alive after the window closes.
        NSApp.setActivationPolicy(.accessory)
    }

    func applicationWillTerminate(_ notification: Notification) { model?.stop() }

    private func installMenus() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Nativol")
        appMenu.addItem(withTitle: "About Nativol", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Nativol", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        menu.addItem(editItem)
        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Show Nativol", action: #selector(showWindow), keyEquivalent: "0")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowItem.submenu = windowMenu
        menu.addItem(windowItem)
        NSApp.mainMenu = menu
        NSApp.windowsMenu = windowMenu

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "externaldrive", accessibilityDescription: "Nativol")
        let statusMenu = NSMenu()
        statusMenu.autoenablesItems = false
        statusMenu.delegate = self
        statusItem.menu = statusMenu
        for item in appMenu.items + windowMenu.items + statusMenu.items where item.action == #selector(showWindow) || item.action == #selector(about) || item.action == #selector(refresh) {
            item.target = self
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        guard menu === statusItem.menu else { return }
        menu.removeAllItems()
        let statuses = model.activeManagedCards
        let busy = model.backendBusy || model.backgroundSetupBusy
        func add(_ title: String, _ action: Selector?, enabled: Bool = true, state: NSControl.StateValue = .off) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            item.isEnabled = enabled
            item.state = state
            menu.addItem(item)
        }
        add(statusTitle, nil, enabled: false)
        if model.demoMode { add("Demo mode", nil, enabled: false) }
        menu.addItem(.separator())
        add("Open Nativol", #selector(showWindow))
        for status in statuses {
            let item = NSMenuItem(title: model.displayName(for: status), action: nil, keyEquivalent: "")
            let actions = NSMenu()
            actions.autoenablesItems = false
            func driveAction(_ title: String, _ action: Selector, enabled: Bool) {
                let command = NSMenuItem(title: title, action: action, keyEquivalent: "")
                command.target = self
                command.representedObject = status.operationId
                command.isEnabled = enabled
                actions.addItem(command)
            }
            let info = NSMenuItem(title: status.isMounted ? (status.isReadOnly ? "Read-only" : "Read & write") : status.state.capitalized,
                                  action: nil, keyEquivalent: "")
            info.isEnabled = false
            actions.addItem(info)
            driveAction("Open in Finder", #selector(openManagedVolume(_:)), enabled: status.isMounted)
            driveAction("Stop Writing", #selector(stopManagedVolume(_:)), enabled: status.isMounted && !status.isReadOnly && !busy)
            driveAction("Safely Eject", #selector(ejectManagedVolume(_:)), enabled: status.isMounted && !busy)
            item.submenu = actions
            menu.addItem(item)
        }
        add("Refresh Volumes", #selector(refresh), enabled: !busy)
        menu.addItem(.separator())
        add("Automatically Enable Writing", #selector(toggleAutomaticMounting), enabled: !model.demoMode && !busy,
            state: model.automaticMountingEnabled ? .on : .off)
        add("Launch at Login", #selector(toggleLaunchAtLogin), enabled: LoginStartup.supported && !model.demoMode && !busy,
            state: model.launchAtLoginEnabled ? .on : .off)
        if LoginStartup.requiresApproval {
            add("Allow Nativol in Login Items…", #selector(openLoginSettings))
        }
        if !model.backgroundServiceInstalled {
            add("Set Up Background Access…", #selector(setupBackgroundAccess), enabled: !model.demoMode && !busy)
        }
        if model.alertMessage != nil { add("View Required Action…", #selector(showWindow)) }
        menu.addItem(.separator())
        if statuses.contains(where: { $0.isMounted }) { add("Quitting keeps managed volumes mounted", nil, enabled: false) }
        let quit = NSMenuItem(title: "Quit Nativol", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        quit.target = NSApp
        menu.addItem(quit)
    }

    private var statusTitle: String {
        let active = model.activeManagedCards
        if active.contains(where: { $0.state == "attention" }) { return "A volume needs attention" }
        let mounted = active.filter { $0.isMounted }.count
        if mounted > 0 { return "\(mounted) NTFS volume\(mounted == 1 ? "" : "s") mounted" }
        if !active.isEmpty { return "Preparing NTFS access…" }
        if model.automaticMountingEnabled && !model.backgroundServiceReady {
            return model.backgroundServiceInstalled ? "Background access needs attention" : "Set up background access"
        }
        return model.automaticMountingEnabled ? "Waiting for external NTFS volumes" : "Automatic writing is off"
    }

    private func updateStatusItem() {
        guard statusItem != nil, model != nil else { return }
        let symbol = model.activeManagedCards.contains(where: { $0.state == "attention" }) ? "externaldrive.badge.exclamationmark" : "externaldrive"
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Nativol — " + statusTitle)
            ?? NSImage(systemSymbolName: "externaldrive", accessibilityDescription: "Nativol")
        image?.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = "Nativol — " + statusTitle
    }

    @objc private func showWindow() {
        guard window != nil else { return }
        NSApp.setActivationPolicy(.regular)
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc private func refresh() { model.refresh() }
    private func selectedStatus(_ item: NSMenuItem) -> ManagedCardStatus? {
        guard let id = item.representedObject as? String else { return nil }
        return model.activeManagedCards.first { $0.operationId == id && $0.isMounted }
    }
    @objc private func openManagedVolume(_ item: NSMenuItem) {
        guard let status = selectedStatus(item) else { return }
        model.openManagedCard(status)
    }
    @objc private func stopManagedVolume(_ item: NSMenuItem) {
        guard let status = selectedStatus(item), !status.isReadOnly else { return }
        model.stopCard(status, eject: false)
    }
    @objc private func ejectManagedVolume(_ item: NSMenuItem) {
        guard let status = selectedStatus(item) else { return }
        model.stopCard(status, eject: true)
    }
    @objc private func toggleAutomaticMounting() { model.setAutomaticMounting(!model.automaticMountingEnabled) }
    @objc private func toggleLaunchAtLogin() { model.setLaunchAtLogin(!model.launchAtLoginEnabled) }
    @objc private func openLoginSettings() { LoginStartup.openSettings() }
    @objc private func setupBackgroundAccess() { model.setupBackgroundAccess() }
    @objc private func about() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development"
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Nativol",
            .applicationVersion: version + " — Intel testing preview",
            .credits: NSAttributedString(string: "A native home for your volumes.\nExternal NTFS writing for Intel macOS 15.7.9.\nBuilt with NTFS-3G and macFUSE.")
        ])
    }
}
