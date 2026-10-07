import Foundation
#if !NATIVOL_STANDALONE_CHECKS
@testable import NativolCore
#endif

struct ExperimentalWriteLeaseChecks {
    private let boot = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!
    private let now = Date(timeIntervalSince1970: 1_790_000_000)

    var all: [(String, () throws -> Void)] {
        [
            ("testExperimentalCardDoesNotChangeReadOnlyOrCertificationGates", testExperimentalCardDoesNotChangeReadOnlyOrCertificationGates),
            ("testExperimentalLeaseRoundTripsNumericIDsAndISO8601Dates", testExperimentalLeaseRoundTripsNumericIDsAndISO8601Dates),
            ("testExperimentalLeaseRejectsBSDReuseAndChangedAttachment", testExperimentalLeaseRejectsBSDReuseAndChangedAttachment),
            ("testExperimentalLeaseExpiresAndCannotReplayAcrossBoots", testExperimentalLeaseExpiresAndCannotReplayAcrossBoots),
            ("testExperimentalLeaseRejectsMalformedLifetimeAndSchema", testExperimentalLeaseRejectsMalformedLifetimeAndSchema),
            ("testExperimentalLeaseRequiresExactConsoleIdentity", testExperimentalLeaseRequiresExactConsoleIdentity),
            ("testExperimentalLeaseBlocksProtectedOrStaleConfirmation", testExperimentalLeaseBlocksProtectedOrStaleConfirmation),
            ("testExperimentalLeaseAcceptsSupportedExternalGeometry", testExperimentalLeaseAcceptsSupportedExternalGeometry),
            ("testExperimentalLeaseRejectsOverflowAndIncompleteTopology", testExperimentalLeaseRejectsOverflowAndIncompleteTopology),
            ("testExperimentalLeaseDoesNotIgnoreOptionalUUIDChanges", testExperimentalLeaseDoesNotIgnoreOptionalUUIDChanges),
            ("testExperimentalLeaseAllowsControlledUnmountButNotEnrollmentUnmounted", testExperimentalLeaseAllowsControlledUnmountButNotEnrollmentUnmounted),
            ("testExperimentalLeaseRejectsForgedRequestEvidence", testExperimentalLeaseRejectsForgedRequestEvidence)
        ]
    }

    func testExperimentalCardDoesNotChangeReadOnlyOrCertificationGates() throws {
        let volume = card()
        let lease = try enroll(volume)
        try require(validate(lease, volume).isEmpty, "The exact disposable MBR card should enroll without UUIDs")
        try require(!TestVolumeIdentity(volume: volume).hasStableIdentity, "Registry evidence is not a durable UUID")
        let inspection = TestPreflight.evaluate(volume: volume,
            selection: LabSelection(volume: volume, userConfirmedDisposable: true), isDemo: false,
            isProtected: false, statusIsFresh: true, identityIsUnique: true)
        try require(inspection.readyForReadOnlyInspection && !inspection.mayWrite,
                    "Read-only inspection still cannot authorize writes")
        try require(!WriteEligibility.evaluate(volume: volume).isEligible && CertificationManifest.certifiedTargets.isEmpty,
                    "Experimental enrollment must not claim certification or enable general writing")
    }

    func testExperimentalLeaseRoundTripsNumericIDsAndISO8601Dates() throws {
        let lease = try enroll(card())
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(lease.request)
        let object = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        try require(object["partitionRegistryID"] is NSNumber && object["mediaRegistryID"] is NSNumber,
                    "Registry IDs must remain JSON numbers for the helper contract")
        try require(object["createdAt"] is String && object["expiresAt"] is String, "Dates must be ISO8601 strings")
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let decoded = try decoder.decode(ExperimentalWriteRequest.self, from: data)
        try require(decoded == lease.request, "Wire round-trip must preserve identity and lifetime")
        try require(decoded.requestedGID == 20, "The ordinary staff group must be accepted")
    }

    func testExperimentalLeaseRejectsBSDReuseAndChangedAttachment() throws {
        let lease = try enroll(card())
        for changed in [card(child: 999), card(parent: 998), card(session: UUID()),
                        card(bsd: "disk5s1", parentBSD: "disk5"),
                        card(offset: 512), card(block: 4096), card(parentBlock: 4096)] {
            try require(validate(lease, changed).contains(.identityChanged), "BSD reuse or geometry change must invalidate enrollment")
        }
        try require(validate(lease, nil).contains(.noCurrentVolume), "A disconnected card cannot reuse a valid lease")
    }

    func testExperimentalLeaseExpiresAndCannotReplayAcrossBoots() throws {
        let lease = try enroll(card())
        try require(validate(lease, card(), date: now.addingTimeInterval(119)).isEmpty, "Lease should be valid within its short lifetime")
        try require(validate(lease, card(), date: now.addingTimeInterval(120)).contains(.expired), "Expiry boundary must be closed")
        try require(validate(lease, card(), bootID: UUID()).contains(.bootChanged), "Boot changes invalidate registry IDs")
        try require(validate(lease, card(), consumed: true).contains(.operationConsumed), "A consumed operation cannot be replayed")
    }

