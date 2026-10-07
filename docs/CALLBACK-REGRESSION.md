# NTFS create-callback regression

Regression harness for NTFS-3G 2026.9.28 with Nativol's one-line `ni = NULL` patch.

Run after building the native image tools and development driver:

```sh
python3 scripts/test-driver-callback.py
```

The wrapper accepts no target argument. It creates a private 64 MiB regular file under `.local-engine/callback-tests`, formats only that new file, builds the C harness against the recorded driver source and static NTFS library, and retains its report and fixture. It refuses root execution. It performs no operating-system mount, device operation, runtime installation, or extension registration.

The harness includes the actual patched `ntfs-3g.c`, renames its unused command-line entry point, supplies a deterministic FUSE caller context, and replaces the FUSE mount function with an aborting sentinel. It calls the actual `ntfs_fuse_create()` callback directly. Libntfs functions named `ntfs_mount`/`ntfs_umount` open and close the private image internally; they do not register a volume with macOS.

The assertions check ordinary file creation, synthetic reparse-parent refusal, absence of leaked child inodes and handles, unchanged image contents across refused operations, and read-only reopening. Run the harness to obtain evidence for the current build; private-machine test reports are not included.

Compilation treats a possibly uninitialized variable and incompatible callback-pointer types as errors. The test requires plugins to be disabled, matching the configuration that exposed the upstream defect. No undefined-behavior warning is suppressed.

The parent is a synthetic NTFS directory with the reparse flag set; it is deliberately not a complete Windows junction. This narrowly verifies the patched refusal branch. Windows-produced reparse fixtures, macFUSE/FSKit transport, actual caller permissions, mount lifecycle, and physical Apple Silicon execution remain independent validation work. A passing result does not certify writable mounting of user drives.

Each run writes its report and disposable fixture under a fresh private directory in the ignored `.local-engine/callback-tests` tree.
