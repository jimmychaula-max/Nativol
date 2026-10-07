# Release readiness

Status: **Intel testing beta**, 0.5.0-beta.1. Preparing a DMG does not certify production readiness. Do not change the release label without evidence for the final binaries and supported scope.

## Before a colleague test build

- Verify the final app/helper/service/driver signatures and hashes, architecture and deployment targets.
- Run the applicable pure core, helper, service, and packaging checks; record actual results separately from targets.
- Confirm unsupported OS, architecture, topology, geometry, health, and changed-attachment cases refuse writing.
- Include the Intel beta guide, license inventory, and exact corresponding source for GPL binaries.
- Inspect the DMG and source archive for personal data, unneeded binaries, copied private history, and build paths.
- Confirm the official macFUSE dependency is installed separately and is not redistributed in the DMG.

## Before claiming production readiness

- Test the broader automatic-drive flow on independent Intel Macs and varied backed-up spare drives.
- Validate Finder copy/rename/delete, macOS metadata, large files, safe eject/reconnect, login/reboot, sleep/wake, and multiple drives.
- Exercise busy volumes, permission loss, service failure, driver failure, disconnects, and rejected dirty/hibernated media using controlled disposable fixtures.
- Validate Windows interoperability and filesystem structure; distinguish checksum checks from structural checks.
- Perform a focused review of the privilege boundary and untrusted filesystem handling.
- Complete Developer ID signing, notarization, update identity/migration and clean uninstall validation for the intended distribution.
- Establish a private vulnerability reporting channel and a maintenance/update policy.
- Rerun source sanitization and independently inspect the exact release archives.

Apple Silicon and additional macOS write support are separate future projects. No production or broad compatibility claim follows from a universal compilation or a test on one machine.
