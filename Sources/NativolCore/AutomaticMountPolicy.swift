import Foundation

/// Attempt at most once for each kernel attachment in this boot. Persisting this
/// state prevents refreshes, app restarts and Stop Writing from remounting a card.
/// These keys suppress work; they never establish a persistent device identity.
public struct AutomaticMountPolicy: Codable, Equatable {
    /// A new key deliberately does not inherit the earlier one-card setting.
    public static let enabledPreferenceKey = "AutomaticExternalNTFSWritingEnabled"
    public let bootSessionUUID: UUID
    public private(set) var attemptedAttachments: Set<String>
    public static let maximumAttemptsPerBoot = 256

    public init(bootSessionUUID: UUID, previous: Self? = nil) {
        self.bootSessionUUID = bootSessionUUID
        attemptedAttachments = previous?.bootSessionUUID == bootSessionUUID
            ? previous!.attemptedAttachments : []
    }

    public static func attachmentKey(partition: UInt64?, media: UInt64?) -> String? {
        guard let partition, let media, partition > 0, media > 0, partition != media else { return nil }
        return "\(partition):\(media)"
    }

    public func mayAttempt(_ key: String) -> Bool {
        !attemptedAttachments.contains(key) && attemptedAttachments.count < Self.maximumAttemptsPerBoot
    }

    /// Call before any asynchronous operation, including a manual stop or eject.
    @discardableResult public mutating func suppress(_ key: String) -> Bool {
        guard mayAttempt(key) else { return false }
        attemptedAttachments.insert(key)
        return true
    }

    /// Call with freshly eligible volumes. Starts are serialized by the caller;
    /// independently mounted media do not prevent another device from starting.
    /// Duplicate partition, address, or parent identity blocks the entire group.
    public func nextCandidate(from volumes: [Volume], blockedMediaIDs: Set<UInt64>) -> Volume? {
        volumes.sorted { $0.bsdName < $1.bsdName }.first { volume in
            guard let media = volume.mediaRegistryID, !blockedMediaIDs.contains(media),
                  let key = Self.attachmentKey(partition: volume.partitionRegistryID, media: media),
                  mayAttempt(key) else { return false }
            return volumes.filter { $0.mediaRegistryID == media }.count == 1
                && volumes.filter { $0.partitionRegistryID == volume.partitionRegistryID }.count == 1
                && volumes.filter { $0.bsdName == volume.bsdName }.count == 1
        }
    }

    /// This is a conservative exclusion fingerprint, never permission to write.
    /// On UUID-less media it can also exclude another drive with equal geometry.
    public static func exclusionKey(for volume: Volume) -> String? {
        if let uuid = TestVolumeIdentity(volume: volume).partitionUUID { return "uuid:" + uuid }
        return geometryExclusionKey(for: volume)
    }

    /// Retain both available signals so a change in UUID metadata availability
    /// cannot silently drop a remembered read-only preference after reconnect.
    public static func exclusionKeys(for volume: Volume) -> Set<String> {
        Set([TestVolumeIdentity(volume: volume).partitionUUID.map { "uuid:" + $0 },
             geometryExclusionKey(for: volume)].compactMap { $0 })
    }

    private static func geometryExclusionKey(for volume: Volume) -> String? {
        guard let offset = volume.partitionOffsetBytes, offset >= 0,
              let whole = volume.mediaCapacityBytes, whole > 0,
              let block = volume.blockSizeBytes, [512, 4096].contains(block),
              let parentBlock = volume.parentBlockSizeBytes, parentBlock == block,
              let transport = volume.deviceProtocol, ["USB", "Thunderbolt"].contains(transport),
              let map = volume.partitionMap, ["FDisk_partition_scheme", "GUID_partition_scheme"].contains(map),
              volume.capacityBytes > 0, volume.capacityBytes <= whole,
              offset <= whole - volume.capacityBytes,
              offset % block == 0, volume.capacityBytes % block == 0, whole % block == 0 else { return nil }
        return "geometry-v1:\(offset):\(volume.capacityBytes):\(whole):\(block):\(transport):\(map)"
    }
}
