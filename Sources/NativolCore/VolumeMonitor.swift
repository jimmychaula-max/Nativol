import Combine
import Darwin
import DiskArbitration
import Foundation
import IOKit

/// Subscribes to Disk Arbitration without claiming disks or changing mount policy.
@MainActor
public final class VolumeMonitor: ObservableObject {
    @Published public private(set) var volumes: [Volume] = []
    @Published public private(set) var errorMessage: String?

    private var inventory = VolumeInventory()
    private var observation: DiskObservation?
    private var generation: UUID?

    public init() {}

    /// A fresh metadata-only snapshot for an experimental request. This does not
    /// claim, open, mount, unmount, probe, or authorize access to a device.
    public nonisolated static func snapshotExperimentalVolume(bsdName: String, sessionID: UUID) -> Volume? {
        guard bsdName.range(of: "^disk[0-9]+s[0-9]+$", options: .regularExpression) != nil,
              let session = DASessionCreate(kCFAllocatorDefault),
              let disk = DADiskCreateFromBSDName(kCFAllocatorDefault, session, bsdName),
              let facts = DiskObservation.snapshot(disk) else { return nil }
        return facts.volume(sessionID: sessionID)
    }

    public func start() {
        guard observation == nil else { return }
        errorMessage = nil
        let generation = UUID()
        self.generation = generation
        // Callbacks are always scheduled on the main queue. The extra dispatch preserves
        // event order and lets a refresh invalidate any pending event from the old session.
        observation = DiskObservation { [weak self] event in
            DispatchQueue.main.async { [weak self] in
                guard let self, self.generation == generation else { return }
                self.receive(event)
            }
        }
        if observation == nil {
            self.generation = nil
            errorMessage = "Nativol could not connect to Disk Arbitration. Try scanning again."
        }
    }

    public func stop() {
        generation = nil
        observation = nil
        inventory.removeAll()
        volumes = []
    }

    /// A new subscription enumerates already attached disks as well as subsequent changes.
    /// Snapshots and attachment identifiers from the previous subscription are invalidated.
    public func refresh() {
        stop()
        start()
    }

    private func receive(_ event: DiskEvent) {
        switch event {
        case .update(let facts): inventory.update(facts)
        case .remove(let bsdName): inventory.remove(bsdName: bsdName)
        }
        volumes = inventory.volumes
    }
}

private enum DiskEvent {
    case update(DiskFacts)
    case remove(String)
}

/// Owns the C callback context for exactly as long as the session is registered.
/// Unregistering and unscheduling in deinit also covers clients forgetting to call stop().
private final class DiskObservation {
    private let session: DASession
    private let onEvent: (DiskEvent) -> Void

    init?(onEvent: @escaping (DiskEvent) -> Void) {
        guard let session = DASessionCreate(kCFAllocatorDefault) else { return nil }
        self.session = session
        self.onEvent = onEvent
        let context = Unmanaged.passUnretained(self).toOpaque()
        DARegisterDiskAppearedCallback(session, nil, diskAppeared, context)
        DARegisterDiskDisappearedCallback(session, nil, diskDisappeared, context)
        // A nil watch list covers mount, unmount, rename and all other description changes.
        DARegisterDiskDescriptionChangedCallback(session, nil, nil, diskChanged, context)
        DASessionSetDispatchQueue(session, DispatchQueue.main)
    }

    deinit {
        let context = Unmanaged.passUnretained(self).toOpaque()
        DAUnregisterCallback(session, unsafeBitCast(diskAppeared, to: UnsafeMutableRawPointer.self), context)
        DAUnregisterCallback(session, unsafeBitCast(diskDisappeared, to: UnsafeMutableRawPointer.self), context)
        DAUnregisterCallback(session, unsafeBitCast(diskChanged, to: UnsafeMutableRawPointer.self), context)
        DASessionSetDispatchQueue(session, nil)
    }

    func update(_ disk: DADisk) {
        if let facts = Self.snapshot(disk) { onEvent(.update(facts)) }
        else if let bsdName = DADiskGetBSDName(disk) {
            // Do not keep a stale, previously valid snapshot when metadata becomes unavailable.
            onEvent(.remove(String(cString: bsdName)))
        }
    }

    func remove(_ disk: DADisk) {
        guard let bsdName = DADiskGetBSDName(disk) else { return }
        onEvent(.remove(String(cString: bsdName)))
    }

