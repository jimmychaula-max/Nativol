# Contributing

Nativol is an Intel testing beta. Keep production-readiness and compatibility claims tied to repeatable evidence. Apple Silicon work is paused; do not enable a new write platform merely because it cross-compiles.

Run `./setup.sh` for the pure core, helper, and service checks. Run `./setup.sh --build` for the Intel application. The detailed build and dependency requirements are in [BUILDING](docs/BUILDING.md). Full Xcode can additionally run `swift test`.

Changes to mounting, automatic discovery, IPC, or helper lifetime need tests for stale attachment identities, untrusted requests, changed mounts, unsupported media, and clean failure. Use synthetic fixtures or newly created disposable image files. Physical testing must use backed-up spare media; never use an irreplaceable drive or a default raw-device target.

Keep disk mutations outside UI code. Preserve the helper's independent identity, topology, health, descriptor, and permission checks. Failed checks must not trigger a forced mount, repair, alternate device, or security-policy bypass. Retain active session ownership until its driver and mount have completed cleanly.

For a contribution, explain the user-visible behavior, supported scope, relevant checks and their actual results, and any untested assumptions. Include a small reproduction using synthetic data. Do not attach full system inventories, disk images, private file names, filesystem serials, home paths, credentials, or unredacted diagnostic reports.

Changes to GPL-covered health code or NTFS-3G patches stay under their applicable license. Original contributions follow the root MIT license unless clearly marked otherwise. Keep dependency versions, upstream checksums, notices, and source-compliance material together. Do not commit downloaded installers or system drivers.

For security issues, follow [SECURITY](SECURITY.md) instead of posting exploit details in a public issue.
