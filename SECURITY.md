# Security

Nativol contains a privileged mount helper and an authenticated local background service. Filesystem metadata, device topology, removable media, and IPC requests are untrusted inputs. This beta has a narrow supported writing configuration; it is not a filesystem-safety certification.

## Report a problem

Report suspected wrong-device targeting, privilege-boundary flaws, authentication bypasses, or integrity failures through [GitHub private vulnerability reporting](https://github.com/jimmychaula-max/Nativol/security/advisories/new). Private reporting is enabled for this repository. Do not put exploit details or personal diagnostics in a public issue.

Include the app version, macOS version, CPU architecture, high-level drive connection type, a synthetic reproduction if available, and a redacted account of what happened. Remove volume labels, filesystem serials, usernames, file names and paths, passwords, and tokens. Preserve evidence locally before changing or repairing an affected volume.

## Boundaries

Background setup requires administrator authorization to install root-owned components and exact package pins. The local protocol checks the caller's kernel audit token, active console identity, hardened runtime, and pinned code signature. It accepts fixed operations, not shell commands or caller-controlled executable paths. No administrator password is saved and no sudoers rule is installed.

The helper rechecks the current attachment and filesystem health before starting the driver under ordinary-user permissions. Ordinary stop and eject do not force-unmount a busy drive. Full Disk Access and macFUSE approval remain separate macOS permissions; Nativol does not alter privacy databases, SIP, or startup-security settings.

Ad-hoc signatures establish a local code identity, not a verified public publisher. See [development signing](docs/DEVELOPMENT-SIGNING.md) and the [release checklist](docs/RELEASE-CHECKLIST.md). Report successful tests as evidence about their actual hardware and operation, not a guarantee about all NTFS volumes.