    func testExperimentalLeaseRejectsMalformedLifetimeAndSchema() throws {
        let lease = try enroll(card())
        let formatter = ISO8601DateFormatter()
        for values: [String: Any] in [
            ["expiresAt": formatter.string(from: now.addingTimeInterval(121))],
            ["expiresAt": formatter.string(from: now)],
            ["createdAt": formatter.string(from: now.addingTimeInterval(1))]
        ] {
            try require(validate(try mutate(lease, values), card()).contains(.invalidLifetime), "Malformed or future lifetime must fail")
        }
        try require(validate(try mutate(lease, ["schemaVersion": 2]), card()).contains(.unsupportedSchema), "Unknown schemas cannot authorize operations")
        try require(validate(try mutate(lease, ["operationId": "00000000-0000-0000-0000-000000000000"]), card()).contains(.invalidOperation), "Missing nonce evidence must fail")
    }

    func testExperimentalLeaseRequiresExactConsoleIdentity() throws {
        let lease = try enroll(card())
        for values: [String: Any] in [["requestedUID": 0], ["requestedUID": 502], ["requestedGID": 0], ["requestedGID": 21]] {
            try require(validate(try mutate(lease, values), card()).contains(.invalidUser), "A request cannot redirect ownership to another user/group")
        }
        do {
            _ = try ExperimentalWriteLease.enroll(volume: card(), bootSessionUUID: boot, requestedUID: 0,
                requestedGID: 20, now: now, isProtected: false, statusIsFresh: true,
                identityIsUnique: true, userAuthorizedWriting: true)
            throw Failure(message: "Root must not enroll")
        } catch let error as ExperimentalWriteLeaseError {
            try require(error.blockers.contains(.invalidUser), "Root enrollment must explain the identity block")
        }
    }

    func testExperimentalLeaseBlocksProtectedOrStaleConfirmation() throws {
        for (protected, fresh, unique, confirmed, blocker) in [
            (true, true, true, true, ExperimentalWriteBlocker.protectedVolume),
            (false, false, true, true, .staleStatus),
            (false, true, false, true, .ambiguousIdentity),
            (false, true, true, false, .writeAuthorizationRequired)
        ] {
            do {
                _ = try ExperimentalWriteLease.enroll(volume: card(), bootSessionUUID: boot,
                    requestedUID: 501, requestedGID: 20, now: now, isProtected: protected,
                    statusIsFresh: fresh, identityIsUnique: unique, userAuthorizedWriting: confirmed)
                throw Failure(message: "Unsafe context was enrolled")
            } catch let error as ExperimentalWriteLeaseError {
                try require(error.blockers.contains(blocker), "Missing explicit context blocker")
            }
        }
        let lease = try enroll(card())
        try require(validate(lease, card(), protected: true).contains(.protectedVolume), "New protection overrides previous confirmation")
        try require(validate(try mutate(lease, ["protectedVolume": true]), card()).contains(.protectedVolume), "Client protection cannot be ignored")
    }

    func testExperimentalLeaseAcceptsSupportedExternalGeometry() throws {
        for current in [card(capacity: 1_500_000_000_000, whole: 1_500_004_194_304),
                        card(transport: "Thunderbolt"), card(map: "GUID_partition_scheme"),
                        card(offset: 1_048_576, capacity: 8_589_934_592, whole: 8_590_983_168,
                             block: 4096, parentBlock: 4096)] {
            let lease = try enroll(current)
            try require(validate(lease, current).isEmpty, "Supported external geometry should make a fresh request")
            try require(validate(lease, current, protected: true).contains(.protectedVolume),
                        "Explicit protection must override eligibility for every capacity")
        }
        for current in [card(transport: "Disk Image"), card(map: "Apple_partition_scheme"),
                        card(block: 1024, parentBlock: 1024), card(block: 512, parentBlock: 4096),
                        card(internalDisk: true), card(internalDisk: nil), card(physical: nil),
                        card(wholeDisk: true), card(filesystem: "exfat")] {
            try requireEnrollmentBlocked(current)
        }
    }

    func testExperimentalLeaseRejectsOverflowAndIncompleteTopology() throws {
        for current in [card(child: nil), card(child: 0), card(parent: nil), card(parent: 0), card(child: 1000),
                        card(offset: nil), card(offset: -1), card(offset: 0), card(offset: Int64.max),
                        card(capacity: Int64.max, whole: Int64.max), card(capacity: 513),
                        card(whole: nil), card(whole: 1), card(block: 0), card(parentBlock: nil),
                        card(related: nil), card(related: false), card(count: nil), card(count: 2),
                        card(parentBSD: "disk5"), card(bsd: "/dev/disk4s1"),
                        card(partitionUUID: "not-a-uuid"), card(mediaUUID: ""),
                        card(session: UUID(uuidString: "00000000-0000-0000-0000-000000000000")!)] {
            try requireEnrollmentBlocked(current)
        }
    }

