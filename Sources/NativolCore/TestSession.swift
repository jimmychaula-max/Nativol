import Foundation

/// A selection is tied to both durable media evidence and this attachment session.
/// Names and BSD addresses are descriptive only: neither can replace missing UUIDs.
public struct TestVolumeIdentity: Equatable {
    public let partitionUUID: String?
    public let mediaUUID: String?
    public let capacityBytes: Int64
    public let sessionID: UUID
    public let bsdName: String
    public let parentBSDName: String?

    public init(partitionUUID: String?, mediaUUID: String?, capacityBytes: Int64,
                sessionID: UUID, bsdName: String, parentBSDName: String?) {
        self.partitionUUID = Self.canonicalUUID(partitionUUID)
        self.mediaUUID = Self.canonicalUUID(mediaUUID)
        self.capacityBytes = capacityBytes
        self.sessionID = sessionID
        self.bsdName = bsdName
        self.parentBSDName = parentBSDName
    }

    public init(volume: Volume) {
        self.init(partitionUUID: volume.partitionUUID, mediaUUID: volume.mediaUUID,
                  capacityBytes: volume.capacityBytes, sessionID: volume.sessionID,
                  bsdName: volume.bsdName, parentBSDName: volume.parentBSDName)
    }

    public var hasStableIdentity: Bool { partitionUUID != nil && mediaUUID != nil }

    public var hasValidDeviceAddress: Bool {
        guard let parentBSDName,
              parentBSDName.range(of: "^disk[0-9]+$", options: .regularExpression) != nil,
              bsdName.range(of: "^disk[0-9]+s[0-9]+$", options: .regularExpression) != nil
        else { return false }
        return bsdName.hasPrefix(parentBSDName + "s")
    }

    public var isComplete: Bool {
        hasStableIdentity && hasValidDeviceAddress && capacityBytes > 0
    }

    /// Equal incomplete snapshots are still not evidence that a device is the same.
    public func matches(_ current: TestVolumeIdentity) -> Bool {
        isComplete && current.isComplete && self == current
    }

    private static func canonicalUUID(_ value: String?) -> String? {
        guard let value,
              let uuid = UUID(uuidString: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              uuid.uuidString != "00000000-0000-0000-0000-000000000000" else { return nil }
        return uuid.uuidString
    }
}

/// An in-memory description of a currently attached partition, sufficient only for
/// observing its read-only metadata. Registry IDs identify kernel objects, not a
/// durable card; they are valid only for this boot. This type is not Codable and
/// must never be used to authorize writing or persisted as a protected-drive key.
public struct ReadOnlyAttachmentIdentity: Equatable {
    public let identity: TestVolumeIdentity
    public let partitionRegistryID: UInt64?
    public let mediaRegistryID: UInt64?
    public let mediaCapacityBytes: Int64?
    public let blockSizeBytes: Int64?

    public init(volume: Volume) {
        identity = TestVolumeIdentity(volume: volume)
        partitionRegistryID = volume.partitionRegistryID
        mediaRegistryID = volume.mediaRegistryID
        mediaCapacityBytes = volume.mediaCapacityBytes
        blockSizeBytes = volume.blockSizeBytes
    }

    public var isComplete: Bool {
        guard let partitionRegistryID, partitionRegistryID != 0,
              let mediaRegistryID, mediaRegistryID != 0, partitionRegistryID != mediaRegistryID,
              identity.hasValidDeviceAddress, identity.capacityBytes > 0,
              let mediaCapacityBytes, mediaCapacityBytes >= identity.capacityBytes,
              let blockSizeBytes, blockSizeBytes > 0 else { return false }
        return identity.capacityBytes % blockSizeBytes == 0 && mediaCapacityBytes % blockSizeBytes == 0
    }

