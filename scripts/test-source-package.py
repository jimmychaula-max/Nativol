#!/usr/bin/env python3
"""Exercise source-package boundaries in temporary fixtures, without compiling."""
import contextlib
import importlib.util
import io
import json
import pathlib
import plistlib
import shutil
import tarfile
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_source', REPO / 'scripts/package-source.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nativol-source-package-test-')
        self.root = pathlib.Path(self.temp.name)
        self.old_root = packager.ROOT
        packager.ROOT = self.root
        for name in packager.REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith('third-party/sources/'):
                shutil.copyfile(REPO / name, path)
            else:
                path.write_text('test source fixture\n')
        (self.root / 'Resources').mkdir()
        (self.root / 'Resources/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString': '0.5.0', 'NativolReleaseVersion': '0.5.0-beta.1'}))
        self.archive = self.root / 'dist/Nativol-0.5.0-beta.1-Source.tar.gz'

    def tearDown(self):
        packager.ROOT = self.old_root
        self.temp.cleanup()

    def run_package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            packager.main()

    def test_complete_source_and_hashes_without_private_files(self):
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('private remote\n')
        (self.root / 'local-reports').mkdir()
        (self.root / 'local-reports/private.json').write_text('private card\n')
        (self.root / 'site').mkdir()
        (self.root / 'site/index.html').write_text('<title>Nativol</title>')
        self.run_package()
        with tarfile.open(self.archive) as archive:
            names = set(archive.getnames())
            self.assertIn('Nativol-Source/third-party/sources/ntfs-3g_ntfsprogs-2026.9.28.tgz', names)
            self.assertIn('Nativol-Source/site/index.html', names)
            self.assertNotIn('Nativol-Source/.git/config', names)
            self.assertNotIn('Nativol-Source/local-reports/private.json', names)
            manifest = json.load(archive.extractfile('Nativol-Source/SOURCE-MANIFEST.json'))
            self.assertEqual(manifest['appVersion'], '0.5.0-beta.1')
            self.assertIn('engine/nativol-ntfs-health.c', manifest['files'])

    def test_identical_output_is_repeatable_and_changed_output_refused(self):
        self.run_package()
        before = self.archive.read_bytes()
        self.run_package()
        self.assertEqual(before, self.archive.read_bytes())
        (self.root / 'README.md').write_text('changed\n')
        with self.assertRaises(ValueError):
            self.run_package()
        self.assertEqual(before, self.archive.read_bytes())

    def test_onboarding_evidence_is_excluded_but_public_donation_assets_remain(self):
        private_files = (
            'FORK_REPORT.md', 'SANITIZATION_REPORT.md',
            'docs/BUY-ME-A-COFFEE-PROFILE.md',
            'docs/buy-me-a-coffee-signup.jpg', 'docs/buy-me-a-coffee-profile-setup.jpg',
            'docs/buy-me-a-coffee-profile-live.jpg', 'docs/binance-pay-receive.jpg',
            'docs/binance-pay-desktop-preview.jpg', 'docs/binance-pay-mobile-preview.jpg',
            'docs/nativol-profile.png',
        )
        public_files = ('docs/DONATIONS.md', 'site/assets/binance-pay-receive.jpg',
                        'site/assets/nativol.svg')
        for name in private_files + public_files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic fixture\n')
        self.run_package()
        before = self.archive.read_bytes()
        with tarfile.open(self.archive) as archive:
            names = set(archive.getnames())
            manifest = json.load(archive.extractfile('Nativol-Source/SOURCE-MANIFEST.json'))
            for name in private_files:
                self.assertNotIn('Nativol-Source/' + name, names)
                self.assertNotIn(name, manifest['files'])
            for name in public_files:
                self.assertIn('Nativol-Source/' + name, names)
                self.assertIn(name, manifest['files'])
        # Local account evidence must not change the public archive or its checksum.
        for name in private_files:
            (self.root / name).write_bytes(b'updated synthetic private fixture\n')
        self.run_package()
        self.assertEqual(before, self.archive.read_bytes())

    def test_source_symlink_is_refused(self):
        (self.root / 'engine/linked.c').symlink_to(self.root / 'LICENSE')
        with self.assertRaises(ValueError):
            self.run_package()

    def test_vendor_library_is_refused(self):
        (self.root / 'engine/vendor.dylib').write_bytes(b'library')
        with self.assertRaises(ValueError):
            self.run_package()

    def test_changed_upstream_archive_is_refused(self):
        (self.root / 'third-party/sources/ntfs-3g_ntfsprogs-2026.9.28.tgz').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            self.run_package()


if __name__ == '__main__':
    unittest.main()
