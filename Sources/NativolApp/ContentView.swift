import SwiftUI
import NativolCore

private enum Design {
    static let accent = Color(nsColor: NSColor(name: "NativolAccent") { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.38, green: 0.77, blue: 0.69, alpha: 1)
            : NSColor(calibratedRed: 0.12, green: 0.47, blue: 0.43, alpha: 1)
    })
    static let canvas = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let line = Color.primary.opacity(0.08)
}

struct ContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                if model.demoMode {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkles")
                        Text("Demo mode · Sample volumes, no device actions")
                        Spacer()
                        Button("Exit Demo") { model.setDemo(false) }.buttonStyle(.plain).font(.system(size: 12, weight: .semibold))
                    }
                    .font(.system(size: 12))
                    .padding(.horizontal, 28).padding(.vertical, 10)
                    .background(Design.accent.opacity(0.10))
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        switch model.section {
                        case .drives: volumesContent
                        case .lab: TestLabView(model: model)
                        case .setup: setupContent
                        case .activity: activityContent
                        }
                    }
                    .padding(28)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                Divider()
                HStack(spacing: 6) {
                    Image(systemName: "lock.shield")
                    Text("Local by design. Your files stay on your Mac.")
                    Spacer()
                    Text("v0.5.0-beta.1 Intel beta")
                }
                .font(.system(size: 11)).foregroundColor(.secondary)
                .padding(.horizontal, 28).padding(.vertical, 12)
            }
        }
        .background(Design.canvas)
        .accentColor(Design.accent)
        .frame(minWidth: 920, minHeight: 620)
        .alert("Nativol", isPresented: Binding(get: { model.alertMessage != nil }, set: { if !$0 { model.alertMessage = nil } })) {
            Button("OK") { model.alertMessage = nil }
        } message: { Text(model.alertMessage ?? "") }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                BrandMark().frame(width: 36, height: 36)
                Text("Nativol").font(.system(size: 24, weight: .semibold, design: .rounded)).tracking(-0.7)
            }.padding(.top, 26).padding(.bottom, 34)
            Text("WORKSPACE").font(.system(size: 10, weight: .semibold)).tracking(1.5).foregroundColor(.secondary).padding(.bottom, 12)
            ForEach(AppSection.allCases, id: \.self) { section in
                Button { model.section = section } label: {
                    HStack(spacing: 10) {
                        Image(systemName: section.symbol).font(.system(size: 15)).frame(width: 19)
                        Text(section.rawValue).font(.system(size: 13, weight: model.section == section ? .semibold : .regular))
                        Spacer()
                        if section == .drives {
                            Text("\(model.volumes.count)").font(.system(size: 11, weight: .medium)).foregroundColor(.secondary)
                        }
                    }
                    .padding(.horizontal, 12).padding(.vertical, 11)
                    .background(model.section == section ? Design.accent.opacity(0.12) : .clear)
                    .foregroundColor(model.section == section ? Design.accent : .primary)
                    .cornerRadius(9)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(model.section == section ? .isSelected : [])
                .padding(.bottom, 5)
            }
            Spacer()
            VStack(alignment: .leading, spacing: 10) {
                Label("Made for your Mac", systemImage: "laptopcomputer").font(.system(size: 12, weight: .medium))
                Text("\(model.environment.architecture)\nmacOS \(model.environment.osVersion)")
                    .font(.system(size: 11)).lineSpacing(4).foregroundColor(.secondary)
            }
            Divider().padding(.vertical, 18)
            Button(model.demoMode ? "View connected volumes" : "Explore demo") { model.setDemo(!model.demoMode); model.section = .drives }
                .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundColor(Design.accent)
                .padding(.bottom, 10)
            Text("Intel testing preview").font(.system(size: 10)).foregroundColor(.secondary)
                .padding(.bottom, 24)
        }
        .padding(.horizontal, 20)
        .frame(width: 192)
        .background(Design.panel.opacity(0.45))
    }

    private var header: some View {
        HStack {
            Text(model.section.rawValue).font(.system(size: 14, weight: .semibold))
            Spacer()
            Button { model.refresh() } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .buttonStyle(.borderless).font(.system(size: 12))
                .keyboardShortcut("r", modifiers: .command)
        }.padding(.horizontal, 28).padding(.vertical, 18)
    }

    @ViewBuilder private var volumesContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.volumes.isEmpty ? "A native home for\nyour volumes." : "Your volumes, at a glance.").font(.system(size: 29, weight: .semibold)).tracking(-0.8).fixedSize(horizontal: false, vertical: true)
            Text("Your external drives, together in one place.").font(.system(size: 13)).foregroundColor(.secondary)
        }
        if let error = model.discoveryError {
            notice("Discovery needs attention", detail: error, symbol: "exclamationmark.triangle")
        }
        ForEach(model.activeManagedCards, id: \.operationId) { managed in
            notice(managed.isMounted ? (managed.isReadOnly ? "Read-only · " + model.displayName(for: managed) : "Read & write · " + model.displayName(for: managed)) : "Preparing · " + model.displayName(for: managed),
                   detail: model.displayBackendMessage(managed.message), symbol: managed.isMounted ? "checkmark.circle" : "externaldrive")
            if managed.isMounted {
                HStack {
                    Button("Open in Finder") { NSWorkspace.shared.open(URL(fileURLWithPath: managed.mountPath)) }
                    Button("Stop Writing") { model.stopCard(managed, eject: false) }.disabled(model.backendBusy)
                    Button("Safely Eject") { model.stopCard(managed, eject: true) }.disabled(model.backendBusy)
                }
            }
        }
        if let recent = model.managedCards.first, recent.state == "failed", model.volumes.contains(where: { recent.matches($0) }) {
            notice("A volume needs attention", detail: model.displayBackendMessage(recent.message), symbol: "info.circle")
            if recent.message.contains("Full Disk Access") {
                Button("Review Access in Setup") { model.section = .setup }
            }
        }
        if model.volumes.isEmpty {
            emptyState
        } else {
            HStack(spacing: 14) {
                Label("\(model.volumes.count) connected", systemImage: "externaldrive")
                Text("\(model.ntfsCount) NTFS")
                Spacer()
                Text("Intel beta · NTFS writing")
            }.font(.system(size: 11)).foregroundColor(.secondary)
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Connected volumes").font(.system(size: 14, weight: .semibold))
                    Spacer()
                    Text(model.demoMode ? "SAMPLE DATA" : "LIVE").font(.system(size: 10, weight: .semibold)).tracking(1).foregroundColor(.secondary)
                }
                ForEach(model.volumes) { volume in
                    Button { model.selectedID = volume.id } label: { volumeRow(volume) }.buttonStyle(.plain)
                }
            }
            if let volume = model.selectedVolume { volumeDetails(volume) }
        }
        if model.environment.competingAppPresent && !model.demoMode {
            notice("Another NTFS app is installed", detail: "Nativol found an existing NTFS application. Its presence does not tell us whether its driver is active. Review Setup before planning a writable driver installation.", symbol: "info.circle")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle().fill(Design.accent.opacity(0.07)).frame(width: 102, height: 102)
                Image(systemName: "externaldrive.badge.plus").font(.system(size: 42, weight: .light)).foregroundColor(Design.accent)
            }
            Text("Your next connection starts here.").font(.system(size: 19, weight: .medium))
            Text("Connect an external drive to see its format and access status.\nInternal storage and disk images stay out of this list.")
                .font(.system(size: 13)).foregroundColor(.secondary).multilineTextAlignment(.center).lineSpacing(4)
            Button("Explore with sample volumes") { model.setDemo(true) }.buttonStyle(.bordered).padding(.top, 4)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 32).padding(.horizontal, 16)
        .background(Design.panel).cornerRadius(16)
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Design.line))
    }

    private func volumeRow(_ volume: Volume) -> some View {
        HStack(spacing: 14) {
            Image(systemName: "externaldrive.fill").font(.system(size: 26)).foregroundColor(volume.isNTFS ? Design.accent : .secondary)
                .frame(width: 44, height: 44).background(Design.accent.opacity(volume.isNTFS ? 0.09 : 0.03)).cornerRadius(10)
            VStack(alignment: .leading, spacing: 5) {
                Text(volume.name).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                Text("\(volume.filesystem.uppercased()) · \(capacity(volume.capacityBytes))")
                    .font(.system(size: 11)).foregroundColor(.secondary)
            }
            Spacer()
            Label(access(volume), systemImage: model.managedStatus(for: volume)?.isMounted == true || volume.isWritable == true ? "checkmark.circle" : "lock")
                .font(.system(size: 11, weight: .medium)).foregroundColor(.secondary)
            Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundColor(.secondary).padding(.leading, 8)
        }
        .padding(14).background(Design.panel).cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(model.selectedID == volume.id ? Design.accent.opacity(0.55) : Design.line, lineWidth: 1))
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(model.selectedID == volume.id ? .isSelected : [])
    }

    private func volumeDetails(_ volume: Volume) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(volume.name).font(.system(size: 16, weight: .semibold))
                Spacer()
                Text(volume.isNTFS ? "NTFS" : volume.filesystem.uppercased()).font(.system(size: 10, weight: .semibold)).tracking(1).foregroundColor(Design.accent)
            }
            Divider()
            HStack(alignment: .top, spacing: 24) {
                detail("Capacity", capacity(volume.capacityBytes))
                detail("Access", access(volume))
                detail("Connection", "External")
            }
            if volume.isNTFS {
                Text(model.managedStatus(for: volume)?.isMounted == true
                     ? "Open the volume in Finder to copy and edit files. Use Stop Writing or Safely Eject when finished. The driver keeps running if you close Nativol."
                     : (model.canTestCard(volume) ? "This external NTFS volume is eligible for a health check. Enable writing to make it available in Finder."
                        : "Writing is unavailable for this connection. Keep Read-only preferences, drive topology, existing access and the supported macOS version are checked before writing."))
                    .font(.system(size: 12)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("This volume uses \(volume.filesystem.uppercased()). Nativol shows its existing access. macOS continues to manage this filesystem.")
                    .font(.system(size: 12)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button { model.openVolume(volume) } label: { Label("Open in Finder", systemImage: "folder") }
                    .buttonStyle(.borderedProminent).disabled(model.demoMode || (volume.mountPath == nil && model.managedStatus(for: volume)?.isMounted != true))
                if volume.isNTFS {
                    if model.managedStatus(for: volume)?.isMounted != true {
                        Button("Enable Writing") { model.showWriteSetup(volume) }
                            .disabled(!model.canTestCard(volume) || model.backendBusy)
                    }
                }
                Spacer()
                Button("Disk Utility…") { model.openDiskUtility() }.buttonStyle(.borderless).disabled(model.demoMode)
            }
            if volume.isNTFS && model.managedStatus(for: volume) == nil {
                if model.isExcludedFromLab(volume) {
                    Label("Kept read-only", systemImage: "lock.shield").font(.system(size: 11))
                    Button("Review Read-only Preference") { model.showWriteSetup(volume) }.buttonStyle(.borderless)
                } else {
                    Button("Keep Read-only") { model.excludeFromLab(volume) }
                        .buttonStyle(.borderless).disabled(model.demoMode)
                }
            }
            Text("Eject each volume in Finder or with its Safely Eject action before disconnecting it.")
                .font(.system(size: 10)).foregroundColor(.secondary)
        }
        .padding(20).background(Design.panel).cornerRadius(14)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Design.line))
    }

    private var setupContent: some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 8) {
                Text("A good foundation.").font(.system(size: 29, weight: .semibold)).tracking(-0.6)
                Text("Understand what is ready, and what comes next.").font(.system(size: 13)).foregroundColor(.secondary)
            }
            notice("Intel testing preview", detail: "Writing is enabled only on native Intel Macs running macOS 15.7.9 with the verified macFUSE 5.4.0 runtime. Each eligible external NTFS volume receives a health check before mounting. Broader drive and Mac testing is still in progress.", symbol: "info.circle")
            VStack(alignment: .leading, spacing: 12) {
                Text("Quietly ready when you need it").font(.system(size: 17, weight: .semibold))
                Text(model.backgroundServiceMessage)
                    .font(.system(size: 12)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                Toggle("Automatically enable writing for external NTFS drives", isOn: Binding(
                    get: { model.automaticMountingEnabled }, set: { model.setAutomaticMounting($0) }))
                    .disabled(model.demoMode)
                Toggle("Start in the menu bar when I sign in", isOn: Binding(
                    get: { model.launchAtLoginEnabled }, set: { model.setLaunchAtLogin($0) }))
                    .disabled(model.demoMode || !LoginStartup.supported)
                Text("Close the window to keep Nativol running in the menu bar. Stop Writing pauses automatic writing until you reconnect the drive.")
                    .font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(model.backgroundServiceInstalled ? "Repair Background Access…" : "Set Up Background Access…") { model.setupBackgroundAccess() }
                        .disabled(model.demoMode || model.backendBusy || model.backgroundSetupBusy || model.activeManagedCard != nil || !PersonalBackend.supportedHost)
                    if LoginStartup.requiresApproval { Button("Open Login Items") { LoginStartup.openSettings() } }
                    if model.backgroundSetupBusy { ProgressView().controlSize(.small) }
                }
                Text("Setup requires an administrator password once. Installations and future updates can ask again; ordinary drive actions use the background service.")
                    .font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(20).background(Design.panel).cornerRadius(14)
            VStack(spacing: 0) {
                setupRow("Running on this Mac", detail: "macOS \(model.environment.osVersion) · \(model.environment.architecture)", state: "Detected", symbol: "laptopcomputer")
                Divider().padding(.leading, 56)
                setupRow("macFUSE runtime", detail: "Filesystem bridge. Presence alone does not verify compatibility.", state: model.environment.macFUSEPresent ? "Found" : "Not found", symbol: "square.stack.3d.up")
                Divider().padding(.leading, 56)
                setupRow("Protected NTFS backend", detail: "Pinned driver and a separate administrator helper.", state: model.backendInstalled ? "Installed" : "Not installed", symbol: "externaldrive")
                Divider().padding(.leading, 56)
                setupRow("Compatibility", detail: "Intel macOS 15.7.9. New drive types and additional Intel Macs need testing.", state: "Beta", symbol: "checkmark.shield")
            }.background(Design.panel).cornerRadius(14).overlay(RoundedRectangle(cornerRadius: 14).stroke(Design.line))
            VStack(alignment: .leading, spacing: 10) {
                Text("Allow access to external drives").font(.system(size: 15, weight: .semibold))
                Text("In macOS Full Disk Access, add or enable \(model.privacyComponentName). Show \(model.privacyComponentName) reveals the installed file to select with the + button. Then return to Write Access and retry Enable Writing.")
                    .font(.system(size: 12)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                if let path = model.privacyComponentPath {
                    Text(path).font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Button("Open Full Disk Access") { model.openFullDiskAccess() }.disabled(model.demoMode)
                    Button("Show \(model.privacyComponentName)") { model.revealPersonalHelper() }.disabled(model.demoMode || !model.privacyComponentAvailable)
                }
            }
            if model.environment.competingAppPresent {
                notice("Existing NTFS software detected", detail: "Another NTFS application is installed. Nativol has not changed or disabled it. Driver ownership must be established before writable mounting can be introduced.", symbol: "info.circle")
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Intel release scope").font(.system(size: 15, weight: .semibold))
                Text("This release targets Intel Macs. The interface requires macOS 12 or later; writing is restricted to macOS 15.7.9. Apple silicon work is paused. Unsupported drives remain under their existing macOS access.")
                    .font(.system(size: 12)).foregroundColor(.secondary).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button(model.backendInstalled ? "Recheck Backend Installation" : "Install NTFS Backend") { model.installPersonalBackend() }
                    .disabled(model.demoMode || model.backendBusy || !PersonalBackend.supportedHost)
                Button("Check Again") { model.refresh() }.buttonStyle(.borderedProminent)
                Button("Export Diagnostics…") { model.exportDiagnostics() }.buttonStyle(.bordered)
            }
        }
    }

    private func access(_ volume: Volume) -> String {
        if let managed = model.managedStatus(for: volume), managed.isMounted {
            return managed.isReadOnly ? "Read-only · Nativol" : "Read & write · Nativol"
        }
        return volume.accessLabel
    }

    private var activityContent: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A little clarity.").font(.system(size: 29, weight: .semibold)).tracking(-0.6)
                    Text("Activity from this session. Cleared when you quit.").font(.system(size: 13)).foregroundColor(.secondary)
                }
                Spacer()
                Button("Clear") { model.clearActivity() }.disabled(model.activity.isEmpty)
            }
            if model.activity.isEmpty {
                notice("All clear", detail: "New activity will appear here as you use Nativol.", symbol: "clock")
            } else {
                VStack(spacing: 0) {
                    ForEach(model.activity) { entry in
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: "circle.fill").font(.system(size: 6)).foregroundColor(Design.accent).padding(.top, 6)
                            Text(entry.message).font(.system(size: 12)).frame(maxWidth: .infinity, alignment: .leading)
                            Text(entry.date, style: .time).font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
                        }.padding(16)
                        if entry.id != model.activity.last?.id { Divider().padding(.leading, 32) }
                    }
                }.background(Design.panel).cornerRadius(14).overlay(RoundedRectangle(cornerRadius: 14).stroke(Design.line))
            }
            Button("Export Diagnostics…") { model.exportDiagnostics() }
        }
    }

    private func setupRow(_ title: String, detail: String, state: String, symbol: String) -> some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 18)).foregroundColor(Design.accent).frame(width: 26)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 13, weight: .medium))
                Text(detail).font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            Text(state).font(.system(size: 11, weight: .medium)).foregroundColor(.secondary)
        }.padding(17)
    }

    private func notice(_ title: String, detail: String, symbol: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol).font(.system(size: 17)).foregroundColor(Design.accent).padding(.top, 1)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 12)).foregroundColor(.secondary).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).background(Design.accent.opacity(0.06)).cornerRadius(12)
    }

    private func detail(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label).font(.system(size: 10)).foregroundColor(.secondary)
            Text(value).font(.system(size: 12, weight: .medium))
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func capacity(_ bytes: Int64) -> String {
        bytes > 0 ? ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) : "Unknown capacity"
    }
}

struct BrandMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 10).fill(Color(red: 0.12, green: 0.47, blue: 0.43))
            HStack(spacing: 3) {
                Capsule().fill(.white).frame(width: 4, height: 18)
                Capsule().fill(.white.opacity(0.8)).frame(width: 4, height: 21).rotationEffect(.degrees(-30))
                Capsule().fill(.white).frame(width: 4, height: 18)
            }
        }.accessibilityHidden(true)
    }
}
