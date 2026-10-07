import Foundation

/// A snapshot of a volume. The session identifier changes after detachment or a new scan.
/// A BSD name alone must never identify a future privileged operation: macOS reuses names.
public struct Volume: Identifiable, Equatable {
    public var id: String { bsdName }
    public let name: String
    public let bsdName: String
    public let parentBSDName: String?
    public let filesystem: String
    public let mountPath: String?
    public let capacityBytes: Int64
    public let isInternal: Bool?
    /// Actual mounted filesystem access, not the hardware's write-capable flag.
    /// This does not mean Nativol provides or certifies a write driver.
    public let isWritable: Bool?
    public let isWhole: Bool
    public let sessionID: UUID
    /// OS-reported partition identity (media UUID, or filesystem UUID where unavailable).
    public let partitionUUID: String?
    public let mediaUUID: String?
    public let isPhysical: Bool?
    /// Current kernel IOMedia identities, never persistent physical-device identifiers.
    public let partitionRegistryID: UInt64?
    public let mediaRegistryID: UInt64?
    public let mediaCapacityBytes: Int64?
    public let blockSizeBytes: Int64?
    public let partitionOffsetBytes: Int64?
    public let parentBlockSizeBytes: Int64?
    public let deviceProtocol: String?
    public let partitionMap: String?
    /// Verified through the I/O Registry hierarchy, not inferred from BSD names.
    public let parentRelationVerified: Bool?
    public let partitionCount: Int?

    public init(
        name: String,
        bsdName: String,
        parentBSDName: String? = nil,
        filesystem: String,
        mountPath: String? = nil,
        capacityBytes: Int64 = 0,
        isInternal: Bool? = nil,
        isWritable: Bool? = nil,
        isWhole: Bool = false,
        sessionID: UUID = UUID(),
        partitionUUID: String? = nil,
        mediaUUID: String? = nil,
        isPhysical: Bool? = nil,
        partitionRegistryID: UInt64? = nil,
        mediaRegistryID: UInt64? = nil,
        mediaCapacityBytes: Int64? = nil,
        blockSizeBytes: Int64? = nil,
        partitionOffsetBytes: Int64? = nil,
        parentBlockSizeBytes: Int64? = nil,
        deviceProtocol: String? = nil,
        partitionMap: String? = nil,
        parentRelationVerified: Bool? = nil,
        partitionCount: Int? = nil
    ) {
        self.name = name
        self.bsdName = bsdName
        self.parentBSDName = parentBSDName
        self.filesystem = filesystem
        self.mountPath = mountPath
        self.capacityBytes = max(0, capacityBytes)
        self.isInternal = isInternal
        self.isWritable = isWritable
        self.isWhole = isWhole
        self.sessionID = sessionID
        self.partitionUUID = partitionUUID
        self.mediaUUID = mediaUUID
        self.isPhysical = isPhysical
        self.partitionRegistryID = partitionRegistryID
        self.mediaRegistryID = mediaRegistryID
        self.mediaCapacityBytes = mediaCapacityBytes
        self.blockSizeBytes = blockSizeBytes
        self.partitionOffsetBytes = partitionOffsetBytes
        self.parentBlockSizeBytes = parentBlockSizeBytes
        self.deviceProtocol = deviceProtocol
        self.partitionMap = partitionMap
        self.parentRelationVerified = parentRelationVerified
        self.partitionCount = partitionCount
    }

    /// Only a positively reported filesystem kind qualifies. A partition type such as
    /// "Microsoft Basic Data" cannot distinguish NTFS from exFAT and is not sufficient.
    public var isNTFS: Bool {
        filesystem.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "ntfs"
    }

    public var isExternal: Bool { isInternal == false }

    public var accessLabel: String {
        guard mountPath != nil else { return "Not mounted" }
        switch isWritable {
        case .some(true): return "Read & write · existing driver"
        case .some(false): return "Read-only"
        case nil: return "Access unknown"
        }
    }

    /// In-memory examples. These paths intentionally do not refer to real mounts.
    public static let demoVolumes: [Volume] = [
        Volume(name: "Windows Archive", bsdName: "demo-disk1s1", parentBSDName: "demo-disk1",
               filesystem: "ntfs", mountPath: "/Nativol Demo/Windows Archive",
               capacityBytes: 1_000_204_886_016, isInternal: false, isWritable: false),
        Volume(name: "Travel SSD", bsdName: "demo-disk2s1", parentBSDName: "demo-disk2",
               filesystem: "exfat", mountPath: "/Nativol Demo/Travel SSD",
               capacityBytes: 500_107_862_016, isInternal: false, isWritable: true),
        Volume(name: "Backup", bsdName: "demo-disk3s1", parentBSDName: "demo-disk3",
               filesystem: "ntfs", capacityBytes: 2_000_398_934_016, isInternal: false)
    ]
}

