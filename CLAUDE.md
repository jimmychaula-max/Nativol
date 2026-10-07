# Nativol contributor guide

Nativol is a native Swift/Objective-C macOS NTFS testing app. This source tree is separate from a private personal deployment; do not copy personal reports, device identifiers, credentials, or Git history into it.

## Commands

- `./setup.sh`: verify local developer tools and run pure core/helper/service and source-package checks.
- `./setup.sh --build`: run those checks and `bash scripts/build-app.sh --intel`.
- `swift test`: XCTest route when full Xcode is selected.
- `python3 -m http.server 8080 --directory site`: preview the static site locally.

Read `docs/BUILDING.md` for exact dependency, release, and DMG commands. Builds must not silently install a driver or operate on a physical volume. Never run image/physical laboratory scripts as a routine source check; inspect their guard requirements first.

## Architecture

- `Sources/NativolApp`: SwiftUI app, menu bar, volume presentation, background client, login startup.
- `Sources/NativolCore`: volume identity, eligibility, leases, automatic-attempt policy.
- `Sources/NativolHelper/main.m`: privileged attachment ownership and managed driver lifecycle.
- `Sources/NativolService/main.m`: authenticated, bounded fixed-operation gateway.
- `engine/nativol-ntfs-health.c`: GPL read-only preflight over an inherited descriptor.
- `patches/`: GPL modifications to pinned NTFS-3G source.
- `Tests/` and `scripts/test-*`: pure validation and explicitly separate disposable-image harnesses.
- `site/`: static HTML/CSS with local JavaScript for the illustrative menu and donation controls; no third-party tracking.

## Scope and changes

The 0.5.0-beta.1 writing gate is native Intel macOS 15.7.9 plus macFUSE 5.4.0. Automatic mounting is a global opt-in for eligible external single-partition NTFS media; it is bounded to eight managed volumes. Apple Silicon is paused. Compile targets are not runtime certification.

Keep identity, geometry, topology, health, ownership, authentication, safe unmount, and permission checks independent of UI state. Refuse ambiguous states. Do not add force/repair fallbacks or broaden support from a successful build alone. Never test on irreplaceable data.

Original code is MIT except the GPL health checker and NTFS patches. Preserve `THIRD_PARTY_NOTICES.md` and corresponding-source obligations. The maintainer has authorized initial publication of the reviewed source, Intel beta, and static website at `nativol.org`. Deploy only the contents of `site/` to Cloudflare Pages; publish the reviewed DMG and its matching source companion through GitHub Releases. Keep local account screenshots, onboarding drafts, and publication-review records out of Git and source archives. Publication authorization does not cover unrelated account changes or purchases. The optional GitHub Pages workflow remains a manual alternative and is not required for Cloudflare deployment.
