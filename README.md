# Nativol

A native macOS menu-bar app for working with external NTFS drives. Nativol brings drive status, Finder access, automatic mounting, and safe eject into a small local app.

**0.5.0-beta.1 · Intel testing beta.** This build is for testing on backed-up spare media. It is not a production-certified filesystem driver, and it does not promise write support for every Mac or drive. Apple Silicon work is paused.

## What it does

- Lists connected volumes and their current access mode.
- Uses NTFS-3G through separately installed macFUSE for eligible external NTFS volumes.
- Offers background operation, a menu-bar icon, and launch after login.
- After one global opt-in, automatically attempts eligible NTFS attachments without per-drive enrollment or repeated administrator prompts.
- Rechecks device identity and filesystem health before writing; uses ordinary unmount/eject operations.

Installing or updating the protected background components needs macOS administrator authorization. Full Disk Access for the service and macFUSE approval are separate system permissions. No password is stored and no passwordless sudo rule is created.

## Compatibility in this beta

| Area | Current boundary |
|---|---|
| App build / discovery | Intel build, macOS 12 deployment target; a target is not proof of runtime compatibility on each release |
| Writing | Native Intel macOS **15.7.9**, separately installed **macFUSE 5.4.0** |
| Candidate media | External physical NTFS over USB or Thunderbolt, verified single-partition topology and supported 512/4096-byte geometry |
| Automatic operation | One global opt-in; up to eight concurrent managed volumes; failed attempts pause for that attachment |
| Unsupported or uncertain media | Writing is refused; other filesystems remain managed by macOS |
| Apple Silicon / other macOS write support | Paused / not enabled in this beta |

Dirty or hibernated NTFS volumes, incomplete identity or topology evidence, unsupported states, and unhealthy preflight results must not be forced writable. Use Windows and a verified backup to address a drive that needs repair. Nativol does not format or repair drives.

The earlier personal build was exercised on one Intel Mac and disposable card. That history does not validate this broader-drive beta on colleagues' hardware. See the [Intel beta guide](docs/INTEL-BETA.md) for first-run setup and a test sequence.

## Build from source

Development checks require an Intel Mac and Apple's Command Line Tools with Swift 5.9 or newer. Building the complete app also requires separately installed official macFUSE 5.4.0 with its development headers. No account, cloud service, API key, or environment secret is required.

```sh
./setup.sh             # Check tools and run pure core/helper/service and source-package checks
./setup.sh --build     # Then build the Intel app; no installation or drive writes
```

The output is `dist/Nativol.app`. Read [BUILDING](docs/BUILDING.md) for exact dependency verification, engine builds, DMG packaging, signing limits, and corresponding source. `setup.sh` does not install macFUSE, download an installer, request administrator access, or mount a device.

## Distribution and website

The [0.5.0-beta.1 Intel beta release](https://github.com/jimmychaula-max/Nativol/releases/tag/v0.5.0-beta.1) includes the testing DMG, SHA-256 checksums, and matching source archive. A local ad-hoc signature is not Apple notarization or publisher authentication. Do not describe this beta as production ready. The [release checklist](docs/RELEASE-CHECKLIST.md) tracks the remaining gates.

The static site is in `site/`, prepared for **[nativol.org](https://nativol.org)** on Cloudflare Pages. The [Nativol GitHub repository](https://github.com/jimmychaula-max/Nativol) hosts the app source and release downloads. See the [hosting guide](docs/HOSTING.md) for deployment settings and verification; registering a domain does not itself deploy the site.

## Contributing and licensing

Read [CONTRIBUTING](CONTRIBUTING.md) before changing drive or privilege handling and [SECURITY](SECURITY.md) for vulnerability reporting. Do not submit unredacted logs or real drive identifiers.

Original Nativol code is MIT licensed, with explicit GPL exceptions for the health checker and NTFS-3G patches. NTFS-3G and other dependencies retain their own terms. See [LICENSE](LICENSE), [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md), and [source compliance](docs/SOURCE-COMPLIANCE.md).