    fileprivate static func snapshot(_ disk: DADisk) -> DiskFacts? {
        guard let bsdCString = DADiskGetBSDName(disk),
              let rawDescription = DADiskCopyDescription(disk) else { return nil }
        let description = rawDescription as NSDictionary
        let bsdName = String(cString: bsdCString)
        let wholeDisk = DADiskCopyWholeDisk(disk)
        let parentBSDName = wholeDisk.flatMap { DADiskGetBSDName($0) }.map { String(cString: $0) }
        let parentDescription = wholeDisk.flatMap { DADiskCopyDescription($0) }.map { $0 as NSDictionary }

        let childInternal = description[kDADiskDescriptionDeviceInternalKey] as? Bool
        let parentInternal = parentDescription?[kDADiskDescriptionDeviceInternalKey] as? Bool
        // Any positive internal flag wins over contradictory child metadata.
        let isInternal: Bool? = childInternal == true || parentInternal == true
            ? true : (childInternal ?? parentInternal)
        let transport = description[kDADiskDescriptionDeviceProtocolKey] as? String
            ?? parentDescription?[kDADiskDescriptionDeviceProtocolKey] as? String
        let path = (description[kDADiskDescriptionVolumePathKey] as? URL)?.path
        let name = description[kDADiskDescriptionVolumeNameKey] as? String
        let filesystem = description[kDADiskDescriptionVolumeKindKey] as? String
        let geometry = wholeDisk.flatMap { mediaGeometry(partition: disk, whole: $0) }
        return DiskFacts(
            bsdName: bsdName,
            parentBSDName: parentBSDName == bsdName ? nil : parentBSDName,
            name: name.flatMap { $0.isEmpty ? nil : $0 } ?? bsdName,
            filesystem: filesystem.flatMap { $0.isEmpty ? nil : $0 } ?? "Unknown",
            mountPath: path,
            capacityBytes: (description[kDADiskDescriptionMediaSizeKey] as? NSNumber)?.int64Value ?? 0,
            isInternal: isInternal,
            isWhole: description[kDADiskDescriptionMediaWholeKey] as? Bool,
            isMountable: description[kDADiskDescriptionVolumeMountableKey] as? Bool,
            isNetwork: description[kDADiskDescriptionVolumeNetworkKey] as? Bool,
            deviceProtocol: transport,
            isWritable: mountedWritability(path: path, bsdName: bsdName),
            partitionUUID: uuidString(description[kDADiskDescriptionMediaUUIDKey])
                ?? uuidString(description[kDADiskDescriptionVolumeUUIDKey]),
            mediaUUID: parentDescription.flatMap { uuidString($0[kDADiskDescriptionMediaUUIDKey]) },
            partitionRegistryID: registryEntryID(disk),
            mediaRegistryID: wholeDisk.flatMap(registryEntryID),
            mediaCapacityBytes: (parentDescription?[kDADiskDescriptionMediaSizeKey] as? NSNumber)?.int64Value,
            blockSizeBytes: (description[kDADiskDescriptionMediaBlockSizeKey] as? NSNumber)?.int64Value,
            partitionOffsetBytes: geometry?.offset,
            parentBlockSizeBytes: (parentDescription?[kDADiskDescriptionMediaBlockSizeKey] as? NSNumber)?.int64Value,
            partitionMap: parentDescription?[kDADiskDescriptionMediaContentKey] as? String,
            parentRelationVerified: geometry?.parentVerified,
            partitionCount: geometry?.partitionCount
        )
    }

    private struct MediaGeometry {
        let offset: Int64?
        let parentVerified: Bool
        let partitionCount: Int?
    }

    private static func mediaGeometry(partition: DADisk, whole: DADisk) -> MediaGeometry? {
        let child = DADiskCopyIOMedia(partition)
        let parent = DADiskCopyIOMedia(whole)
        guard child != IO_OBJECT_NULL, parent != IO_OBJECT_NULL else {
            if child != IO_OBJECT_NULL { IOObjectRelease(child) }
            if parent != IO_OBJECT_NULL { IOObjectRelease(parent) }
            return nil
        }
        defer { IOObjectRelease(child); IOObjectRelease(parent) }
        guard IOObjectConformsTo(child, "IOMedia") != 0,
              IOObjectConformsTo(parent, "IOMedia") != 0,
              property(parent, "Whole") as? Bool == true,
              property(child, "Whole") as? Bool == false else { return nil }
        let offset = (property(child, "Base") as? NSNumber)?.int64Value
        var parentID: UInt64 = 0
        guard IORegistryEntryGetRegistryEntryID(parent, &parentID) == KERN_SUCCESS, parentID != 0 else { return nil }
        return MediaGeometry(offset: offset, parentVerified: isDescendant(child, of: parentID),
                             partitionCount: partitionCount(parent))
    }

