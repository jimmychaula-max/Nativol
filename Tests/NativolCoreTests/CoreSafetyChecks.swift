import Foundation
#if !NATIVOL_STANDALONE_CHECKS
@testable import NativolCore
#endif

struct CoreSafetyChecks {
    var all: [(String, () throws -> Void)] {
        [
            ("testFilesystemMustPositivelyIdentifyNTFS", testFilesystemMustPositivelyIdentifyNTFS),
            ("testUnknownExternalStatusIsNeverAssumedExternal", testUnknownExternalStatusIsNeverAssumedExternal),
            ("testDiscoveryRequiresExternalPhysicalPartitionEvidence", testDiscoveryRequiresExternalPhysicalPartitionEvidence),
            ("testMountedWritabilityIsNotInferredFromFilesystemType", testMountedWritabilityIsNotInferredFromFilesystemType),
            ("testNoCurrentConfigurationGrantsWriteAccess", testNoCurrentConfigurationGrantsWriteAccess),
            ("testInvalidVolumesHaveSpecificWriteBlockers", testInvalidVolumesHaveSpecificWriteBlockers),
            ("testMountAndDescriptionChangesReplaceSnapshotsWithoutDuplicates", testMountAndDescriptionChangesReplaceSnapshotsWithoutDuplicates),
            ("testBsdNameReuseDoesNotReuseAttachmentIdentity", testBsdNameReuseDoesNotReuseAttachmentIdentity),
            ("testParentDisappearanceRemovesAllSiblingPartitions", testParentDisappearanceRemovesAllSiblingPartitions),
            ("testParentDisappearanceInvalidatesPartiallyDescribedChildSession", testParentDisappearanceInvalidatesPartiallyDescribedChildSession),
            ("testLosingExternalEvidenceRemovesPreviouslyEligibleVolume", testLosingExternalEvidenceRemovesPreviouslyEligibleVolume),
            ("testRefreshInvalidatesPreviousAttachmentIdentity", testRefreshInvalidatesPreviousAttachmentIdentity)
        ]
    }

    func testFilesystemMustPositivelyIdentifyNTFS() throws {
        for filesystem in ["ntfs", "NTFS", " NTFS\n"] {
            try requireTrue(volume(filesystem: filesystem).isNTFS)
        }
        for filesystem in ["Unknown", "", "Microsoft Basic Data", "exfat", "apfs", "ntfs-like"] {
            try requireFalse(volume(filesystem: filesystem).isNTFS)
        }
    }

    func testUnknownExternalStatusIsNeverAssumedExternal() throws {
        try requireTrue(volume(isInternal: false).isExternal)
        try requireFalse(volume(isInternal: true).isExternal)
        try requireFalse(volume(isInternal: nil).isExternal)
    }

    func testDiscoveryRequiresExternalPhysicalPartitionEvidence() throws {
        try requireTrue(facts().isDiscoverable)
        try requireFalse(facts(isInternal: nil).isDiscoverable)
        try requireFalse(facts(isInternal: true).isDiscoverable)
        try requireFalse(facts(isWhole: true).isDiscoverable)
        try requireFalse(facts(isWhole: nil).isDiscoverable)
        try requireFalse(facts(isMountable: false).isDiscoverable)
        try requireFalse(facts(isMountable: nil).isDiscoverable)
        try requireFalse(facts(isNetwork: true).isDiscoverable)
        for transport: String? in [nil, "", "Disk Image", "Virtual Interface", "unknown", "Network"] {
            try requireFalse(facts(deviceProtocol: transport).isDiscoverable)
        }
        try requireTrue(facts(deviceProtocol: "Thunderbolt").isDiscoverable)
    }

    func testMountedWritabilityIsNotInferredFromFilesystemType() throws {
        try requireEqual(volume(mountPath: nil, isWritable: true).accessLabel, "Not mounted")
        try requireEqual(volume(isWritable: nil).accessLabel, "Access unknown")
        try requireEqual(volume(isWritable: false).accessLabel, "Read-only")
        try requireEqual(volume(isWritable: true).accessLabel, "Read & write · existing driver")
    }

    func testNoCurrentConfigurationGrantsWriteAccess() throws {
        try requireTrue(CertificationManifest.certifiedTargets.isEmpty)
        for architecture in ["x86_64", "arm64", "unknown"] {
            for osVersion in ["12.0", "15.7.9", "26.0", "999.0"] {
                let target = CertificationTarget(macOSVersion: osVersion, architecture: architecture,
                                                 backendIdentifier: "ntfs-3g", backendVersion: "2022.10.3")
                try requireFalse(CertificationManifest.isCertified(target))
                let result = WriteEligibility.evaluate(volume: volume(isWritable: true), certification: target)
                try requireFalse(result.isEligible)
                try requireTrue(result.blockers.contains(.writeBackendUnavailable))
                try requireTrue(result.blockers.contains(.uncertifiedConfiguration))
            }
        }
    }

    func testInvalidVolumesHaveSpecificWriteBlockers() throws {
        let result = WriteEligibility.evaluate(volume: volume(filesystem: "Microsoft Basic Data", isInternal: nil,
                                                              isWhole: true))
        try requireFalse(result.isEligible)
        try requireTrue(result.blockers.contains(.notNTFS))
        try requireTrue(result.blockers.contains(.externalStatusUnverified))
        try requireTrue(result.blockers.contains(.wholeDisk))
    }

