# Corresponding source and dependency licenses

Nativol's original application, service and helper code use the root MIT
license. This does not relicense third-party source or Nativol's changes to
copyleft components. See `THIRD_PARTY_NOTICES.md` for the component inventory.

## NTFS engine and health inspector

The release uses NTFS-3G **2026.9.28**, upstream tag commit
`7f0f841fc52cf719106c5c93bafe465004e36816`. NTFS-3G, libntfs-3g, the three local
patches and the statically linked `nativol-ntfs-health` program use
**GPL-2.0-or-later**. The full upstream source archive is included under
`third-party/sources/`, with its original notices and license texts intact.

The modifications made for Nativol on 5–6 October 2026 are supplied separately:

- `ntfs-3g-2026.9.28-no-plugins-inode.patch` initializes the inode pointer on the
  plugins-disabled refusal path.
- `ntfs-3g-2026.9.28-darwin-fuse.patch` selects the Darwin FUSE filesystem type
  while retaining block-device I/O.
- `ntfs-3g-2026.9.28-darwin-fd.patch` preserves and duplicates the validated
  inherited descriptor instead of reopening the device by pathname.

The modified upstream files are `src/ntfs-3g.c`, `src/ntfs-3g_common.c` and
`libntfs-3g/unix_io.c`. Applying these three patches to the included archive is
the preferred editable source of the shipped engine. The separate health
program's source is `engine/nativol-ntfs-health.c`.

`scripts/engine-build.sh`, `build-driver-dev.sh`, `build-ntfs-health.sh`,
`build-personal-backend.sh`, `build-app.sh` and the installation implementation
are all present in the source companion. Build configuration, required SDK and
compiler versions are documented in `docs/BUILDING.md`. Headers, upstream
build files, and the exact pkgconf build-tool source and license are included.

The source archive inside every testing DMG is the complete clean project
snapshot plus these upstream archives. `SOURCE-MANIFEST.json` hashes its files;
`BUILD-RECORD.json` in the DMG ties the build inputs to the app binaries.
Recipients can modify and rebuild the software; signing credentials are not
required for a local ad-hoc build. Do not redistribute a binary without its
source companion and license notices.

NTFS-3G's `COPYING.LIB` applies to its included upstream fuse-lite source. This
build selects external FUSE and does not link that fuse-lite library. The
upstream source archive is retained intact, including its license files.

## macFUSE

**No macFUSE binary is included in the source archive or testing DMG.** The
release requires independently installed official macFUSE 5.4.0 and its SDK.
Its runtime is not wholly open source, and its licenses remain independent of
Nativol's MIT license. The installer can make a protected copy of the verified
library already installed on the recipient's own Mac; Nativol does not acquire
or redistribute the vendor installer on the recipient's behalf.

Review the exact [macFUSE 5.4.0 license](https://github.com/macfuse/macfuse/blob/macfuse-5.4.0/LICENSE.txt)
for any future distribution change. Its terms include notice requirements and
restrictions on binary bundling or automated acquisition with commercial
software. A future macFUSE bundling decision needs its own license and
corresponding-source review.

## Source package contents

The source packager includes application/helper/service/health source, tests,
resources, patches, build and installation scripts, project documentation,
licenses, upstream archives, and the static website when present. It excludes
Git history, build caches, private reports and generated binary distributions.
It rejects symlinks and unexpected executable-library or disk-image artifacts
in the source tree. No private logs or original developer machine state are
necessary to build the beta.