    private static func property(_ entry: io_registry_entry_t, _ key: String) -> Any? {
        IORegistryEntryCreateCFProperty(entry, key as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue()
    }

    private static func isDescendant(_ child: io_registry_entry_t, of parentID: UInt64) -> Bool {
        var current = child
        IOObjectRetain(current)
        defer { IOObjectRelease(current) }
        for _ in 0..<64 {
            var parent: io_registry_entry_t = IO_OBJECT_NULL
            guard IORegistryEntryGetParentEntry(current, kIOServicePlane, &parent) == KERN_SUCCESS else { return false }
            IOObjectRelease(current)
            current = parent
            var identifier: UInt64 = 0
            guard IORegistryEntryGetRegistryEntryID(current, &identifier) == KERN_SUCCESS else { return false }
            if identifier == parentID { return true }
        }
        return false
    }

    private static func partitionCount(_ whole: io_registry_entry_t) -> Int? {
        var iterator: io_iterator_t = IO_OBJECT_NULL
        guard IORegistryEntryCreateIterator(whole, kIOServicePlane, IOOptionBits(kIORegistryIterateRecursively),
                                            &iterator) == KERN_SUCCESS else { return nil }
        defer { IOObjectRelease(iterator) }
        var count = 0
        // Bound metadata traversal; complex or incomplete topologies fail closed.
        for _ in 0..<256 {
            let entry = IOIteratorNext(iterator)
            if entry == IO_OBJECT_NULL { return IOIteratorIsValid(iterator) != 0 ? count : nil }
            if IOObjectConformsTo(entry, "IOMedia") != 0 {
                guard property(entry, "Whole") as? Bool == false,
                      property(entry, "Partition ID") is NSNumber else {
                    IOObjectRelease(entry)
                    return nil
                }
                count += 1
            }
            IOObjectRelease(entry)
        }
        return nil
    }

    /// Reads current I/O Registry metadata only. This never opens a raw device.
    /// Registry IDs describe kernel objects for this boot and must not be persisted.
    private static func registryEntryID(_ disk: DADisk) -> UInt64? {
        let media = DADiskCopyIOMedia(disk)
        guard media != IO_OBJECT_NULL else { return nil }
        defer { IOObjectRelease(media) }
        guard IOObjectConformsTo(media, "IOMedia") != 0 else { return nil }
        var identifier: UInt64 = 0
        guard IORegistryEntryGetRegistryEntryID(media, &identifier) == KERN_SUCCESS,
              identifier != 0 else { return nil }
        return identifier
    }

    private static func uuidString(_ value: Any?) -> String? {
        guard let value else { return nil }
        if let uuid = value as? UUID { return uuid.uuidString }
        if let string = value as? String, let uuid = UUID(uuidString: string) { return uuid.uuidString }
        let object = value as AnyObject
        guard CFGetTypeID(object) == CFUUIDGetTypeID() else { return nil }
        return CFUUIDCreateString(kCFAllocatorDefault, unsafeBitCast(object, to: CFUUID.self)) as String?
    }

    private static func mountedWritability(path: String?, bsdName: String) -> Bool? {
        guard let path else { return nil }
        var status = statfs()
        guard path.withCString({ statfs($0, &status) }) == 0 else { return nil }
        let source = withUnsafePointer(to: &status.f_mntfromname) { pointer in
            pointer.withMemoryRebound(to: CChar.self, capacity: Int(MAXPATHLEN)) {
                String(cString: $0)
            }
        }
        // A removed mount path might now resolve to the host filesystem. Never report that
        // filesystem's writable flag as if it belonged to the disconnected volume.
        guard source == "/dev/\(bsdName)" else { return nil }
        return (status.f_flags & UInt32(MNT_RDONLY)) == 0
    }
}

private let diskAppeared: DADiskAppearedCallback = { disk, context in
    guard let context else { return }
    Unmanaged<DiskObservation>.fromOpaque(context).takeUnretainedValue().update(disk)
}

private let diskDisappeared: DADiskDisappearedCallback = { disk, context in
    guard let context else { return }
    Unmanaged<DiskObservation>.fromOpaque(context).takeUnretainedValue().remove(disk)
}

private let diskChanged: DADiskDescriptionChangedCallback = { disk, _, context in
    guard let context else { return }
    Unmanaged<DiskObservation>.fromOpaque(context).takeUnretainedValue().update(disk)
}
