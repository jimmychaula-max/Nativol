# Nativol 0.5.0-beta.1 — Intel test guide

This DMG is a testing release for colleagues, not a production-certified driver. Keep a separate verified backup of any data on a test drive. Start with spare NTFS media. Apple Silicon work is paused.

## Supported writing configuration

- Native Intel Mac running macOS **15.7.9**.
- Official **macFUSE 5.4.0**, installed separately, with the required macOS approvals.
- External physical NTFS over USB or Thunderbolt, a verified single-partition layout, supported 512/4096-byte geometry, and a successful read-only health check.
- At most eight simultaneous Nativol-managed volumes.

The app has a macOS 12 deployment target for discovery; writing on other versions is not enabled. Other filesystems stay under native macOS management. Nativol refuses unsupported, dirty, hibernated, ambiguous, or unhealthy NTFS candidates. It does not format drives, repair them, or force a busy unmount. A supported configuration is a beta eligibility boundary, not a guarantee that every drive is safe.

## Install and set up

1. Obtain the DMG and its SHA-256 checksum from the maintainer. Compare the checksum before opening it. This beta uses local ad-hoc signing and is not Developer ID signed or notarized.
2. Install official macFUSE 5.4.0 separately from [macFUSE](https://macfuse.github.io/). Follow its own installation and system-approval instructions; the Nativol DMG does not include or install it.
3. Copy Nativol to Applications, then launch it. If macOS blocks this unsigned beta, use only the system's normal approval flow for an app whose source and checksum you trust. Do not disable Gatekeeper or remove quarantine with a script.
4. In Nativol Setup, install background access. Approve the macOS administrator prompt for the protected app/service installation. Enable the displayed NativolService in Privacy & Security → Full Disk Access when requested.
5. Enable automatic writing after reviewing the supported scope. This global setting applies to all eligible attachments; there is no per-drive enrollment. Enable launch at login if desired. Login startup occurs after sign-in.

Administrator authorization is for setup or updates. Ordinary supported drive operations use the background service without storing a password. If setup or permissions are incomplete, address the displayed status instead of repeatedly reconnecting or forcing a write.

## First test

1. Connect a backed-up spare NTFS drive and confirm Nativol shows writable access.
2. Copy a small folder with a few synthetic files to it. Open and compare the files, then copy them back; compare checksums when possible.
3. Eject with Finder and confirm it completes. Reconnect and repeat using Nativol's safe eject action.
4. Restart the Mac, sign in, and check that the menu-bar app appears. Reconnect and confirm automatic writing requires no password.
5. If you have Windows access, read back the same files there and check the volume's state after clean ejection. A copied-file check alone is not a complete filesystem integrity check.

Stop if copying, ejection, or identity checks fail. Keep the drive connected while a safe eject is pending. Do not unplug a drive during a write, run repair through Nativol, or use a valuable drive to expand testing.

## Report results

Share the app version, Intel Mac model family, macOS version, macFUSE version, drive connection type, approximate capacity, and which steps passed or failed. Include exact error text with personal details removed. Do not send raw system logs, full disk images, filesystem serials, home paths, volume labels, or personal files.

The production-readiness checklist remains open until this broader build has physical evidence across the intended hardware and filesystems, along with signing/notarization and lifecycle testing. See `docs/RELEASE-CHECKLIST.md` in the accompanying source.