    func testMountAndDescriptionChangesReplaceSnapshotsWithoutDuplicates() throws {
        var inventory = VolumeInventory()
        inventory.update(facts())
        let firstID = inventory.volumes.first?.sessionID
        inventory.update(facts(name: "Renamed", mountPath: nil, isWritable: nil))
        try requireEqual(inventory.volumes.count, 1)
        try requireEqual(inventory.volumes.first?.name, "Renamed")
        try requireNil(inventory.volumes.first?.mountPath)
        try requireNil(inventory.volumes.first?.isWritable)
        try requireEqual(inventory.volumes.first?.sessionID, firstID)
    }

    func testBsdNameReuseDoesNotReuseAttachmentIdentity() throws {
        var inventory = VolumeInventory()
        inventory.update(facts())
        let firstID = inventory.volumes.first?.sessionID
        inventory.remove(bsdName: "disk4s1")
        try requireTrue(inventory.volumes.isEmpty)
        inventory.update(facts())
        try requireNotEqual(inventory.volumes.first?.sessionID, firstID)
    }

    func testParentDisappearanceRemovesAllSiblingPartitions() throws {
        var inventory = VolumeInventory()
        inventory.update(facts(bsdName: "disk4s1"))
        inventory.update(facts(bsdName: "disk4s2"))
        inventory.remove(bsdName: "disk4")
        try requireTrue(inventory.volumes.isEmpty)
    }

    func testParentDisappearanceInvalidatesPartiallyDescribedChildSession() throws {
        // Disk Arbitration can briefly omit parent/external metadata before disappearance.
        // Test both a still-discoverable child and one removed from visible inventory.
        for partialExternalEvidence: Bool? in [false, nil] {
            var inventory = VolumeInventory()
            inventory.update(facts())
            let firstID = inventory.volumes.first?.sessionID
            try requireTrue(firstID != nil)
            inventory.update(facts(parentBSDName: nil, isInternal: partialExternalEvidence))
            inventory.remove(bsdName: "disk4")
            try requireTrue(inventory.volumes.isEmpty)
            inventory.update(facts())
            try requireEqual(inventory.volumes.count, 1)
            try requireNotEqual(inventory.volumes.first?.sessionID, firstID)
        }
    }

    func testLosingExternalEvidenceRemovesPreviouslyEligibleVolume() throws {
        var inventory = VolumeInventory()
        inventory.update(facts())
        inventory.update(facts(isInternal: nil))
        try requireTrue(inventory.volumes.isEmpty)
    }

    func testRefreshInvalidatesPreviousAttachmentIdentity() throws {
        var inventory = VolumeInventory()
        inventory.update(facts())
        let firstID = inventory.volumes.first?.sessionID
        inventory.removeAll()
        inventory.update(facts())
        try requireNotEqual(inventory.volumes.first?.sessionID, firstID)
    }

    private func volume(filesystem: String = "ntfs", mountPath: String? = "/Volumes/Test",
                        isInternal: Bool? = false, isWritable: Bool? = false,
                        isWhole: Bool = false) -> Volume {
        Volume(name: "Test", bsdName: "disk4s1", parentBSDName: "disk4", filesystem: filesystem,
               mountPath: mountPath, isInternal: isInternal, isWritable: isWritable, isWhole: isWhole)
    }

    private func facts(bsdName: String = "disk4s1", parentBSDName: String? = "disk4",
                       name: String = "Test", mountPath: String? = "/Volumes/Test",
                       isInternal: Bool? = false, isWhole: Bool? = false,
                       isMountable: Bool? = true, isNetwork: Bool? = false,
                       deviceProtocol: String? = "USB", isWritable: Bool? = false) -> DiskFacts {
        DiskFacts(bsdName: bsdName, parentBSDName: parentBSDName, name: name, filesystem: "ntfs",
                  mountPath: mountPath, capacityBytes: 1_000, isInternal: isInternal,
                  isWhole: isWhole, isMountable: isMountable, isNetwork: isNetwork,
                  deviceProtocol: deviceProtocol, isWritable: isWritable)
    }
}

private struct CheckFailure: Error, CustomStringConvertible {
    let description: String
}

private func requireTrue(_ condition: Bool, file: StaticString = #filePath, line: UInt = #line) throws {
    guard condition else { throw CheckFailure(description: "Expected true at \(file):\(line)") }
}

private func requireFalse(_ condition: Bool, file: StaticString = #filePath, line: UInt = #line) throws {
    guard !condition else { throw CheckFailure(description: "Expected false at \(file):\(line)") }
}

private func requireEqual<T: Equatable>(_ lhs: T, _ rhs: T,
                                         file: StaticString = #filePath, line: UInt = #line) throws {
    guard lhs == rhs else { throw CheckFailure(description: "Expected \(lhs) == \(rhs) at \(file):\(line)") }
}

private func requireNotEqual<T: Equatable>(_ lhs: T, _ rhs: T,
                                            file: StaticString = #filePath, line: UInt = #line) throws {
    guard lhs != rhs else { throw CheckFailure(description: "Expected distinct values at \(file):\(line)") }
}

private func requireNil<T>(_ value: T?, file: StaticString = #filePath, line: UInt = #line) throws {
    guard value == nil else { throw CheckFailure(description: "Expected nil at \(file):\(line)") }
}
