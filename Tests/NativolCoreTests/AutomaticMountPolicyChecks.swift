import Foundation
#if !NATIVOL_STANDALONE_CHECKS
@testable import NativolCore
#endif

struct AutomaticMountPolicyChecks {
    var all: [(String, () throws -> Void)] { [
        ("automaticMountDoesNotRetryStoppedOrFailedAttachmentAfterRelaunch", restart),
        ("automaticMountAllowsReconnectAndClearsOldBootSuppression", reconnect),
        ("automaticMountRequiresCompleteDistinctRegistryIdentity", identity),
        ("automaticMountSuppressionFailsClosedAtCapacity", capacity),
        ("automaticMountQueuesIndependentDrivesWithoutRetryOrStarvation", independentDrives),
        ("automaticMountRejectsAmbiguousMediaAndPartitionIdentity", ambiguousDrives),
        ("writingExclusionSurvivesReconnectWithoutUUID", exclusion),
        ("broadWritingUsesSeparatePreferenceFromPersonalBuild", preference)
    ] }
    private func check(_ condition: Bool) throws {
        if !condition { throw NSError(domain: "AutomaticMountPolicyChecks", code: 1) }
    }
    private func restart() throws {
        let boot = UUID()
        var policy = AutomaticMountPolicy(bootSessionUUID: boot)
        try check(policy.suppress("1001:1000"))
        try check(!policy.suppress("1001:1000"))
        let saved = try JSONEncoder().encode(policy)
        let restored = AutomaticMountPolicy(bootSessionUUID: boot,
            previous: try JSONDecoder().decode(AutomaticMountPolicy.self, from: saved))
        try check(!restored.mayAttempt("1001:1000"))
    }
    private func reconnect() throws {
        var policy = AutomaticMountPolicy(bootSessionUUID: UUID())
        policy.suppress("1001:1000")
        try check(policy.mayAttempt("2001:2000"))
        try check(AutomaticMountPolicy(bootSessionUUID: UUID(), previous: policy).mayAttempt("1001:1000"))
    }
    private func identity() throws {
        try check(AutomaticMountPolicy.attachmentKey(partition: nil, media: 1) == nil)
        try check(AutomaticMountPolicy.attachmentKey(partition: 2, media: nil) == nil)
        try check(AutomaticMountPolicy.attachmentKey(partition: 0, media: 1) == nil)
        try check(AutomaticMountPolicy.attachmentKey(partition: 1, media: 1) == nil)
        try check(AutomaticMountPolicy.attachmentKey(partition: 2, media: 1) == "2:1")
    }
    private func capacity() throws {
        var policy = AutomaticMountPolicy(bootSessionUUID: UUID())
        for index in 0..<AutomaticMountPolicy.maximumAttemptsPerBoot { policy.suppress("\(index + 2):1") }
        try check(!policy.mayAttempt("9999:1"))
        try check(!policy.suppress("9999:1"))
        try check(!policy.mayAttempt("2:1"))
    }

    private func drive(_ bsd: String, child: UInt64, media: UInt64,
                       uuid: String? = nil, offset: Int64 = 1_048_576) -> Volume {
        Volume(name: "Fixture", bsdName: bsd, parentBSDName: String(bsd.split(separator: "s").dropLast().joined(separator: "s")),
               filesystem: "ntfs", capacityBytes: 8_589_934_592, partitionUUID: uuid,
               partitionRegistryID: child, mediaRegistryID: media, mediaCapacityBytes: 8_590_983_168,
               blockSizeBytes: 512, partitionOffsetBytes: offset, parentBlockSizeBytes: 512,
               deviceProtocol: "USB", partitionMap: "FDisk_partition_scheme")
    }
    private func independentDrives() throws {
        let first = drive("disk2s1", child: 21, media: 20), second = drive("disk3s1", child: 31, media: 30)
        var policy = AutomaticMountPolicy(bootSessionUUID: UUID())
        try check(policy.nextCandidate(from: [second, first], blockedMediaIDs: [])?.bsdName == first.bsdName)
        // A failed, stopped, or accepted first attempt is never retried, and
        // does not prevent the other physical device from starting.
        policy.suppress("21:20")
        try check(policy.nextCandidate(from: [first, second], blockedMediaIDs: [20])?.bsdName == second.bsdName)
        policy.suppress("31:30")
        try check(policy.nextCandidate(from: [first, second], blockedMediaIDs: []) == nil)
        let reconnected = drive("disk2s1", child: 41, media: 40)
        try check(policy.nextCandidate(from: [first, second, reconnected], blockedMediaIDs: [20, 30]) == nil)
        try check(policy.nextCandidate(from: [second, reconnected], blockedMediaIDs: [30])?.mediaRegistryID == 40)
    }
    private func ambiguousDrives() throws {
        let policy = AutomaticMountPolicy(bootSessionUUID: UUID())
        let a = drive("disk2s1", child: 21, media: 20)
        for ambiguous in [drive("disk2s2", child: 22, media: 20), drive("disk3s1", child: 21, media: 30),
                          drive("disk2s1", child: 31, media: 30)] {
            try check(policy.nextCandidate(from: [a, ambiguous], blockedMediaIDs: []) == nil)
        }
        try check(policy.nextCandidate(from: [a], blockedMediaIDs: [20]) == nil)
    }
    private func exclusion() throws {
        let initial = drive("disk2s1", child: 21, media: 20)
        let replacementAttachment = drive("disk7s1", child: 71, media: 70)
        let key = AutomaticMountPolicy.exclusionKey(for: initial)
        try check(key != nil && key == AutomaticMountPolicy.exclusionKey(for: replacementAttachment))
        try check(AutomaticMountPolicy.exclusionKey(for: drive("disk2s1", child: 21, media: 20, offset: Int64.max)) == nil)
        let identified = drive("disk2s1", child: 21, media: 20, uuid: "40000000-0000-0000-0000-000000000001")
        try check(AutomaticMountPolicy.exclusionKey(for: identified) == "uuid:40000000-0000-0000-0000-000000000001")
        try check(!AutomaticMountPolicy.exclusionKeys(for: identified).isDisjoint(with: AutomaticMountPolicy.exclusionKeys(for: initial)))
        try check(!AutomaticMountPolicy.exclusionKeys(for: identified).isDisjoint(with: AutomaticMountPolicy.exclusionKeys(for: replacementAttachment)))
    }
    private func preference() throws {
        let suite = "Nativol.AutomaticWritingRegression." + UUID().uuidString
        let preferences = UserDefaults(suiteName: suite)!
        defer { preferences.removePersistentDomain(forName: suite) }
        preferences.set(true, forKey: "AutomaticMountingEnabled")
        try check(!preferences.bool(forKey: AutomaticMountPolicy.enabledPreferenceKey))
        preferences.set(true, forKey: AutomaticMountPolicy.enabledPreferenceKey)
        try check(preferences.bool(forKey: AutomaticMountPolicy.enabledPreferenceKey))
    }
}

#if canImport(XCTest) && !NATIVOL_STANDALONE_CHECKS
import XCTest
final class AutomaticMountPolicyTests: XCTestCase {
    func testAutomaticMountPolicyChecks() throws {
        for (_, check) in AutomaticMountPolicyChecks().all { try check() }
    }
}
#endif
