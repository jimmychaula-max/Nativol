import AppKit
import Combine
import NativolCore

enum AppSection: String, CaseIterable {
    case drives = "Volumes", lab = "Write Access", setup = "Setup", activity = "Activity"
    var symbol: String {
        switch self {
        case .drives: return "externaldrive"
        case .lab: return "flask"
        case .setup: return "slider.horizontal.3"
        case .activity: return "clock.arrow.circlepath"
        }
    }
}

struct ActivityEntry: Identifiable {
    let id = UUID()
    let date = Date()
    let message: String
}

struct EnvironmentReport {
    let osVersion: String
    let architecture: String
    let macFUSEPresent: Bool
    let ntfsEnginePresent: Bool
    let competingAppPresent: Bool

    static func inspect() -> EnvironmentReport {
        let files = FileManager.default
        #if arch(arm64)
        let architecture = "arm64 (native Apple Silicon)"
        #else
        let architecture = "x86_64 (Intel build)"
        #endif
        let os = ProcessInfo.processInfo.operatingSystemVersion
        return EnvironmentReport(
            osVersion: "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
            architecture: architecture,
            macFUSEPresent: files.fileExists(atPath: "/Library/Filesystems/macfuse.fs"),
            ntfsEnginePresent: ["/usr/local/bin/ntfs-3g", "/opt/homebrew/bin/ntfs-3g", "/usr/local/sbin/ntfs-3g", "/opt/homebrew/sbin/ntfs-3g"].contains { files.fileExists(atPath: $0) },
            competingAppPresent: ["/Applications/NTFS for Mac.app", "/Applications/Microsoft NTFS for Mac by Tuxera.app"].contains { files.fileExists(atPath: $0) }
        )
    }

