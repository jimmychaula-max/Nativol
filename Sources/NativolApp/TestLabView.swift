import SwiftUI
import NativolCore

struct TestLabView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Write access.").font(.system(size: 29, weight: .semibold)).tracking(-0.6)
                Text("Use external NTFS volumes in Finder.")
                    .font(.system(size: 13)).foregroundColor(.secondary)
            }
            VStack(alignment: .leading, spacing: 16) {
                Label("Choose a volume", systemImage: "externaldrive").font(.system(size: 15, weight: .semibold))
                Text("Nativol checks the selected attachment and filesystem health before enabling writing. Filesystems that need repair, are hibernated, or are already owned by another writer are refused. This Intel beta supports macOS 15.7.9 only.")
                    .font(.system(size: 12)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                if model.demoMode {
                    Label("Exit demo mode to choose a connected volume.", systemImage: "info.circle")
                } else if model.volumes.isEmpty {
                    Label("Connect an external drive to get started.", systemImage: "externaldrive.badge.plus")
                } else {
                    Picker("Volume", selection: Binding(get: { model.labVolumeID ?? "" }, set: { model.labVolumeID = $0.isEmpty ? nil : $0 })) {
                        Text("Choose a volume…").tag("")
                        ForEach(model.volumes) { volume in
                            Text("\(volume.name) · \(ByteCountFormatter.string(fromByteCount: volume.capacityBytes, countStyle: .file)) · \(volume.filesystem.uppercased())").tag(volume.id)
                        }
                    }
                    if let volume = model.labVolume {
                        if model.isExcludedFromLab(volume) {
                            Label("This volume is kept read-only.", systemImage: "lock.shield")
                            Text("The remembered preference matches available volume identity and geometry. Another drive with the same geometry may also remain read-only. Allow Writing Again removes these matching preferences.")
                                .font(.system(size: 11)).foregroundColor(.secondary)
                            Button("Allow Writing Again") { model.allowWritingAgain(volume) }.buttonStyle(.borderless)
                        } else if let status = model.managedStatus(for: volume) {
                            Text(model.displayBackendMessage(status.message)).font(.system(size: 12))
                        } else {
                            HStack {
                                Button("Enable Writing") { model.enableCardWriting() }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(!model.canTestCard(volume) || !model.backendInstalled || model.backendBusy)
                                Button("Keep Read-only") { model.excludeFromLab(volume) }
                                    .buttonStyle(.borderless)
                            }
                            if !model.backendInstalled {
                                Button("Set Up NTFS Access") { model.section = .setup }.buttonStyle(.borderless)
                            } else if !model.canTestCard(volume) {
                                Text("Writing is unavailable for this connection. Supported external NTFS volumes need a unique physical attachment, one partition, and an existing read-only macOS mount.")
                                    .font(.system(size: 11)).foregroundColor(.secondary)
                            }
                        }
                    }
                }
            }.padding(20).background(Color(nsColor: .controlBackgroundColor)).cornerRadius(14)
            Button("Configure automatic writing in Setup") { model.section = .setup }.buttonStyle(.borderless)
        }
    }
}
