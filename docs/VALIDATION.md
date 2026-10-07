# Intel beta validation

Release candidate: **0.5.0-beta.1, build 12**, prepared 2026-10-07 on native Intel macOS 15.7.9. This record describes local automated verification. It does not establish production readiness or certify other Macs and physical drives.

## Completed checks

| Check | Observed result |
| --- | --- |
| Core policy harness | 50 checks passed, including general geometry, identity changes, independent-drive queueing, ambiguous attachments, reconnect exclusions and separate broad-writing opt-in. |
| Helper validation harness | 319 assertions passed without device or engine operations: request/lease validation, supported and refused geometry, kernel readiness sequencing, mount identity and child/unmount lifecycle handling. |
| Background service harness | 78 checks passed: caller identity, bounded framing, operation ownership, independent-drive admission, conflicts, incomplete session identity and eight-drive limit. Includes actual kernel peer credentials and rejection of an untrusted test executable. |
| Login launch interpretation | 8 checks passed without registering a login item. |
| NTFS health inspector | 12 existing tests passed, then one added 4096-byte sector test passed. All fixtures were newly created private regular image files. Checks cover clean NTFS, dirty/unsupported flags, hibernation variants, malformed NTFS, descriptor-only interface and unchanged image hashes after inspection. |
| Inherited descriptor I/O | Passed regular-file tests for descriptor identity, close-on-exec behavior, read/write data, read-only denial, exclusive locking, revoked pathname permissions and incompatible descriptors. No devices or filesystem mounts used. |
| Source packager | 5 tests passed, including source/hash inventory, site assets, stable archive content, exclusion of private/generated files and refusal of altered upstream source or unexpected symlinks/vendor libraries. |
| Fresh Intel build | App, service, helper, NTFS driver and health inspector compiled successfully as x86_64. Every bundled executable passed architecture checks; no personal `/Users/` build paths or macFUSE binaries were found. |
| Bundle and installer | Strict recursive code-signature verification passed. Generated privileged installer passed shell syntax validation and its exact native app identity requirement. The installer was printed and checked, never executed. |
| Build/source correspondence | Fresh inventory matched `dist/build-record.json`; the DMG packager rejects changes to compiled inputs or app contents before packaging. |

The app uses ad-hoc signatures, which establish local integrity but do not provide Developer ID trust or notarization. Build diagnostics included missing debug-module cache warnings after path remapping; linking succeeded and debug information was stripped before signing.

## Package verification

The packaging script verifies the new DMG, attaches only that generated image read-only, compares all payload files with its manifest, verifies the enclosed app signature, and detaches it before publishing the local artifact. It includes the exact corresponding-source archive and a build record. This is artifact inspection, not installation or a drive-writing test. See the sibling SHA-256 checksum for the distributed artifact's identity.

## Still required

- Installation and current build execution on independent Intel Macs, including macFUSE approval and Full Disk Access.
- Physical testing of this broader-drive build: simultaneous drives, GPT/MBR layouts, USB/Thunderbolt, physical 512/4096-byte sectors, larger media and Windows structural checks.
- Finder copy/rename/delete and metadata, larger transfers, busy eject, sleep/wake, restart, permission loss, failure recovery and uninstall/update behavior for the final shared build.
- Developer ID signing, notarization, stable update identities and a focused privilege/filesystem review before production distribution.

Earlier personal-build copy/eject/reconnect/reboot observations on one machine are not evidence that this general-drive build has passed those scenarios. The local installed personal app was not replaced during packaging, and no physical drive was written by these checks.

Daemon pending mount reservations are in memory until a helper publishes its protected state. If the daemon crashes during that brief interval, the eight-drive resource cap may undercount a still-starting helper. The per-physical-drive lock still prevents duplicate ownership; this restart edge needs lifecycle testing.

Apple silicon and additional macOS writing support remain paused/out of scope. See [release readiness](RELEASE-CHECKLIST.md) and the [Intel test guide](INTEL-BETA.md).
