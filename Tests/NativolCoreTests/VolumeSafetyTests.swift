#if canImport(XCTest) && !NATIVOL_STANDALONE_CHECKS
import XCTest

final class VolumeSafetyTests: XCTestCase {
    func testFilesystemMustPositivelyIdentifyNTFS() throws { try CoreSafetyChecks().testFilesystemMustPositivelyIdentifyNTFS() }
    func testUnknownExternalStatusIsNeverAssumedExternal() throws { try CoreSafetyChecks().testUnknownExternalStatusIsNeverAssumedExternal() }
    func testDiscoveryRequiresExternalPhysicalPartitionEvidence() throws { try CoreSafetyChecks().testDiscoveryRequiresExternalPhysicalPartitionEvidence() }
    func testMountedWritabilityIsNotInferredFromFilesystemType() throws { try CoreSafetyChecks().testMountedWritabilityIsNotInferredFromFilesystemType() }
    func testNoCurrentConfigurationGrantsWriteAccess() throws { try CoreSafetyChecks().testNoCurrentConfigurationGrantsWriteAccess() }
    func testInvalidVolumesHaveSpecificWriteBlockers() throws { try CoreSafetyChecks().testInvalidVolumesHaveSpecificWriteBlockers() }
    func testMountAndDescriptionChangesReplaceSnapshotsWithoutDuplicates() throws { try CoreSafetyChecks().testMountAndDescriptionChangesReplaceSnapshotsWithoutDuplicates() }
    func testBsdNameReuseDoesNotReuseAttachmentIdentity() throws { try CoreSafetyChecks().testBsdNameReuseDoesNotReuseAttachmentIdentity() }
    func testParentDisappearanceRemovesAllSiblingPartitions() throws { try CoreSafetyChecks().testParentDisappearanceRemovesAllSiblingPartitions() }
    func testParentDisappearanceInvalidatesPartiallyDescribedChildSession() throws { try CoreSafetyChecks().testParentDisappearanceInvalidatesPartiallyDescribedChildSession() }
    func testLosingExternalEvidenceRemovesPreviouslyEligibleVolume() throws { try CoreSafetyChecks().testLosingExternalEvidenceRemovesPreviouslyEligibleVolume() }
    func testRefreshInvalidatesPreviousAttachmentIdentity() throws { try CoreSafetyChecks().testRefreshInvalidatesPreviousAttachmentIdentity() }
}
#endif
