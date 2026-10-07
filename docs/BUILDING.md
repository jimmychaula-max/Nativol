# Building the Intel beta

The current release is **0.5.0-beta.1**, build 12. Apple silicon work is paused.
These commands compile and package files inside this checkout. They do not
install Nativol, activate a driver, mount a physical drive, or publish anything.

## Prerequisites

- An Intel Mac, running as an ordinary user.
- Apple Command Line Tools with Swift 5.9 or later, Clang, the macOS SDK, Python 3,
  and the standard macOS signing and disk-image tools. The initial build used
  Swift 6.1.2, Apple Clang 17, and SDK 15.5.
- Separately install **official macFUSE 5.4.0**, including its development headers,
  from [the vendor](https://macfuse.github.io/). The build verifies the exact
  reviewed library, framework and headers before compiling. Nativol does not
  download or redistribute a macFUSE binary in its DMG.

The source deployment floor is macOS 12, allowing the interface to inspect
compatibility. **Writable operation remains restricted to native Intel macOS
15.7.9** in this beta. Compilation on another OS does not establish writing
support. The published tester instructions define the supported media checks.

## Build

```sh
bash scripts/build-app.sh --intel
```

The command builds the included pinned NTFS source, applies the three patches,
builds the health inspector, app, privileged helper and background service, then
creates `dist/Nativol.app`. Every executable contains only `x86_64` code. Its
local ad-hoc signatures provide code identities; they are not Developer ID
signatures and do not provide notarization or publisher authentication.

`dist/build-record.json` records hashes of the app files, sources, patches,
upstream archives and scripts used for the build. The package command refuses
changed build inputs until the app is rebuilt.

The vendored source archives are used before any network fetch. Their hashes:

| Archive | SHA-256 |
| --- | --- |
| `ntfs-3g_ntfsprogs-2026.9.28.tgz` | `350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8` |
| `pkgconf-2.5.1.tar.xz` | `cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243` |

If the included source files are missing, `engine-build.sh` can retrieve these
same pinned archives from the upstream URLs and verifies them before execution.
No prebuilt NTFS executable is required to build this project.

## Package

After completing the documented tests:

```sh
python3 scripts/package-source.py
bash scripts/package-test-dmg.sh
```

The second command also creates the source companion if necessary. It includes
the app, an Applications shortcut, tester instructions, license notices,
source archive and file manifests. It verifies the DMG, attaches only the image
it just created in read-only mode, compares every payload file, verifies the
app signature, and detaches the image. It never mounts or operates on a test
card. Existing artifact names are not silently replaced.

Changes after packaging require removing only the generated source archive
and DMG for that version, or selecting a new release version, then repackaging.
Do not omit the source archive when sharing this build; it accompanies the GPL
engine and health inspector.

## Runtime dependency

The bundled backend manifest includes the expected hash of `libfuse.2.dylib`,
but its bytes are deliberately absent from the app. Installation validates the
separately installed vendor library at its fixed location, then makes a verified
protected local copy. The full macFUSE runtime, framework and approved extension
remain independently installed vendor components.

No private developer checkout or extracted SDK cache is needed. Build products
stay in `.build/`, `.local-engine/` and `dist/`, which are excluded from source
packages. See [source compliance](SOURCE-COMPLIANCE.md) for the license boundary.

## Production release work

The beta packaging command deliberately does not claim production readiness.
A production release additionally requires Developer ID signing and a tested
Hardened Runtime configuration for every component, notarization and stapling,
validation on clean recipient Macs, and completion of the filesystem test
matrix. Follow [Apple's distribution guidance](https://developer.apple.com/developer-id/).
Do not disable Gatekeeper or remove quarantine attributes to make a build pass
release checks.

## Tests and historical lab tools

The current release checks are the core, helper, service, login-startup,
NTFS-health, inherited-descriptor, installer-bundle and source-package tests
listed in `docs/VALIDATION.md`. The default `./setup.sh` runs the applicable
checks that do not need a built engine. The health and descriptor regressions
use newly created regular image files and do not operate on physical media.

Some retained developer lab scripts describe earlier backend experiments.
They can still reference an extracted `.local-fuse-inspect` SDK, historical
build records, or a deliberately disabled physical-card profile. Those
harnesses are unsupported by this release and are outside its validation
results. Do not reactivate the physical-card scripts or infer that old lab
commands apply to this beta. Running any additional harness requires separate
review of its setup, target and permitted operations.
