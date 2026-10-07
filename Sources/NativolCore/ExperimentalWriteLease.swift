import Foundation

/// Untrusted request data for the separately reviewed personal helper. Possession
/// of this object grants no authority. The helper must re-read identity, bind its
/// device descriptor, check health, and enforce its own protected-drive policy.
/// Callers use JSONEncoder/JSONDecoder with .iso8601 date strategies.
public struct ExperimentalWriteRequest: Codable, Equatable {
    public let schemaVersion: Int
    public let operationId: UUID
    public let bootSessionUUID: UUID
    public let createdAt: Date
    public let expiresAt: Date
    public let bsdName: String
    public let parentBSDName: String
    public let partitionRegistryID: UInt64
    public let mediaRegistryID: UInt64
    public let partitionOffsetBytes: Int64
    public let capacityBytes: Int64
    public let mediaCapacityBytes: Int64
    public let blockSizeBytes: Int64
    public let parentBlockSizeBytes: Int64
    public let partitionUUID: String?
    public let mediaUUID: String?
    public let sessionID: UUID
    public let requestedUID: UInt32
    public let requestedGID: UInt32
    public let deviceProtocol: String
    public let partitionMap: String
    public let parentRelationVerified: Bool
    public let partitionCount: Int
    public let protectedVolume: Bool
}

public enum ExperimentalWriteBlocker: String, Equatable {
    case writeAuthorizationRequired, protectedVolume, staleStatus, ambiguousIdentity
    case noCurrentVolume, unsupportedSchema, invalidOperation, operationConsumed
    case invalidLifetime, expired, bootChanged, identityChanged, invalidUser
    case missingMetadata, invalidGeometry, unsupportedPersonalProfile, externalPhysicalPartitionRequired
    case ntfsRequired, invalidDeviceAddress, parentRelationUnverified, singlePartitionRequired
    case readOnlyMountRequired
}

public struct ExperimentalWriteLeaseError: Error, LocalizedError {
    public let blockers: [ExperimentalWriteBlocker]
    public var errorDescription: String? {
        "Experimental card request blocked: " + blockers.map(\.rawValue).joined(separator: ", ")
    }
}

/// A short-lived request for one currently attached external NTFS partition.
/// It deliberately does not change WriteEligibility or certification.
/// It is not an engine authorization or evidence of NTFS health. No operations
/// are performed here, and the privileged helper must distrust/revalidate it.
public struct ExperimentalWriteLease {
    public static let maximumLifetime: TimeInterval = 120
    public let request: ExperimentalWriteRequest

    /// Wrapping decoded input does not validate or authorize it.
    public init(request: ExperimentalWriteRequest) { self.request = request }

    public static func enroll(volume: Volume, bootSessionUUID: UUID,
                              requestedUID: UInt32, requestedGID: UInt32,
                              now: Date = Date(), isProtected: Bool,
                              statusIsFresh: Bool, identityIsUnique: Bool,
                              userAuthorizedWriting: Bool) throws -> Self {
        var blockers = contextBlockers(isProtected: isProtected, statusIsFresh: statusIsFresh,
                                       identityIsUnique: identityIsUnique)
        if !userAuthorizedWriting { blockers.append(.writeAuthorizationRequired) }
        blockers += volumeBlockers(volume)
        if volume.mountPath?.isEmpty != false || volume.isWritable != false {
            blockers.append(.readOnlyMountRequired)
        }
        if requestedUID < 501 || requestedGID == 0 { blockers.append(.invalidUser) }
        if bootSessionUUID == zeroUUID { blockers.append(.bootChanged) }
        if !now.timeIntervalSince1970.isFinite { blockers.append(.invalidLifetime) }
        guard blockers.isEmpty else { throw ExperimentalWriteLeaseError(blockers: blockers) }
        let identity = TestVolumeIdentity(volume: volume)
        // Complete metadata was required above. Defaults cannot turn missing
        // evidence into a valid profile, even if this method is later extended.
        let request = ExperimentalWriteRequest(schemaVersion: 1, operationId: UUID(),
            bootSessionUUID: bootSessionUUID, createdAt: now, expiresAt: now.addingTimeInterval(maximumLifetime),
            bsdName: volume.bsdName, parentBSDName: volume.parentBSDName ?? "",
            partitionRegistryID: volume.partitionRegistryID ?? 0, mediaRegistryID: volume.mediaRegistryID ?? 0,
            partitionOffsetBytes: volume.partitionOffsetBytes ?? -1, capacityBytes: volume.capacityBytes,
            mediaCapacityBytes: volume.mediaCapacityBytes ?? 0, blockSizeBytes: volume.blockSizeBytes ?? 0,
            parentBlockSizeBytes: volume.parentBlockSizeBytes ?? 0,
            partitionUUID: identity.partitionUUID, mediaUUID: identity.mediaUUID, sessionID: volume.sessionID,
            requestedUID: requestedUID, requestedGID: requestedGID,
            deviceProtocol: volume.deviceProtocol ?? "", partitionMap: volume.partitionMap ?? "",
            parentRelationVerified: volume.parentRelationVerified == true,
            partitionCount: volume.partitionCount ?? 0, protectedVolume: isProtected)
        return Self(request: request)
    }

