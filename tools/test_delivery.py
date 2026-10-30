import base64
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import build_pages as build
import check_expiry


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.previous = Path.cwd()
        os.chdir(self.temporary.name)
        self.addCleanup(os.chdir, self.previous)
        self.real_run = build.run
        self.real_run('openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', 'fixture-key.pem')
        public = self.real_run('openssl', 'pkey', '-in', 'fixture-key.pem', '-pubout', '-outform', 'DER')
        self.authority = {'SchemaVersion': 1, 'Configured': True, 'AppId': 'io.guardly.desktop',
            'RuntimeIdentifier': 'linux-x64', 'BootstrapVersion': '0.1.0', 'BootstrapSequence': 1,
            'ManifestUrl': build.BASE + 'stable/manifest.json', 'SignatureUrl': build.BASE + 'stable/signature.der',
            'PinnedKeySpkiBase64': base64.b64encode(public).decode(), 'AllowLoopbackHttpForTests': False}
        self.image = b'fixture appimage bytes'
        config = json.dumps(self.authority).encode()
        receipt = {'status': 'passed', 'testChannel': False, 'configuredChannel': True,
            'sha256': self.digest(self.image), 'byteLength': len(self.image), 'channelSha256': self.digest(config)}
        self.bundle_files = {'Guardly.AppImage': self.image, 'update-channel.json': config,
            'installation.json': json.dumps(receipt).encode(), **{n: b'fixture' for n in
            ('install.py', 'installer.py', 'release_support.py', 'consumer_process.py',
             'notices/inventory.json', 'notices/LICENSE', 'notices/THIRD_PARTY_NOTICES.md')}}
        self.remote = {'v0.1.0': {'Guardly.AppImage': self.image, 'consumer.tar.gz': self.bundle(),
            'source.tar.gz': b'fixture source', 'NOTICE.txt': b'fixture notices'}}
        self.catalog = {'SchemaVersion': 1, 'Releases': [{'Version': '0.1.0', 'Tag': 'v0.1.0',
            'SourceProvenance': 'fixture revision', 'Assets': self.identities(self.remote['v0.1.0'])}], 'Stable': None}
        self.immutable = True
        self.renew('metadata-v0.1.0-r0000', dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3))

    @staticmethod
    def digest(data): return hashlib.sha256(data).hexdigest()

    def identities(self, files):
        return {n: {'Sha256': self.digest(data), 'ByteLength': len(data)} for n, data in files.items()}

    def bundle(self):
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode='w:gz') as archive:
            for name, data in self.bundle_files.items():
                info = tarfile.TarInfo('Guardly/' + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return result.getvalue()

    def renew(self, tag, expiry, **changes):
        manifest = {'SchemaVersion': 1, 'AppId': 'io.guardly.desktop', 'RuntimeIdentifier': 'linux-x64',
            'Version': '0.1.0', 'ReleaseSequence': 1, 'ArchiveUrl': build.BASE + 'releases/0.1.0/Guardly.AppImage',
            'Sha256': self.digest(self.image), 'ByteLength': len(self.image), 'ExpiresAt': expiry.isoformat(), **changes}
        Path('manifest.json').write_text(json.dumps(manifest))
        self.real_run('openssl', 'dgst', '-sha256', '-sign', 'fixture-key.pem', '-out', 'signature.der', 'manifest.json')
        self.remote[tag] = {n: Path(n).read_bytes() for n in ('manifest.json', 'signature.der')}
        self.catalog['Stable'] = {'Version': '0.1.0', 'MetadataTag': tag, 'Assets': self.identities(self.remote[tag])}

    def fake_run(self, *args):
        if args[:2] == ('gh', 'api'):
            tag = args[2].split('/')[-1]
            return json.dumps({'immutable': self.immutable, 'draft': False, 'prerelease': False,
                'assets': [{'name': n, 'size': len(d), 'digest': 'sha256:' + self.digest(d)}
                           for n, d in self.remote[tag].items()]}).encode()
        if args[:3] == ('gh', 'release', 'download'):
            name = args[args.index('--pattern') + 1]
            (Path(args[args.index('--dir') + 1]) / name).write_bytes(self.remote[args[3]][name])
            return b''
        return self.real_run(*args)

    def build(self):
        Path('authority.json').write_text(json.dumps(self.authority))
        Path('catalog.json').write_text(json.dumps(self.catalog))
        with patch.object(build, 'run', self.fake_run):
            build.main()

    def test_renewal_changes_metadata_and_preserves_binary_and_installer_link(self):
        self.build()
        binary = Path('site/releases/0.1.0/Guardly.AppImage').read_bytes()
        previous = Path('site/stable/manifest.json').read_bytes()
        shutil.rmtree('site')
        self.renew('metadata-v0.1.0-r0001', dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6))
        self.build()
        self.assertEqual(binary, Path('site/releases/0.1.0/Guardly.AppImage').read_bytes())
        self.assertNotEqual(previous, Path('site/stable/manifest.json').read_bytes())
        self.assertIn('consumer.tar.gz', Path('site/index.html').read_text())
        self.assertFalse(Path('site/releases/0.1.0/consumer.tar.gz').exists())

    def test_expired_metadata_rejected(self):
        self.renew('metadata-v0.1.0-r0001', dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
        with self.assertRaises(AssertionError): self.build()

    def test_signed_wrong_binary_url_rejected(self):
        self.renew('metadata-v0.1.0-r0001', dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3), ArchiveUrl='https://example.com/wrong')
        with self.assertRaises(AssertionError): self.build()

    def test_replaced_signature_rejected_even_when_catalog_hash_matches(self):
        tag = self.catalog['Stable']['MetadataTag']
        self.remote[tag]['signature.der'] = b'tampered'
        self.catalog['Stable']['Assets'] = self.identities(self.remote[tag])
        with self.assertRaises(subprocess.CalledProcessError): self.build()

    def test_immutable_release_required(self):
        self.immutable = False
        with self.assertRaises(AssertionError): self.build()

    def test_installer_embedded_image_mismatch_rejected(self):
        self.bundle_files['Guardly.AppImage'] = b'wrong image'
        self.remote['v0.1.0']['consumer.tar.gz'] = self.bundle()
        self.catalog['Releases'][0]['Assets'] = self.identities(self.remote['v0.1.0'])
        with self.assertRaises(AssertionError): self.build()

    def test_expiry_monitor_threshold_and_empty_scaffold(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.assertIn('No application', check_expiry.check({'Stable': None}, None, now))
        self.assertIn('more than', check_expiry.check(self.catalog, {'Version': '0.1.0', 'ExpiresAt': (now + dt.timedelta(hours=49)).isoformat()}, now))
        with self.assertRaises(RuntimeError):
            check_expiry.check(self.catalog, {'Version': '0.1.0', 'ExpiresAt': (now + dt.timedelta(hours=47)).isoformat()}, now)


if __name__ == '__main__': unittest.main()