    /// Includes optional UUIDs, addresses, session, and geometry in the comparison.
    /// Missing UUIDs can be tolerated here; a UUID changing or disappearing cannot.
    public func matches(_ current: ReadOnlyAttachmentIdentity) -> Bool {
        isComplete && current.isComplete && self == current
    }
}

/// Ephemeral user intent for one currently attached test partition. Do not persist this
/// object or carry it across refreshes, detachment, app relaunch, or a changed selection.
/// The confirmation describes disposable media; it grants no permission to write.
public struct LabSelection: Equatable {
    public let identity: TestVolumeIdentity
    public let attachmentIdentity: ReadOnlyAttachmentIdentity
    public let userConfirmedDisposable: Bool

    public init(volume: Volume, userConfirmedDisposable: Bool) {
        identity = TestVolumeIdentity(volume: volume)
        attachmentIdentity = ReadOnlyAttachmentIdentity(volume: volume)
        self.userConfirmedDisposable = userConfirmedDisposable
    }

    public func matches(_ current: Volume) -> Bool {
        let attachment = ReadOnlyAttachmentIdentity(volume: current)
        // Even the UUID route cannot ignore changed registry or geometry evidence.
        guard attachmentIdentity == attachment else { return false }
        return identity.matches(attachment.identity) || attachmentIdentity.matches(attachment)
    }
}

public enum TestMountState: String, Equatable {
    case notMounted
    case readOnly
    case readWrite
    case unknown

    public var title: String {
        switch self {
        case .notMounted: return "Not mounted"
        case .readOnly: return "Mounted read-only"
        case .readWrite: return "Mounted with existing write access"
        case .unknown: return "Mount access unverified"
        }
    }
}

public enum TestPreflightBlocker: String, Identifiable, Equatable {
    case demoMode
    case protectedVolume
    case staleStatus
    case noCurrentVolume
    case noSelection
    case disposableConfirmationRequired
    case selectionChanged
    case missingStableIdentity
    case ambiguousIdentity
    case invalidDeviceAddress
    case unknownCapacity
    case notNTFS
    case externalPhysicalPartitionUnverified
    case notMounted
    case existingWriteAccess
    case mountAccessUnknown

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .demoMode: return "Leave demo mode"
        case .protectedVolume: return "This drive is protected"
        case .staleStatus: return "Current drive status required"
        case .noCurrentVolume: return "Connect the test partition"
        case .noSelection: return "Select a disposable test partition"
        case .disposableConfirmationRequired: return "Confirm the selected card is disposable"
        case .selectionChanged: return "Select the test partition again"
        case .missingStableIdentity: return "Device identity unavailable"
        case .ambiguousIdentity: return "Device identity is not unique"
        case .invalidDeviceAddress: return "Partition address unverified"
        case .unknownCapacity: return "Partition capacity unavailable"
        case .notNTFS: return "NTFS partition required"
        case .externalPhysicalPartitionUnverified: return "External physical partition required"
        case .notMounted: return "Partition is not mounted"
        case .existingWriteAccess: return "Existing write access detected"
        case .mountAccessUnknown: return "Read-only access unverified"
        }
    }

    public var message: String {
        switch self {
        case .demoMode:
            return "Sample drives cannot be enrolled in a real test session."
        case .protectedVolume:
            return "Drives containing files you need to keep are excluded from the test lab."
        case .staleStatus:
            return "Refresh discovery before inspecting the card. An old or incomplete scan is not sufficient."
        case .noCurrentVolume:
            return "The selected partition must be present in the current drive inventory."
        case .noSelection:
            return "Choose a spare NTFS card or drive. A volume label never enrolls a device automatically."
        case .disposableConfirmationRequired:
            return "Confirm that this exact partition contains no files you need to keep."
        case .selectionChanged:
            return "The device identity, size, address, or attachment session has changed. Earlier confirmation has expired."
        case .missingStableIdentity:
            return "Read-only inspection needs a verified UUID pair or complete current-connection registry and size information. Names and disk numbers cannot substitute for this evidence."
        case .ambiguousIdentity:
            return "The current inventory cannot uniquely identify this partition. Duplicate or unverified identity evidence blocks enrollment."
        case .invalidDeviceAddress:
            return "The partition and its physical parent must have matching, verified device addresses."
        case .unknownCapacity:
            return "A positive partition size is required to detect changed or replaced media."
        case .notNTFS:
            return "The reported filesystem must be NTFS; a Microsoft Basic Data partition type is insufficient."
        case .externalPhysicalPartitionUnverified:
            return "Internal disks, whole disks, virtual media, and unknown hardware are excluded from this card test."
        case .notMounted:
            return "This stage inspects an already mounted read-only partition and does not mount devices."
        case .existingWriteAccess:
            return "The mounted partition already reports write access. This stage requires read-only access."
        case .mountAccessUnknown:
            return "The current mounted filesystem must explicitly report read-only access."
        }
    }
}

