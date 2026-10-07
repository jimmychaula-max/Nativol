import Foundation
#if !NATIVOL_STANDALONE_CHECKS
@testable import NativolCore
#endif

/// Shared by XCTest and the standalone command-line-tools runner.
struct TestSessionChecks {
    var all: [(String, () throws -> Void)] {
        [
            ("testReadOnlyEnrollmentNeverGrantsWriteAccess", testReadOnlyEnrollmentNeverGrantsWriteAccess),
            ("testSelectionExpiresAfterDetachOrRefresh", testSelectionExpiresAfterDetachOrRefresh),
            ("testReusedBsdNameCannotSelectReplacementMedia", testReusedBsdNameCannotSelectReplacementMedia),
            ("testMissingOrInvalidUUIDsCannotMatchThemselves", testMissingOrInvalidUUIDsCannotMatchThemselves),
            ("testChangedCapacityOrTopologyInvalidatesSelection", testChangedCapacityOrTopologyInvalidatesSelection),
            ("testNamesNeverEnrollOrIdentifyTestMedia", testNamesNeverEnrollOrIdentifyTestMedia),
            ("testFreshStatusAndUniqueIdentityRequired", testFreshStatusAndUniqueIdentityRequired),
            ("testDemoAndProtectedDrivesCannotEnroll", testDemoAndProtectedDrivesCannotEnroll),
            ("testFilesystemAndPhysicalEvidenceRequired", testFilesystemAndPhysicalEvidenceRequired),
            ("testOnlyExplicitMountedReadOnlyAccessPasses", testOnlyExplicitMountedReadOnlyAccessPasses),
            ("testConfirmationBelongsToOneSelection", testConfirmationBelongsToOneSelection),
            ("testDisappearanceCannotReusePreviousReport", testDisappearanceCannotReusePreviousReport),
            ("testCanonicalUUIDsCompareWithoutNameDependence", testCanonicalUUIDsCompareWithoutNameDependence),
            ("testCurrentConnectionCanInspectAnMBRCardWithoutWeakeningStableIdentity", testCurrentConnectionCanInspectAnMBRCardWithoutWeakeningStableIdentity),
            ("testConnectionSelectionRejectsReplacementAndGeometryChanges", testConnectionSelectionRejectsReplacementAndGeometryChanges),
            ("testConnectionIdentityRequiresDistinctMediaObjectsAndCompleteGeometry", testConnectionIdentityRequiresDistinctMediaObjectsAndCompleteGeometry),
            ("testConnectionIdentityDoesNotIgnoreOptionalUUIDChanges", testConnectionIdentityDoesNotIgnoreOptionalUUIDChanges),
            ("testStableUUIDSelectionAlsoRejectsChangedRegistryEvidence", testStableUUIDSelectionAlsoRejectsChangedRegistryEvidence)
        ]
    }

    func testReadOnlyEnrollmentNeverGrantsWriteAccess() throws {
        let volume = card()
        let report = evaluate(volume)
        try verify(report.readyForReadOnlyInspection, "A fully verified read-only test partition should pass inspection preflight")
        try verify(report.mountState == .readOnly, "Report must show current access")
        try verify(!report.usesEphemeralIdentity, "A complete UUID pair keeps the stable identity route")
        try verify(!report.mayWrite, "Read-only enrollment must never authorize writes")
        try verify(!WriteEligibility.evaluate(volume: volume).isEligible, "The write gate remains closed after enrollment")
    }

    func testSelectionExpiresAfterDetachOrRefresh() throws {
        let selection = LabSelection(volume: card(), userConfirmedDisposable: true)
        let reattached = card(sessionID: UUID())
        try expectBlock(.selectionChanged, evaluate(reattached, selection: selection))
        try verify(!selection.matches(reattached), "Reattached media needs new explicit selection even if UUIDs and device address remain equal")
    }

    func testReusedBsdNameCannotSelectReplacementMedia() throws {
        let selection = LabSelection(volume: card(), userConfirmedDisposable: true)
        for replacement in [
            card(partitionUUID: "90000000-0000-0000-0000-000000000001"),
            card(mediaUUID: "90000000-0000-0000-0000-000000000002"),
            card(sessionID: UUID())
        ] {
            try expectBlock(.selectionChanged, evaluate(replacement, selection: selection))
        }
    }

