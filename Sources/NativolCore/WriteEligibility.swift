import Foundation

/// Certification must be for an exact tested combination, never a broad OS range.
public struct CertificationTarget: Equatable, Hashable {
    public let macOSVersion: String
    public let architecture: String
    public let backendIdentifier: String
    public let backendVersion: String

    public init(macOSVersion: String, architecture: String,
                backendIdentifier: String, backendVersion: String) {
        self.macOSVersion = macOSVersion
        self.architecture = architecture
        self.backendIdentifier = backendIdentifier
        self.backendVersion = backendVersion
    }
}

public enum CertificationManifest {
    /// No combination has completed destructive-media, integrity, and recovery testing yet.
    /// Beta write authorization is separate from this certification record.
    public static let certifiedTargets: [CertificationTarget] = []

    public static func isCertified(_ target: CertificationTarget) -> Bool {
        certifiedTargets.contains(target)
    }
}

public enum WriteBlocker: String, Identifiable, Equatable {
    case writeBackendUnavailable
    case uncertifiedConfiguration
    case notNTFS
    case externalStatusUnverified
    case wholeDisk

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .writeBackendUnavailable: return "Write engine not integrated"
        case .uncertifiedConfiguration: return "Compatibility testing pending"
        case .notNTFS: return "NTFS volume required"
        case .externalStatusUnverified: return "External drive required"
        case .wholeDisk: return "Partition required"
        }
    }

    public var message: String {
        switch self {
        case .writeBackendUnavailable:
            return "General certified write access is unavailable. The Intel beta uses a separate attachment and health validation path."
        case .uncertifiedConfiguration:
            return "No macOS, processor, and driver combination has been certified for writing yet."
        case .notNTFS:
            return "The filesystem must be positively identified as NTFS."
        case .externalStatusUnverified:
            return "Internal devices and devices with unknown external status cannot be used."
        case .wholeDisk:
            return "A whole disk cannot be used as an NTFS partition."
        }
    }
}

/// A general certification report, not an authorization token. ExperimentalWriteLease
/// and the protected helper separately validate beta requests; their existence does
/// not add a configuration to this certification manifest.
public struct WriteEligibility: Equatable {
    public let blockers: [WriteBlocker]
    public var isEligible: Bool { blockers.isEmpty }

    private init(blockers: [WriteBlocker]) { self.blockers = blockers }

    public static func evaluate(volume: Volume, certification: CertificationTarget? = nil) -> Self {
        var blockers: [WriteBlocker] = [.writeBackendUnavailable]
        if certification.map(CertificationManifest.isCertified) != true {
            blockers.append(.uncertifiedConfiguration)
        }
        if !volume.isNTFS { blockers.append(.notNTFS) }
        if !volume.isExternal { blockers.append(.externalStatusUnverified) }
        if volume.isWhole { blockers.append(.wholeDisk) }
        return Self(blockers: blockers)
    }
}
