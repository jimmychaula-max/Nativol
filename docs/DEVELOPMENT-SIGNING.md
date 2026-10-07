# Development signing

The app and background gateway use Hardened Runtime without debugging or library-validation exceptions. The administrator-installed service pins the current app's code-directory hash and verifies live callers using kernel audit tokens. Updating the app changes that pin and requires setup again. This local ad-hoc trust arrangement is separate from public Developer ID signing.

`scripts/build-driver-dev.sh` signs the development `ntfs-3g` and `ntfs-3g.probe` executables, verifies their signatures, saves signature details, and computes final binary checksums. Cross-compilation and signature verification do not establish Apple Silicon runtime compatibility.

The pinned [macFUSE Mounter source](https://github.com/macfuse/mount/blob/68f082dc7b6b51aefdf99fafdb8ed1c74928b79a/Mount/Mounter.swift) configures a peer requirement to authenticate its mount daemon. The [XPC transport source](https://github.com/macfuse/mount/blob/68f082dc7b6b51aefdf99fafdb8ed1c74928b79a/Mount/Channel/XPC/XPCTransport.swift) creates the endpoint listener separately. Nativol does not modify either vendor component, remove the peer requirement, claim the vendor's identity, or change macOS security policy.

Ad-hoc signing supplies a code identity for local development. It is not Developer ID signing, Apple notarization, publisher authentication, or permission to access a drive. Public binary distribution needs a separate signing and notarization plan for the app and privileged components, protected installation paths, dependency verification, and third-party license review. User approval of the macFUSE extension remains an independent requirement.