    func testMissingOrInvalidUUIDsCannotMatchThemselves() throws {
        for uuid: String? in [nil, "", "disk4s1", "NATIVOL_TEST", "00000000-0000-0000-0000-000000000000"] {
            for volume in [card(partitionUUID: uuid), card(mediaUUID: uuid)] {
                let identity = TestVolumeIdentity(volume: volume)
                try verify(!identity.matches(identity), "Equal incomplete identities cannot prove identity")
                try expectBlock(.missingStableIdentity, evaluate(volume))
            }
        }
        try expectBlock(.unknownCapacity, evaluate(card(capacityBytes: 0)))
    }

    func testChangedCapacityOrTopologyInvalidatesSelection() throws {
        let selection = LabSelection(volume: card(), userConfirmedDisposable: true)
        for current in [
            card(capacityBytes: 2_000),
            card(bsdName: "disk5s1", parentBSDName: "disk5"),
            card(parentBSDName: "disk5")
        ] {
            try expectBlock(.selectionChanged, evaluate(current, selection: selection))
        }
        for current in [
            card(parentBSDName: nil),
            card(parentBSDName: "disk5"),
            card(bsdName: "/dev/disk4s1"),
            card(bsdName: "disk4s1;touch /tmp/x")
        ] {
            try expectBlock(.invalidDeviceAddress, evaluate(current))
        }
    }

    func testNamesNeverEnrollOrIdentifyTestMedia() throws {
        let original = card(name: "NATIVOL_TEST")
        let selection = LabSelection(volume: original, userConfirmedDisposable: true)
        let renamed = card(name: "A different name")
        try verify(evaluate(renamed, selection: selection).readyForReadOnlyInspection, "A rename alone does not change device identity")
        let unselected = TestPreflight.evaluate(volume: original, selection: nil, isDemo: false,
                                               isProtected: false, statusIsFresh: true, identityIsUnique: true)
        try expectBlock(.noSelection, unselected)
        try expectBlock(.selectionChanged, evaluate(card(name: "NATIVOL_TEST", mediaUUID: "90000000-0000-0000-0000-000000000002"), selection: selection))
    }

    func testFreshStatusAndUniqueIdentityRequired() throws {
        let volume = card()
        let selection = LabSelection(volume: volume, userConfirmedDisposable: true)
        let stale = TestPreflight.evaluate(volume: volume, selection: selection, isDemo: false,
                                         isProtected: false, statusIsFresh: false, identityIsUnique: true)
        try expectBlock(.staleStatus, stale)
        let duplicate = TestPreflight.evaluate(volume: volume, selection: selection, isDemo: false,
                                              isProtected: false, statusIsFresh: true, identityIsUnique: false)
        try expectBlock(.ambiguousIdentity, duplicate)
    }

    func testDemoAndProtectedDrivesCannotEnroll() throws {
        let volume = card()
        let selection = LabSelection(volume: volume, userConfirmedDisposable: true)
        let demo = TestPreflight.evaluate(volume: volume, selection: selection, isDemo: true,
                                         isProtected: false, statusIsFresh: true, identityIsUnique: true)
        try expectBlock(.demoMode, demo)
        let protected = TestPreflight.evaluate(volume: volume, selection: selection, isDemo: false,
                                              isProtected: true, statusIsFresh: true, identityIsUnique: true)
        try expectBlock(.protectedVolume, protected)
    }

    func testFilesystemAndPhysicalEvidenceRequired() throws {
        for volume in [card(filesystem: "exfat"), card(filesystem: "Microsoft Basic Data"), card(filesystem: "")] {
            try expectBlock(.notNTFS, evaluate(volume))
        }
        for volume in [card(isInternal: nil), card(isInternal: true), card(isWhole: true),
                       card(isPhysical: nil), card(isPhysical: false)] {
            try expectBlock(.externalPhysicalPartitionUnverified, evaluate(volume))
        }
    }

    func testOnlyExplicitMountedReadOnlyAccessPasses() throws {
        let selected = LabSelection(volume: card(), userConfirmedDisposable: true)
        let unmounted = evaluate(card(mountPath: nil), selection: selected)
        try expectBlock(.notMounted, unmounted)
        try verify(unmounted.mountState == .notMounted, "Unmounted volume must not inherit its old mount status")
        try expectBlock(.notMounted, evaluate(card(mountPath: ""), selection: selected))
        let unknown = evaluate(card(isWritable: nil), selection: selected)
        try expectBlock(.mountAccessUnknown, unknown)
        try verify(unknown.mountState == .unknown, "Unknown access stays unknown")
        let writable = evaluate(card(isWritable: true), selection: selected)
        try expectBlock(.existingWriteAccess, writable)
        try verify(writable.mountState == .readWrite, "Existing writer must be reported accurately")
    }