    /// Validates enrollment against a freshly observed attachment. A controlled
    /// unmount may change the mount path/access while all identity fields stay
    /// equal. The helper separately validates mount state at each operation.
    public func validate(current: Volume?, bootSessionUUID: UUID,
                         requestedUID: UInt32, requestedGID: UInt32,
                         now: Date = Date(), isProtected: Bool,
                         statusIsFresh: Bool, identityIsUnique: Bool,
                         operationConsumed: Bool = false) -> [ExperimentalWriteBlocker] {
        var blockers = Self.contextBlockers(isProtected: isProtected || request.protectedVolume,
                                           statusIsFresh: statusIsFresh, identityIsUnique: identityIsUnique)
        if request.schemaVersion != 1 { blockers.append(.unsupportedSchema) }
        if request.operationId == Self.zeroUUID || request.sessionID == Self.zeroUUID {
            blockers.append(.invalidOperation)
        }
        if operationConsumed { blockers.append(.operationConsumed) }
        let lifetime = request.expiresAt.timeIntervalSince(request.createdAt)
        if !lifetime.isFinite || lifetime <= 0 || lifetime > Self.maximumLifetime
            || !now.timeIntervalSince1970.isFinite || now < request.createdAt {
            blockers.append(.invalidLifetime)
        }
        if now >= request.expiresAt { blockers.append(.expired) }
        if bootSessionUUID == Self.zeroUUID || request.bootSessionUUID != bootSessionUUID {
            blockers.append(.bootChanged)
        }
        if requestedUID < 501 || requestedGID == 0 || request.requestedUID != requestedUID
            || request.requestedGID != requestedGID { blockers.append(.invalidUser) }
        guard let current else { return blockers + [.noCurrentVolume] }
        blockers += Self.volumeBlockers(current)
        let identity = TestVolumeIdentity(volume: current)
        if request.bsdName != current.bsdName || request.parentBSDName != current.parentBSDName
            || request.sessionID != current.sessionID
            || request.partitionRegistryID != current.partitionRegistryID
            || request.mediaRegistryID != current.mediaRegistryID
            || request.partitionOffsetBytes != current.partitionOffsetBytes
            || request.capacityBytes != current.capacityBytes
            || request.mediaCapacityBytes != current.mediaCapacityBytes
            || request.blockSizeBytes != current.blockSizeBytes
            || request.parentBlockSizeBytes != current.parentBlockSizeBytes
            || request.partitionUUID != identity.partitionUUID || request.mediaUUID != identity.mediaUUID
            || request.deviceProtocol != current.deviceProtocol || request.partitionMap != current.partitionMap
            || request.parentRelationVerified != current.parentRelationVerified
            || request.partitionCount != current.partitionCount {
            blockers.append(.identityChanged)
        }
        return blockers
    }

    private static let zeroUUID = UUID(uuidString: "00000000-0000-0000-0000-000000000000")!

    private static func contextBlockers(isProtected: Bool, statusIsFresh: Bool,
                                        identityIsUnique: Bool) -> [ExperimentalWriteBlocker] {
        var blockers: [ExperimentalWriteBlocker] = []
        if isProtected { blockers.append(.protectedVolume) }
        if !statusIsFresh { blockers.append(.staleStatus) }
        if !identityIsUnique { blockers.append(.ambiguousIdentity) }
        return blockers
    }

    private static func volumeBlockers(_ volume: Volume) -> [ExperimentalWriteBlocker] {
        var blockers: [ExperimentalWriteBlocker] = []
        if !volume.isExternal || volume.isPhysical != true || volume.isWhole {
            blockers.append(.externalPhysicalPartitionRequired)
        }
        if !volume.isNTFS { blockers.append(.ntfsRequired) }
        let stableIdentity = TestVolumeIdentity(volume: volume)
        if !stableIdentity.hasValidDeviceAddress { blockers.append(.invalidDeviceAddress) }
        if (volume.partitionUUID != nil && stableIdentity.partitionUUID == nil)
            || (volume.mediaUUID != nil && stableIdentity.mediaUUID == nil) {
            blockers.append(.missingMetadata)
        }
        if volume.sessionID == zeroUUID { blockers.append(.invalidOperation) }
        if volume.parentRelationVerified != true { blockers.append(.parentRelationUnverified) }
        if volume.partitionCount != 1 { blockers.append(.singlePartitionRequired) }
        guard let childID = volume.partitionRegistryID, childID != 0,
              let parentID = volume.mediaRegistryID, parentID != 0, childID != parentID,
              let offset = volume.partitionOffsetBytes, let whole = volume.mediaCapacityBytes,
              let block = volume.blockSizeBytes, let parentBlock = volume.parentBlockSizeBytes,
              let transport = volume.deviceProtocol, let map = volume.partitionMap else {
            return blockers + [.missingMetadata]
        }
        let length = volume.capacityBytes
        // Subtraction after bounds checks avoids offset+length overflow.
        if offset <= 0 || length <= 0 || whole <= 0 || length > whole || offset > whole - length
            || block <= 0 || parentBlock <= 0 {
            blockers.append(.invalidGeometry)
        } else if offset % block != 0 || length % block != 0 || whole % parentBlock != 0
            || offset % parentBlock != 0 || length % parentBlock != 0 {
            blockers.append(.invalidGeometry)
        }
        // Supported transport/topology is separate from filesystem health. The
        // protected helper repeats this policy and inspects the bound device.
        if ![512, 4096].contains(block) || parentBlock != block
            || !["USB", "Thunderbolt"].contains(transport)
            || !["FDisk_partition_scheme", "GUID_partition_scheme"].contains(map) {
            blockers.append(.unsupportedPersonalProfile)
        }
        return blockers
    }
}