/// A pure readiness report for read-only inspection, never an operation authorization.
/// It performs no disk I/O and does not assess dirty state, hibernation, encryption,
/// filesystem health, or NTFS engine compatibility. Writing remains disabled even
/// when every inspection prerequisite is satisfied.
public struct TestPreflight: Equatable {
    public let identity: TestVolumeIdentity?
    public let attachmentIdentity: ReadOnlyAttachmentIdentity?
    public let mountState: TestMountState
    public let isNTFS: Bool
    public let isExternalPhysicalPartition: Bool
    public let blockers: [TestPreflightBlocker]

    public var readyForReadOnlyInspection: Bool { blockers.isEmpty }
    public var mayWrite: Bool { false }
    public var usesEphemeralIdentity: Bool {
        identity?.isComplete != true && attachmentIdentity?.isComplete == true
    }

    public static func evaluate(volume: Volume?, selection: LabSelection?, isDemo: Bool,
                                isProtected: Bool, statusIsFresh: Bool,
                                identityIsUnique: Bool) -> Self {
        var blockers: [TestPreflightBlocker] = []
        if isDemo { blockers.append(.demoMode) }
        if isProtected { blockers.append(.protectedVolume) }
        if !statusIsFresh { blockers.append(.staleStatus) }
        if let selection {
            if !selection.userConfirmedDisposable { blockers.append(.disposableConfirmationRequired) }
        } else {
            blockers.append(.noSelection)
        }

        guard let volume else {
            blockers.append(.noCurrentVolume)
            return Self(identity: nil, attachmentIdentity: nil, mountState: .unknown, isNTFS: false,
                        isExternalPhysicalPartition: false, blockers: blockers)
        }

        let identity = TestVolumeIdentity(volume: volume)
        let attachmentIdentity = ReadOnlyAttachmentIdentity(volume: volume)
        if !identity.hasStableIdentity && !attachmentIdentity.isComplete { blockers.append(.missingStableIdentity) }
        if !identityIsUnique { blockers.append(.ambiguousIdentity) }
        if !identity.hasValidDeviceAddress { blockers.append(.invalidDeviceAddress) }
        if identity.capacityBytes <= 0 { blockers.append(.unknownCapacity) }
        if let selection, !selection.matches(volume) { blockers.append(.selectionChanged) }
        if !volume.isNTFS { blockers.append(.notNTFS) }

        let physicalPartition = volume.isExternal && volume.isPhysical == true && !volume.isWhole
        if !physicalPartition { blockers.append(.externalPhysicalPartitionUnverified) }

        let mountState: TestMountState
        if volume.mountPath?.isEmpty != false {
            mountState = .notMounted
            blockers.append(.notMounted)
        } else {
            switch volume.isWritable {
            case .some(false): mountState = .readOnly
            case .some(true):
                mountState = .readWrite
                blockers.append(.existingWriteAccess)
            case nil:
                mountState = .unknown
                blockers.append(.mountAccessUnknown)
            }
        }

        return Self(identity: identity, attachmentIdentity: attachmentIdentity, mountState: mountState, isNTFS: volume.isNTFS,
                    isExternalPhysicalPartition: physicalPartition, blockers: blockers)
    }
}