/// Kept separate from Disk Arbitration to exercise conservative discovery without hardware.
struct DiskFacts {
    let bsdName: String
    let parentBSDName: String?
    let name: String
    let filesystem: String
    let mountPath: String?
    let capacityBytes: Int64
    let isInternal: Bool?
    let isWhole: Bool?
    let isMountable: Bool?
    let isNetwork: Bool?
    let deviceProtocol: String?
    let isWritable: Bool?
    var partitionUUID: String? = nil
    var mediaUUID: String? = nil
    var partitionRegistryID: UInt64? = nil
    var mediaRegistryID: UInt64? = nil
    var mediaCapacityBytes: Int64? = nil
    var blockSizeBytes: Int64? = nil
    var partitionOffsetBytes: Int64? = nil
    var parentBlockSizeBytes: Int64? = nil
    var partitionMap: String? = nil
    var parentRelationVerified: Bool? = nil
    var partitionCount: Int? = nil

    var isDiscoverable: Bool {
        guard isInternal == false, isWhole == false, isMountable == true,
              isNetwork != true, let deviceProtocol else { return false }
        // Unknown transports and virtual interfaces stay excluded. This is a deliberate
        // allowlist, not an assumption that every device marked external is physical.
        let physicalTransports: Set<String> = [
            "usb", "firewire", "thunderbolt", "sata", "ata", "sas",
            "pci-express", "secure digital", "sd"
        ]
        return physicalTransports.contains(deviceProtocol.lowercased())
    }

    func volume(sessionID: UUID) -> Volume? {
        guard isDiscoverable else { return nil }
        return Volume(name: name, bsdName: bsdName, parentBSDName: parentBSDName,
                      filesystem: filesystem, mountPath: mountPath,
                      capacityBytes: capacityBytes, isInternal: isInternal,
                      isWritable: isWritable, isWhole: false, sessionID: sessionID,
                      partitionUUID: partitionUUID, mediaUUID: mediaUUID, isPhysical: true,
                      partitionRegistryID: partitionRegistryID, mediaRegistryID: mediaRegistryID,
                      mediaCapacityBytes: mediaCapacityBytes, blockSizeBytes: blockSizeBytes,
                      partitionOffsetBytes: partitionOffsetBytes, parentBlockSizeBytes: parentBlockSizeBytes,
                      deviceProtocol: deviceProtocol, partitionMap: partitionMap,
                      parentRelationVerified: parentRelationVerified, partitionCount: partitionCount)
    }
}

/// Reconciles events instead of appending callbacks, preventing duplicates and stale mounts.
struct VolumeInventory {
    private(set) var volumesByBSDName: [String: Volume] = [:]
    private var attachmentIDs: [String: UUID] = [:]
    private var parentByBSDName: [String: String] = [:]

    var volumes: [Volume] {
        volumesByBSDName.values.sorted {
            if $0.isNTFS != $1.isNTFS { return $0.isNTFS }
            let comparison = $0.name.localizedStandardCompare($1.name)
            return comparison == .orderedSame ? $0.bsdName < $1.bsdName : comparison == .orderedAscending
        }
    }

    mutating func update(_ facts: DiskFacts) {
        let attachmentID = attachmentIDs[facts.bsdName] ?? UUID()
        attachmentIDs[facts.bsdName] = attachmentID
        if let parent = facts.parentBSDName { parentByBSDName[facts.bsdName] = parent }
        // Assigning nil removes an entry if an updated description is no longer eligible.
        volumesByBSDName[facts.bsdName] = facts.volume(sessionID: attachmentID)
    }

    mutating func remove(bsdName: String) {
        let affected = parentByBSDName.filter { $0.value == bsdName }.map(\.key)
        for name in affected + [bsdName] {
            volumesByBSDName.removeValue(forKey: name)
            attachmentIDs.removeValue(forKey: name)
            parentByBSDName.removeValue(forKey: name)
        }
    }

    mutating func removeAll() {
        volumesByBSDName.removeAll()
        attachmentIDs.removeAll()
        parentByBSDName.removeAll()
    }
}