    var diagnosticText: String {
        """
        Nativol 0.5.0-beta.1 — Intel testing preview
        macOS: \(osVersion)
        Executable architecture: \(architecture)
        macFUSE bundle found: \(macFUSEPresent)
        NTFS-3G found at known locations: \(ntfsEnginePresent)
        Other NTFS application found at known locations: \(competingAppPresent)
        Presence checks do not establish that a driver is loaded or compatible.
        Certified writable configurations: 0
        Writable mounting: external NTFS beta, native Intel macOS 15.7.9 only
        No volume names, paths, device identifiers, or file contents included.
        """
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var section: AppSection = .drives
    @Published var selectedID: String?
    @Published var demoMode: Bool
    @Published private(set) var volumes: [Volume] = []
    @Published private(set) var environment = EnvironmentReport.inspect()
    @Published private(set) var activity: [ActivityEntry] = []
    @Published private(set) var discoveryError: String?
    @Published var alertMessage: String?
    @Published var labVolumeID: String? {
        didSet {
            if oldValue != labVolumeID { labConfirmedDisposable = false; labSelection = nil }
        }
    }
    @Published var labConfirmedDisposable = false {
        didSet {
            if !labConfirmedDisposable { labSelection = nil }
        }
    }
    @Published private(set) var labSelection: LabSelection?
    @Published private(set) var imageLabReport: ImageLabReport?
    @Published private(set) var imageLabReportError: String?
    @Published private(set) var excludedLabSessions: Set<UUID> = []
    @Published private(set) var excludedLabUUIDs: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "ExcludedLabVolumeUUIDs") ?? [])
    @Published private(set) var excludedWritingIdentities: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "ExcludedWritingIdentities") ?? [])
    @Published private(set) var managedCards: [ManagedCardStatus] = []
    @Published private(set) var backendBusy = false
    @Published private(set) var backendInstalled = PersonalBackend.installed
    @Published private(set) var automaticMountingEnabled = UserDefaults.standard.bool(forKey: AutomaticMountPolicy.enabledPreferenceKey)
    @Published private(set) var launchAtLoginEnabled = LoginStartup.enabled
    @Published private(set) var backgroundServiceInstalled = BackgroundService.installed
    @Published private(set) var backgroundSetupBusy = false
    @Published private(set) var backgroundServiceReady = false
    @Published private(set) var backgroundServiceMessage = "Checking background access…"
    private let monitor = VolumeMonitor()
    private var subscriptions = Set<AnyCancellable>()
    private var pendingOperationID: String?
    private var automaticPolicy: AutomaticMountPolicy?
    private var backgroundCheckBusy = false
    private var lastBackgroundCheck = Date.distantPast

    init(demo: Bool = false) {
        demoMode = demo
        if let boot = try? PersonalBackend.bootSession() {
            let saved = UserDefaults.standard.data(forKey: "AutomaticMountSuppression")
            let previous = saved.flatMap { try? JSONDecoder().decode(AutomaticMountPolicy.self, from: $0) }
            if saved != nil && previous == nil {
                automaticMountingEnabled = false
                UserDefaults.standard.set(false, forKey: AutomaticMountPolicy.enabledPreferenceKey)
                backgroundServiceMessage = "Automatic writing paused because its saved session state could not be read. Enable it again in Setup."
            }
            automaticPolicy = AutomaticMountPolicy(bootSessionUUID: boot, previous: previous)
        }
        loadImageLabReport()
        monitor.$volumes.sink { [weak self] volumes in
            guard let self = self, !self.demoMode else { return }
            self.accept(volumes)
        }.store(in: &subscriptions)
        monitor.$errorMessage.sink { [weak self] error in
            self?.discoveryError = error
        }.store(in: &subscriptions)
        Timer.publish(every: 1, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            self?.refreshBackend()
            self?.refreshBackgroundAccess()
            self?.attemptAutomaticMount()
        }.store(in: &subscriptions)
        refreshBackend()
        if demo {
            accept(Volume.demoVolumes)
            record("Sample volumes loaded. Device actions are unavailable in demo mode.")
        } else {
            monitor.start()
            record("Nativol started. Observing external volumes.")
        }
    }

    var selectedVolume: Volume? { volumes.first { $0.id == selectedID } }
    var ntfsCount: Int { volumes.filter(\.isNTFS).count }

    func refresh() {
        clearLabSelection()
        environment = .inspect()
        loadImageLabReport()
        backendInstalled = PersonalBackend.installed
        backgroundServiceInstalled = BackgroundService.installed
        refreshBackgroundAccess(force: true)
        refreshBackend()
        if !demoMode { monitor.refresh() }
        record("Refreshed volume and setup information.")
    }

    func setDemo(_ enabled: Bool) {
        guard demoMode != enabled else { return }
        demoMode = enabled
        clearLabSelection()
        if enabled {
            monitor.stop()
            discoveryError = nil
            accept(Volume.demoVolumes)
            record("Entered demo mode. All displayed volumes are samples.")
        } else {
            accept([])
            monitor.start()
            record("Returned to connected volumes.")
        }
    }

    func openVolume(_ volume: Volume) {
        if !demoMode, let status = managedStatus(for: volume), status.isMounted {
            NSWorkspace.shared.open(URL(fileURLWithPath: status.mountPath, isDirectory: true))
            record("Opened the Nativol volume in Finder.")
            return
        }
        guard !demoMode,
              let current = monitor.volumes.first(where: { $0.id == volume.id && $0.sessionID == volume.sessionID }),
              let path = current.mountPath else {
            alertMessage = "This volume is no longer mounted. Refresh the volume list and try again."
            return
        }
        let url = URL(fileURLWithPath: path, isDirectory: true)
        guard FileManager.default.fileExists(atPath: url.path), NSWorkspace.shared.open(url) else {
            alertMessage = "Finder could not open the volume. It may have been disconnected."
            return
        }
        record("Opened a mounted volume in Finder.")
    }

    func openManagedCard(_ status: ManagedCardStatus) {
        guard !demoMode, status.isMounted else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: status.mountPath, isDirectory: true))
    }

    func setAutomaticMounting(_ enabled: Bool) {
        guard !demoMode else { return }
        automaticMountingEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: AutomaticMountPolicy.enabledPreferenceKey)
        record(enabled ? "Automatic writing enabled for eligible external NTFS volumes when background access is ready."
                       : "Automatic writing paused. The current mount stays as it is.")
        if enabled { refreshBackgroundAccess(force: true); attemptAutomaticMount() }
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        guard !demoMode else { return }
        do {
            try LoginStartup.setEnabled(enabled)
            launchAtLoginEnabled = LoginStartup.enabled
            record(launchAtLoginEnabled ? "Nativol will start in the menu bar after sign-in."
                   : (LoginStartup.requiresApproval ? "Approve Nativol in Login Items to start it after sign-in."
                      : "Launch at login is disabled."))
        } catch { alertMessage = error.localizedDescription }
    }

    func setupBackgroundAccess() {
        guard !demoMode, !backendBusy, !backgroundSetupBusy, activeManagedCard == nil,
              PersonalBackend.supportedHost else { return }
        backgroundSetupBusy = true
        backendBusy = true
        Task {
            defer { backgroundSetupBusy = false; backendBusy = false }
            do {
                let command = try AppInstallation.installCommand() + PersonalBackend.installCommand() + "\n" + BackgroundService.installCommand()
                try await PersonalBackend.authorize(command)
                UserDefaults.standard.set(true, forKey: "BackgroundAccessConfigured")
                backendInstalled = PersonalBackend.installed
                backgroundServiceInstalled = BackgroundService.installed
                record("Background access installed. Ordinary mount, stop and eject requests use the protected service.")
                AppInstallation.reopenInstalled()
            } catch { alertMessage = error.localizedDescription; record("Background setup did not complete.") }
        }
    }

    private func refreshBackgroundAccess(force: Bool = false) {
        guard !demoMode, !backgroundSetupBusy, !backgroundCheckBusy,
              force || Date().timeIntervalSince(lastBackgroundCheck) >= 10 else { return }
        lastBackgroundCheck = Date()
        launchAtLoginEnabled = LoginStartup.enabled
        backgroundServiceInstalled = BackgroundService.installed
        guard backgroundServiceInstalled else {
            backgroundServiceReady = false
            backgroundServiceMessage = "Set up background access once to mount without repeated passwords."
            return
        }
        backgroundCheckBusy = true
        Task {
            defer { backgroundCheckBusy = false }
            do {
                _ = try await BackgroundService.status()
                backgroundServiceReady = true
                backgroundServiceMessage = "Ready. Mount, stop and eject without another password."
            } catch {
                backgroundServiceReady = false
                backgroundServiceMessage = error.localizedDescription
            }
        }
    }

    private func suppressAutomaticMount(partition: UInt64?, media: UInt64?) {
        guard let key = AutomaticMountPolicy.attachmentKey(partition: partition, media: media),
              var policy = automaticPolicy else { return }
        policy.suppress(key)
        automaticPolicy = policy
        if let data = try? JSONEncoder().encode(policy) {
            UserDefaults.standard.set(data, forKey: "AutomaticMountSuppression")
        }
    }

    private func attemptAutomaticMount() {
        guard automaticMountingEnabled, backgroundServiceReady, backendInstalled, !demoMode,
              !backendBusy, !backgroundSetupBusy, pendingOperationID == nil, discoveryError == nil,
              let policy = automaticPolicy, activeManagedCards.count < 8 else { return }
        let active = activeManagedCards
        let blockedMedia = Set(active.compactMap { UInt64($0.mediaRegistryID) }.filter { $0 > 0 })
        guard blockedMedia.count == active.count else { return }
        let eligible = volumes.filter { canTestCard($0) }
        guard let volume = policy.nextCandidate(from: eligible, blockedMediaIDs: blockedMedia),
              let key = AutomaticMountPolicy.attachmentKey(partition: volume.partitionRegistryID, media: volume.mediaRegistryID),
              policy.mayAttempt(key), let boot = try? PersonalBackend.bootSession(),
              let lease = try? ExperimentalWriteLease.enroll(volume: volume, bootSessionUUID: boot,
                requestedUID: getuid(), requestedGID: getgid(), isProtected: isExcludedFromLab(volume),
                statusIsFresh: discoveryError == nil, identityIsUnique: true,
                userAuthorizedWriting: true) else { return }
        suppressAutomaticMount(partition: volume.partitionRegistryID, media: volume.mediaRegistryID)
        backendBusy = true
        pendingOperationID = lease.request.operationId.uuidString
        record("Checking an external NTFS volume for automatic writing.")
        Task {
            defer { backendBusy = false; refreshBackend() }
            do { try await BackgroundService.mount(lease.request, readOnly: false) }
            catch {
                pendingOperationID = nil
                backgroundServiceMessage = displayBackendMessage(error.localizedDescription)
                if error.localizedDescription.contains("Full Disk Access") {
                    alertMessage = displayBackendMessage(error.localizedDescription)
                    section = .setup
                }
                record("Automatic writing stopped: \(error.localizedDescription) Reconnect the drive or use Enable Writing to retry.")
            }
        }
    }

    func openDiskUtility() {
        guard !demoMode else { return }
        let url = URL(fileURLWithPath: "/System/Applications/Utilities/Disk Utility.app")
        NSWorkspace.shared.openApplication(at: url, configuration: .init()) { [weak self] _, error in
            if error != nil {
                DispatchQueue.main.async { self?.alertMessage = "Disk Utility could not be opened. Open it from Applications → Utilities." }
            }
        }
        record("Opened Disk Utility. No disk operation requested by Nativol.")
    }

    func exportDiagnostics() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "Nativol-diagnostics.txt"
        panel.title = "Export diagnostics"
        panel.message = "Includes app and dependency status. Volume names, paths, identifiers, and file contents are excluded."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try environment.diagnosticText.write(to: url, atomically: true, encoding: .utf8)
            record("Exported a diagnostic report without volume information.")
        } catch {
            alertMessage = "The report could not be saved. Choose another location."
        }
    }

    func clearActivity() { activity.removeAll() }
    func stop() { monitor.stop() }

    var labVolume: Volume? { volumes.first { $0.id == labVolumeID } }

    var labPreflight: TestPreflight {
        let volume = labVolume
        let canonical = volume.map(TestVolumeIdentity.init)
        let uuidIsUnique = canonical?.partitionUUID.map { uuid in
            volumes.filter { TestVolumeIdentity(volume: $0).partitionUUID == uuid }.count == 1
        }
        let registryIsUnique = volume?.partitionRegistryID.flatMap { entryID -> Bool? in
            guard entryID > 0 else { return nil }
            return volumes.filter { $0.partitionRegistryID == entryID }.count == 1
        }
        // Every available identity source must be unambiguous; absence alone is not proof.
        let uniquenessChecks = [uuidIsUnique, registryIsUnique].compactMap { $0 }
        return TestPreflight.evaluate(
            volume: volume, selection: labSelection, isDemo: demoMode,
            isProtected: volume.map(isExcludedFromLab) ?? false,
            statusIsFresh: discoveryError == nil && !demoMode && monitor.volumes == volumes,
            identityIsUnique: !uniquenessChecks.isEmpty && uniquenessChecks.allSatisfy { $0 }
        )
    }

    func inspectLabCard() {
        guard !demoMode, let volume = labVolume, labConfirmedDisposable else { return }
        labSelection = LabSelection(volume: volume, userConfirmedDisposable: true)
        let preflight = labPreflight
        record(preflight.readyForReadOnlyInspection
               ? "Test-card connection verified for read-only inspection. No disk operation performed."
               : "Test-card check found \(preflight.blockers.count) unmet prerequisites. No disk operation performed.")
    }

    func clearLabSelection() {
        labSelection = nil
        labConfirmedDisposable = false
    }

    func isExcludedFromLab(_ volume: Volume) -> Bool {
        excludedLabSessions.contains(volume.sessionID)
            || volume.partitionUUID.map { excludedLabUUIDs.contains($0.uppercased()) } == true
            || !AutomaticMountPolicy.exclusionKeys(for: volume).isDisjoint(with: excludedWritingIdentities)
    }

    func excludeFromLab(_ volume: Volume) {
        guard !demoMode else { return }
        excludedLabSessions.insert(volume.sessionID)
        if let uuid = volume.partitionUUID {
            excludedLabUUIDs.insert(uuid.uppercased())
            UserDefaults.standard.set(Array(excludedLabUUIDs), forKey: "ExcludedLabVolumeUUIDs")
        }
        let keys = AutomaticMountPolicy.exclusionKeys(for: volume)
        if !keys.isEmpty {
            excludedWritingIdentities.formUnion(keys)
            UserDefaults.standard.set(Array(excludedWritingIdentities), forKey: "ExcludedWritingIdentities")
        } else {
            setAutomaticMounting(false)
            alertMessage = "Automatic writing is paused. This volume lacks enough identity information to remember its read-only preference after reconnecting."
        }
        clearLabSelection()
        record("Kept a volume read-only for future writing requests. Current mounts are unchanged.")
    }

    func allowWritingAgain(_ volume: Volume) {
        guard !demoMode else { return }
        excludedLabSessions.remove(volume.sessionID)
        if let uuid = volume.partitionUUID { excludedLabUUIDs.remove(uuid.uppercased()) }
        excludedWritingIdentities.subtract(AutomaticMountPolicy.exclusionKeys(for: volume))
        UserDefaults.standard.set(Array(excludedLabUUIDs), forKey: "ExcludedLabVolumeUUIDs")
        UserDefaults.standard.set(Array(excludedWritingIdentities), forKey: "ExcludedWritingIdentities")
        record("Removed matching read-only preferences. Eligible automatic writing may resume.")
    }

    func loadImageLabReport() {
        let result = ImageLabReport.load()
        imageLabReport = result.report
        imageLabReportError = result.error
    }

    func managedStatus(for volume: Volume) -> ManagedCardStatus? {
        guard !demoMode else { return nil }
        return managedCards.first { $0.matches(volume) && $0.isActive }
    }

    var activeManagedCards: [ManagedCardStatus] { demoMode ? [] : managedCards.filter { $0.isActive } }
    var activeManagedCard: ManagedCardStatus? { activeManagedCards.first }

    func displayName(for status: ManagedCardStatus) -> String {
        volumes.first(where: { status.matches($0) })?.name ?? "NTFS volume · " + String(status.operationId.prefix(8))
    }

    private func identityIsUnique(_ volume: Volume) -> Bool {
        guard let partition = volume.partitionRegistryID, let media = volume.mediaRegistryID else { return false }
        return volumes.filter { $0.partitionRegistryID == partition }.count == 1
            && volumes.filter { $0.mediaRegistryID == media }.count == 1
            && volumes.filter { $0.bsdName == volume.bsdName }.count == 1
            && (TestVolumeIdentity(volume: volume).partitionUUID.map { uuid in
                volumes.filter { TestVolumeIdentity(volume: $0).partitionUUID == uuid }.count == 1
            } ?? true)
    }


    func canTestCard(_ volume: Volume) -> Bool {
        guard !demoMode, !isExcludedFromLab(volume), PersonalBackend.supportedHost,
              !activeManagedCards.contains(where: { UInt64($0.mediaRegistryID) == volume.mediaRegistryID }),
              let boot = try? PersonalBackend.bootSession() else { return false }
        return (try? ExperimentalWriteLease.enroll(volume: volume, bootSessionUUID: boot,
            requestedUID: getuid(), requestedGID: getgid(), isProtected: false,
            statusIsFresh: discoveryError == nil, identityIsUnique: identityIsUnique(volume),
            userAuthorizedWriting: true)) != nil
    }

    func showWriteSetup(_ volume: Volume) {
        labVolumeID = volume.id
        section = .lab
    }

    func installPersonalBackend() {
        guard !demoMode, !backendBusy, pendingOperationID == nil, activeManagedCards.isEmpty, PersonalBackend.supportedHost else { return }
        backendBusy = true
        record("Installing the pinned personal backend through macOS administrator authorization.")
        Task {
            defer { backendBusy = false }
            do { try await PersonalBackend.install(); backendInstalled = PersonalBackend.installed; record("Personal backend installed in a protected system directory.") }
            catch { alertMessage = error.localizedDescription; record("Backend installation did not complete.") }
        }
    }

    func openFullDiskAccess() {
        guard !demoMode, let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") else { return }
        NSWorkspace.shared.open(url)
    }

    private var backgroundPrivacyAccess: Bool {
        backgroundServiceInstalled || UserDefaults.standard.bool(forKey: "BackgroundAccessConfigured")
    }

    var privacyComponentName: String { backgroundPrivacyAccess ? "NativolService" : "NativolHelper" }

    var privacyComponentPath: String? {
        if backgroundPrivacyAccess { return PersonalBackend.base + "/Service/NativolService" }
        guard let (_, version, _) = try? PersonalBackend.payload() else { return nil }
        return PersonalBackend.base + "/Versions/" + version + "/bin/NativolHelper"
    }

    var privacyComponentAvailable: Bool {
        privacyComponentPath.map { PersonalBackend.isProtected($0, directory: false) } ?? false
    }

    func displayBackendMessage(_ message: String) -> String {
        guard backgroundPrivacyAccess, message.contains("Full Disk Access") else { return message }
        // macOS attributes device access to the process responsible for launching
        // the helper. The background service owns that responsibility in this mode.
        return message.replacingOccurrences(of: "NativolHelper", with: "NativolService")
    }

    func revealPersonalHelper() {
        guard !demoMode, let path = privacyComponentPath,
              PersonalBackend.isProtected(path, directory: false) else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func enableCardWriting() {
        guard !demoMode, !backendBusy, pendingOperationID == nil, backendInstalled, let volume = labVolume,
              canTestCard(volume), activeManagedCards.count < 8 else { return }
        do {
            // Build a fresh attachment lease immediately before the OS prompt.
            let lease = try ExperimentalWriteLease.enroll(volume: volume,
                bootSessionUUID: PersonalBackend.bootSession(), requestedUID: getuid(), requestedGID: getgid(),
                isProtected: isExcludedFromLab(volume), statusIsFresh: discoveryError == nil,
                identityIsUnique: identityIsUnique(volume),
                userAuthorizedWriting: true)
            backendBusy = true
            suppressAutomaticMount(partition: volume.partitionRegistryID, media: volume.mediaRegistryID)
            pendingOperationID = lease.request.operationId.uuidString
            record("Requested writing for the selected NTFS volume. The protected helper will recheck its identity and health.")
            Task {
                defer { backendBusy = false; refreshBackend() }
                do { try await PersonalBackend.mount(lease.request); section = .drives }
                catch { pendingOperationID = nil; alertMessage = displayBackendMessage(error.localizedDescription); record("The helper did not enable writing.") }
            }
        } catch { alertMessage = error.localizedDescription }
    }

    func stopCard(_ status: ManagedCardStatus, eject: Bool) {
        guard !demoMode, !backendBusy, status.isMounted else { return }
        suppressAutomaticMount(partition: UInt64(status.partitionRegistryID), media: UInt64(status.mediaRegistryID))
        backendBusy = true
        Task {
            defer { backendBusy = false; refreshBackend() }
            do { try await PersonalBackend.stop(status, eject: eject); record(eject ? "Requested a clean unmount and eject." : "Requested a clean end to writing.") }
            catch { alertMessage = displayBackendMessage(error.localizedDescription) }
        }
    }

    private func refreshBackend() {
        let updated = PersonalBackend.statuses()
        for item in updated where managedCards.first(where: { $0.operationId == item.operationId })?.message != item.message {
            record(item.message)
        }
        managedCards = updated
        if let pendingOperationID, let status = updated.first(where: { $0.operationId == pendingOperationID }) {
            if ["failed", "attention"].contains(status.state) {
                self.pendingOperationID = nil
                alertMessage = displayBackendMessage(status.message)
                if status.message.contains("Full Disk Access") { section = .setup }
            } else if status.isMounted || !status.isActive { self.pendingOperationID = nil }
        }
    }

    private func accept(_ updated: [Volume]) {
        let changed = volumes.count != updated.count
        let previousLabIdentity = labVolume.map(ReadOnlyAttachmentIdentity.init)
        volumes = updated
        let currentLabIdentity = labVolume.map(ReadOnlyAttachmentIdentity.init)
        // Pending checkbox consent also expires when media changes, before inspection.
        if previousLabIdentity != currentLabIdentity { clearLabSelection() }
        if let selection = labSelection,
           !updated.contains(where: { selection.matches($0) }) {
            clearLabSelection()
        }
        if let labVolumeID, !updated.contains(where: { $0.id == labVolumeID }) { self.labVolumeID = nil }
        if !updated.contains(where: { $0.id == selectedID }) { selectedID = updated.first?.id }
        if changed { record("Volume list updated: \(updated.count) external volume(s).") }
    }

    private func record(_ message: String) {
        activity.insert(ActivityEntry(message: displayBackendMessage(message)), at: 0)
        if activity.count > 100 { activity.removeLast(activity.count - 100) }
    }
}
