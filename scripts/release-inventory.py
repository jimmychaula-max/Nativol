#!/usr/bin/env python3
"""Read-only release checks and source/binary inventory; never runs Nativol."""
import hashlib
import json
import pathlib
import plistlib
import stat
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIBRARY = 'lib/libfuse.2.dylib'
LIBRARY_SHA = '7f8284463379f48034e38913804920719fb2c13a42e04027533fd1d19ff8bb42'
EXPECTED = {'bin/NativolHelper', 'bin/ntfs-3g', 'bin/nativol-ntfs-health', LIBRARY,
            'licenses/NTFS-3G-COPYING', 'licenses/NTFS-3G-COPYING.LIB'}
ARCHIVES = {'ntfs-3g_ntfsprogs-2026.9.28.tgz': '350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8',
            'pkgconf-2.5.1.tar.xz': 'cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243'}


def digest(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Not a regular file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(app):
    if app.is_symlink() or not app.is_dir():
        raise ValueError('Expected a regular app bundle')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    backend = app / 'Contents/Resources/Backend'
    manifest = json.loads((backend / 'manifest.json').read_bytes())
    if (manifest.get('schemaVersion') != 1 or manifest.get('profile') != 'intel-external-test-v1'
            or set(manifest.get('files', {})) != EXPECTED):
        raise ValueError('Unexpected Intel beta backend manifest')
    if manifest['files'][LIBRARY] != LIBRARY_SHA or (backend / LIBRARY).exists():
        raise ValueError('Vendor library must be a pinned external dependency, not bundled')
    for name, expected in manifest['files'].items():
        if name != LIBRARY and digest(backend / name) != expected:
            raise ValueError('Backend file differs from its manifest: ' + name)
    if (backend / 'version.txt').read_text().strip() != digest(backend / 'manifest.json'):
        raise ValueError('Backend version differs from manifest')
    executables = ['Contents/MacOS/Nativol', 'Contents/Resources/Service/NativolService']
    executables += ['Contents/Resources/Backend/' + name for name in sorted(EXPECTED) if name.startswith('bin/')]
    for name in executables:
        if b'/Users/' in (app / name).read_bytes():
            raise ValueError('Personal build path found in executable: ' + name)
        arch = subprocess.check_output(['/usr/bin/lipo', '-archs', str(app / name)], text=True).strip()
        if arch != 'x86_64':
            raise ValueError('Beta executable is not Intel-only: ' + name)
    service = app / 'Contents/Resources/Service'
    service_manifest = json.loads((service / 'manifest.json').read_bytes())
    if service_manifest != {'schemaVersion': 1, 'files': {'NativolService': digest(service / 'NativolService')}}:
        raise ValueError('Service manifest mismatch')
    inputs = {}
    for folder in ('Sources', 'Resources', 'engine', 'patches'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_symlink():
                raise ValueError('Source symlink is not supported: ' + str(path))
            if path.is_file():
                inputs[str(path.relative_to(ROOT))] = digest(path)
    for name in ('Package.swift', 'scripts/build-app.sh', 'scripts/build-background-service.sh',
                 'scripts/build-driver-dev.sh', 'scripts/build-ntfs-health.sh', 'scripts/build-personal-backend.sh',
                 'scripts/engine-build.sh', 'scripts/make-icon.swift'):
        inputs[name] = digest(ROOT / name)
    for name, expected in ARCHIVES.items():
        relative = 'third-party/sources/' + name
        actual = digest(ROOT / relative)
        if actual != expected:
            raise ValueError('Upstream source archive checksum mismatch: ' + name)
        inputs[relative] = actual
    files = {}
    for path in sorted(app.rglob('*')):
        if path.is_symlink():
            raise ValueError('Unexpected app symlink: ' + str(path.relative_to(app)))
        if path.is_file():
            if path.suffix in ('.dylib', '.kext') or 'macfuse.fs' in path.parts:
                raise ValueError('Unexpected vendor/runtime binary in app')
            files[str(path.relative_to(app))] = digest(path)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    return {'schemaVersion': 1, 'kind': 'nativol-intel-beta-build',
            'appVersion': info.get('NativolReleaseVersion', info['CFBundleShortVersionString']),
            'bundleVersion': info['CFBundleShortVersionString'], 'buildNumber': info['CFBundleVersion'],
            'architectures': ['x86_64'], 'signature': 'ad-hoc', 'notarized': False,
            'macFUSEBundled': False, 'macFUSERequired': '5.4.0',
            'buildInputs': inputs, 'appFiles': files}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: release-inventory.py path/to/Nativol.app')
    try:
        print(json.dumps(inventory(pathlib.Path(sys.argv[1]).absolute()), indent=2, sort_keys=True))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