    func testConfirmationBelongsToOneSelection() throws {
        let volume = card()
        let unconfirmed = LabSelection(volume: volume, userConfirmedDisposable: false)
        try expectBlock(.disposableConfirmationRequired, evaluate(volume, selection: unconfirmed))
        let anotherCard = card(partitionUUID: "90000000-0000-0000-0000-000000000001")
        let confirmed = LabSelection(volume: volume, userConfirmedDisposable: true)
        try expectBlock(.selectionChanged, evaluate(anotherCard, selection: confirmed))
    }

    func testDisappearanceCannotReusePreviousReport() throws {
        let volume = card()
        let selection = LabSelection(volume: volume, userConfirmedDisposable: true)
        let disappeared = TestPreflight.evaluate(volume: nil, selection: selection, isDemo: false,
                                                isProtected: false, statusIsFresh: true, identityIsUnique: true)
        try expectBlock(.noCurrentVolume, disappeared)
        try verify(disappeared.identity == nil, "No previous identity should be represented as current")
        try verify(disappeared.mountState == .unknown, "No previous mount status should be represented as current")
    }

    func testCanonicalUUIDsCompareWithoutNameDependence() throws {
        let upper = card(partitionUUID: "ABCDEF00-0000-0000-0000-000000000001")
        let lower = card(partitionUUID: " abcdef00-0000-0000-0000-000000000001\n")
        let selected = LabSelection(volume: upper, userConfirmedDisposable: true)
        try verify(evaluate(lower, selection: selected).readyForReadOnlyInspection, "UUID formatting should not create a false replacement")
    }

    func testCurrentConnectionCanInspectAnMBRCardWithoutWeakeningStableIdentity() throws {
        let volume = connectionCard()
        let stableIdentity = TestVolumeIdentity(volume: volume)
        try verify(!stableIdentity.hasStableIdentity && !stableIdentity.isComplete,
                   "Registry evidence must not masquerade as durable UUID identity")
        try verify(!stableIdentity.matches(stableIdentity), "Strict UUID matching remains closed without UUIDs")
        let report = evaluate(volume)
        try verify(report.readyForReadOnlyInspection && report.usesEphemeralIdentity,
                   "Complete attachment evidence should permit metadata-only inspection of an MBR card")
        try verify(!report.mayWrite && !WriteEligibility.evaluate(volume: volume).isEligible,
                   "An ephemeral connection must never qualify for physical writes")
    }

    func testConnectionSelectionRejectsReplacementAndGeometryChanges() throws {
        let selection = LabSelection(volume: connectionCard(), userConfirmedDisposable: true)
        for current in [
            connectionCard(sessionID: UUID()),
            connectionCard(partitionRegistryID: 2001),
            connectionCard(mediaRegistryID: 2000),
            connectionCard(capacityBytes: 2_097_152),
            connectionCard(mediaCapacityBytes: 16_777_216),
            connectionCard(blockSizeBytes: 4096)
        ] {
            try expectBlock(.selectionChanged, evaluate(current, selection: selection))
        }
    }

    func testConnectionIdentityRequiresDistinctMediaObjectsAndCompleteGeometry() throws {
        for current in [
            connectionCard(partitionRegistryID: nil),
            connectionCard(partitionRegistryID: 0),
            connectionCard(mediaRegistryID: nil),
            connectionCard(mediaRegistryID: 0),
            connectionCard(partitionRegistryID: 1000),
            connectionCard(capacityBytes: 0),
            connectionCard(capacityBytes: 1025),
            connectionCard(mediaCapacityBytes: nil),
            connectionCard(mediaCapacityBytes: 0),
            connectionCard(mediaCapacityBytes: -512),
            connectionCard(mediaCapacityBytes: 512),
            connectionCard(mediaCapacityBytes: 8_388_609),
            connectionCard(blockSizeBytes: nil),
            connectionCard(blockSizeBytes: 0),
            connectionCard(blockSizeBytes: -512)
        ] {
            let identity = ReadOnlyAttachmentIdentity(volume: current)
            try verify(!identity.isComplete && !identity.matches(identity),
                       "Incomplete or contradictory connection evidence cannot identify itself")
            try expectBlock(.missingStableIdentity, evaluate(current))
        }
    }

    func testConnectionIdentityDoesNotIgnoreOptionalUUIDChanges() throws {
        let knownPartitionUUID = "20000000-0000-0000-0000-000000000001"
        let selection = LabSelection(volume: connectionCard(partitionUUID: knownPartitionUUID), userConfirmedDisposable: true)
        for current in [
            connectionCard(partitionUUID: "20000000-0000-0000-0000-000000000002"),
            connectionCard(),
            connectionCard(partitionUUID: knownPartitionUUID, mediaUUID: "30000000-0000-0000-0000-000000000001")
        ] {
            try expectBlock(.selectionChanged, evaluate(current, selection: selection))
        }
    }

