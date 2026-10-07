#!/usr/bin/env python3
"""Package a complete source companion without private caches or Git history."""
import fnmatch
import gzip
import hashlib
import io
import json
import pathlib
import plistlib
import stat
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOP_FILES = ('Package.swift', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'CHANGELOG.md',
             'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', 'CLAUDE.md',
             'setup.sh', '.gitignore', '.gitattributes', '.env.example')
TOP_DIRS = ('Sources', 'Tests', 'Resources', 'engine', 'patches', 'scripts', 'docs',
            'licenses', 'third-party', '.github', 'site', 'website')
EXCLUDE_PARTS = {'__pycache__', '.DS_Store', '.git', '.build', '.local-engine', '.local-fuse-inspect',
                 'local-reports', 'node_modules', 'dist', '.swiftpm'}
# Local onboarding records may remain at stable paths used by the maintainer.
# Keep this boundary independent of Git: source packaging also runs before git init.
EXCLUDE_PATTERNS = ('FORK_REPORT.md', 'SANITIZATION_REPORT.md',
                    'docs/BUY-ME-A-COFFEE-PROFILE.md', 'docs/buy-me-a-coffee-*.jpg',
                    'docs/binance-pay-*.jpg', 'docs/nativol-profile.png')
REQUIRED = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/BUILDING.md', 'docs/SOURCE-COMPLIANCE.md',
            'licenses/NTFS-3G-COPYING', 'engine/nativol-ntfs-health.c',
            'third-party/sources/ntfs-3g_ntfsprogs-2026.9.28.tgz',
            'third-party/sources/pkgconf-2.5.1.tar.xz')


def main():
    archives = {'ntfs-3g_ntfsprogs-2026.9.28.tgz': '350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8',
                'pkgconf-2.5.1.tar.xz': 'cd05c9589b9f86ecf044c10a2269822bc9eb001eced2582cfffd658b0a50c243'}
    for name, expected in archives.items():
        path = ROOT / 'third-party/sources' / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Upstream source archive checksum mismatch: ' + name)
    files = {}
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            raise ValueError('Missing source companion file: ' + name)
    paths = [ROOT / name for name in TOP_FILES if (ROOT / name).exists()]
    for name in TOP_DIRS:
        folder = ROOT / name
        if folder.exists():
            if folder.is_symlink():
                raise ValueError('Source folder symlink refused: ' + name)
            paths += list(folder.rglob('*'))
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if EXCLUDE_PARTS.intersection(relative.parts):
            continue
        if any(fnmatch.fnmatchcase(relative.as_posix(), pattern) for pattern in EXCLUDE_PATTERNS):
            continue
        if path.is_symlink():
            raise ValueError('Source symlink refused: ' + str(relative))
        if path.is_file():
            if path.name == '.env' or path.suffix in ('.dmg', '.dylib', '.o', '.a', '.pyc'):
                raise ValueError('Unexpected generated/private file: ' + str(relative))
            files[str(relative)] = path.read_bytes()
    info = plistlib.loads(files['Resources/Info.plist'])
    version = info.get('NativolReleaseVersion', info['CFBundleShortVersionString'])
    if not all(c.isalnum() or c in '.-_' for c in version):
        raise ValueError('Unsafe version')
    manifest = {'schemaVersion': 1, 'kind': 'nativol-corresponding-source', 'appVersion': version,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files['SOURCE-MANIFEST.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    archive = out / ('Nativol-' + version + '-Source.tar.gz')
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=buffer, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for name, data in sorted(files.items()):
                entry = tarfile.TarInfo('Nativol-Source/' + name)
                entry.size = len(data)
                entry.mode = 0o755 if name != 'SOURCE-MANIFEST.json' and (ROOT / name).stat().st_mode & stat.S_IXUSR else 0o644
                entry.mtime = 0
                entry.uid = entry.gid = 0
                entry.uname = entry.gname = ''
                tar.addfile(entry, io.BytesIO(data))
    payload = buffer.getvalue()
    if archive.exists() and archive.read_bytes() != payload:
        raise ValueError('Source archive already exists with different content; remove only that generated archive before packaging again')
    archive.write_bytes(payload)
    archive.with_suffix(archive.suffix + '.sha256').write_text(hashlib.sha256(payload).hexdigest() + '  ' + archive.name + '\n')
    print(archive)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