    func testExperimentalLeaseDoesNotIgnoreOptionalUUIDChanges() throws {
        let known = "40000000-0000-0000-0000-000000000001"
        let lease = try enroll(card(partitionUUID: known))
        for current in [card(), card(partitionUUID: UUID().uuidString),
                        card(partitionUUID: known, mediaUUID: UUID().uuidString)] {
            try require(validate(lease, current).contains(.identityChanged), "Optional UUID evidence must remain unchanged")
        }
    }

    func testExperimentalLeaseAllowsControlledUnmountButNotEnrollmentUnmounted() throws {
        let lease = try enroll(card())
        try require(validate(lease, card(mounted: false, writable: nil)).isEmpty,
                    "A controlled unmount must preserve attachment identity for helper revalidation")
        try requireEnrollmentBlocked(card(mounted: false))
        try requireEnrollmentBlocked(card(writable: true))
        try requireEnrollmentBlocked(card(writable: nil))
    }

    func testExperimentalLeaseRejectsForgedRequestEvidence() throws {
        let lease = try enroll(card())
        for values: [String: Any] in [["partitionOffsetBytes": 0], ["parentBlockSizeBytes": 4096],
            ["parentRelationVerified": false], ["partitionCount": 2], ["deviceProtocol": "USB;echo x"],
            ["partitionMap": "GUID_partition_scheme"], ["bsdName": "disk4s1;echo x"],
            ["partitionRegistryID": 0], ["mediaRegistryID": 1001]] {
            try require(validate(try mutate(lease, values), card()).contains(.identityChanged), "Forged request evidence must not match fresh metadata")
        }
    }

    private func enroll(_ volume: Volume) throws -> ExperimentalWriteLease {
        try ExperimentalWriteLease.enroll(volume: volume, bootSessionUUID: boot, requestedUID: 501,
            requestedGID: 20, now: now, isProtected: false, statusIsFresh: true,
            identityIsUnique: true, userAuthorizedWriting: true)
    }
    private func validate(_ lease: ExperimentalWriteLease, _ volume: Volume?, date: Date? = nil,
                          bootID: UUID? = nil, consumed: Bool = false, protected: Bool = false) -> [ExperimentalWriteBlocker] {
        lease.validate(current: volume, bootSessionUUID: bootID ?? boot, requestedUID: 501,
            requestedGID: 20, now: date ?? now, isProtected: protected, statusIsFresh: true,
            identityIsUnique: true, operationConsumed: consumed)
    }
    private func requireEnrollmentBlocked(_ volume: Volume) throws {
        do { _ = try enroll(volume); throw Failure(message: "Unsafe attachment was enrolled") }
        catch is ExperimentalWriteLeaseError { }
    }
    private func mutate(_ lease: ExperimentalWriteLease, _ values: [String: Any]) throws -> ExperimentalWriteLease {
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        var object = try JSONSerialization.jsonObject(with: encoder.encode(lease.request)) as! [String: Any]
        for (key, value) in values { object[key] = value }
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        return ExperimentalWriteLease(request: try decoder.decode(ExperimentalWriteRequest.self,
            from: JSONSerialization.data(withJSONObject: object)))
    }
    private func card(bsd: String = "disk4s1", parentBSD: String? = "disk4",
        session: UUID = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!,
        child: UInt64? = 1001, parent: UInt64? = 1000, offset: Int64? = 4_194_304,
        capacity: Int64 = 4_004_511_744, whole: Int64? = 4_008_706_048, block: Int64? = 512,
        parentBlock: Int64? = 512, transport: String? = "USB", map: String? = "FDisk_partition_scheme",
        related: Bool? = true, count: Int? = 1, internalDisk: Bool? = false, physical: Bool? = true,
        wholeDisk: Bool = false, filesystem: String = "ntfs", mounted: Bool = true, writable: Bool? = false,
        partitionUUID: String? = nil, mediaUUID: String? = nil) -> Volume {
        Volume(name: "Any label", bsdName: bsd, parentBSDName: parentBSD, filesystem: filesystem,
            mountPath: mounted ? "/Volumes/Any label" : nil, capacityBytes: capacity,
            isInternal: internalDisk, isWritable: writable, isWhole: wholeDisk, sessionID: session,
            partitionUUID: partitionUUID, mediaUUID: mediaUUID, isPhysical: physical,
            partitionRegistryID: child, mediaRegistryID: parent, mediaCapacityBytes: whole, blockSizeBytes: block,
            partitionOffsetBytes: offset, parentBlockSizeBytes: parentBlock, deviceProtocol: transport,
            partitionMap: map, parentRelationVerified: related, partitionCount: count)
    }
    private struct Failure: Error { let message: String }
    private func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw Failure(message: message) }
    }
}

#if canImport(XCTest) && !NATIVOL_STANDALONE_CHECKS
import XCTest
final class ExperimentalWriteLeaseTests: XCTestCase {
    func testExperimentalWriteLeaseChecks() throws {
        for (name, check) in ExperimentalWriteLeaseChecks().all {
            do { try check() } catch { XCTFail("\(name): \(error)") }
        }
    }
}
#endif