    func testStableUUIDSelectionAlsoRejectsChangedRegistryEvidence() throws {
        let partitionUUID = "20000000-0000-0000-0000-000000000001"
        let mediaUUID = "30000000-0000-0000-0000-000000000001"
        let volume = connectionCard(partitionUUID: partitionUUID, mediaUUID: mediaUUID)
        let selection = LabSelection(volume: volume, userConfirmedDisposable: true)
        try verify(evaluate(volume, selection: selection).readyForReadOnlyInspection, "Stable UUID route remains supported")
        let replacement = connectionCard(partitionUUID: partitionUUID, mediaUUID: mediaUUID, partitionRegistryID: 2001)
        try verify(TestVolumeIdentity(volume: volume).matches(TestVolumeIdentity(volume: replacement)),
                   "Strict UUID type remains unchanged; the lab adds current-connection evidence")
        try expectBlock(.selectionChanged, evaluate(replacement, selection: selection))
    }

    private func connectionCard(sessionID: UUID = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!,
                                partitionUUID: String? = nil, mediaUUID: String? = nil,
                                partitionRegistryID: UInt64? = 1001, mediaRegistryID: UInt64? = 1000,
                                capacityBytes: Int64 = 1_048_576, mediaCapacityBytes: Int64? = 8_388_608,
                                blockSizeBytes: Int64? = 512) -> Volume {
        Volume(name: "Spare MBR Card", bsdName: "disk4s1", parentBSDName: "disk4", filesystem: "ntfs",
               mountPath: "/Volumes/Spare MBR Card", capacityBytes: capacityBytes,
               isInternal: false, isWritable: false, sessionID: sessionID,
               partitionUUID: partitionUUID, mediaUUID: mediaUUID, isPhysical: true,
               partitionRegistryID: partitionRegistryID, mediaRegistryID: mediaRegistryID,
               mediaCapacityBytes: mediaCapacityBytes, blockSizeBytes: blockSizeBytes)
    }

    private func evaluate(_ volume: Volume, selection: LabSelection? = nil) -> TestPreflight {
        TestPreflight.evaluate(volume: volume,
                               selection: selection ?? LabSelection(volume: volume, userConfirmedDisposable: true),
                               isDemo: false, isProtected: false, statusIsFresh: true, identityIsUnique: true)
    }

    private func card(name: String = "NATIVOL_TEST", bsdName: String = "disk4s1", parentBSDName: String? = "disk4",
                      filesystem: String = "ntfs", mountPath: String? = "/Volumes/NATIVOL_TEST",
                      capacityBytes: Int64 = 1_000, isInternal: Bool? = false, isWritable: Bool? = false,
                      isWhole: Bool = false, sessionID: UUID = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!,
                      partitionUUID: String? = "20000000-0000-0000-0000-000000000001",
                      mediaUUID: String? = "30000000-0000-0000-0000-000000000001", isPhysical: Bool? = true) -> Volume {
        Volume(name: name, bsdName: bsdName, parentBSDName: parentBSDName, filesystem: filesystem,
               mountPath: mountPath, capacityBytes: capacityBytes, isInternal: isInternal,
               isWritable: isWritable, isWhole: isWhole, sessionID: sessionID,
               partitionUUID: partitionUUID, mediaUUID: mediaUUID, isPhysical: isPhysical)
    }

    private func expectBlock(_ blocker: TestPreflightBlocker, _ report: TestPreflight,
                             file: StaticString = #filePath, line: UInt = #line) throws {
        try verify(report.blockers.contains(blocker), "Expected blocker \(blocker)", file: file, line: line)
        try verify(!report.readyForReadOnlyInspection, "Blocked report cannot be ready", file: file, line: line)
        try verify(!report.mayWrite, "No preflight result may authorize writing", file: file, line: line)
    }

    private func verify(_ condition: Bool, _ message: String,
                        file: StaticString = #filePath, line: UInt = #line) throws {
        guard condition else { throw TestSessionFailure(description: "\(message) at \(file):\(line)") }
    }
}

private struct TestSessionFailure: Error, CustomStringConvertible {
    let description: String
}

#if canImport(XCTest) && !NATIVOL_STANDALONE_CHECKS
import XCTest

final class TestSessionTests: XCTestCase {
    func testReadOnlyPreflightScenarios() throws {
        for (name, check) in TestSessionChecks().all {
            do { try check() }
            catch { XCTFail("\(name): \(error)") }
        }
    }
}
#endif
