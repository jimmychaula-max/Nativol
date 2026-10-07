# License and dependency notices

The root [MIT license](LICENSE) covers original Nativol application, core, helper, service, test, build, documentation, and website code, except the GPL-covered files listed below. Bundling separate programs does not relicense those programs. Preserve the applicable notices when redistributing source or binaries.

| Material | License / treatment | Included material |
|---|---|---|
| Original Nativol code, except the following GPL files | MIT; copyright 2026 Nativol contributors | Application, core, helper, service, scripts, tests, documentation, website |
| `engine/nativol-ntfs-health.c` and the resulting checker linked to libntfs-3g | GPL-2.0-or-later | Source, build scripts, upstream corresponding source, [license text](licenses/NTFS-3G-COPYING) |
| The three `patches/ntfs-3g-2026.9.28-*.patch` files and patched NTFS-3G binaries | GPL-2.0-or-later | Patches, pinned upstream archive, configuration and scripts, [license text](licenses/NTFS-3G-COPYING) |
| NTFS-3G / ntfsprogs 2026.9.28 and libntfs-3g | GPL-2.0-or-later; preserve upstream notices | `third-party/sources/ntfs-3g_ntfsprogs-2026.9.28.tgz` |
| Upstream bundled fuse-lite source | Preserve its upstream licensing, including LGPL-2.1-or-later material | Included in the unmodified NTFS-3G source archive; the Nativol build uses separately installed macFUSE, not fuse-lite. See [COPYING.LIB](licenses/NTFS-3G-COPYING.LIB) and upstream file notices. |
| pkgconf 2.5.1 | Its permissive upstream license | Build-time source in `third-party/sources/pkgconf-2.5.1.tar.xz`; [license text](licenses/pkgconf-COPYING) |
| macFUSE 5.4.0 | Separate vendor dependency with its own terms | Installed by the user from [macFUSE](https://macfuse.github.io/). No macFUSE framework, dylib, extension, or installer is included in the Nativol DMG. |
| macOS system frameworks and SDK | Apple-provided system/build dependencies | Not redistributed in this source tree or DMG |

The NTFS-3G changes are a Darwin FUSE adaptation, inherited-descriptor device access, and initialization of an inode pointer before a refused create operation. Exact changes are provided as patches, not hidden inside a binary. The GPL health checker uses inherited descriptor 3 and performs read-only checks; it does not accept a target device path.

See [source compliance](docs/SOURCE-COMPLIANCE.md) for the archive checksums and corresponding-source packaging requirements. A release containing GPL binaries must accompany them with the exact source and build material required by their licenses. A link to an unrelated upstream branch is not a replacement for the matching source. This inventory does not grant rights over upstream trademarks or third-party software.
