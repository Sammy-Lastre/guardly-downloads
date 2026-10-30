"""Build a bounded Pages artifact from immutable, digest-pinned Release assets."""
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import tempfile
import datetime
import tarfile

REPOSITORY = 'Sammy-Lastre/guardly-downloads'
BASE = 'https://sammy-lastre.github.io/guardly-downloads/'


def run(*args):
    return subprocess.check_output(args, timeout=180)


def main():
    catalog = json.loads(Path('catalog.json').read_text())
    authority = json.loads(Path('authority.json').read_text())
    assert set(catalog) == {'SchemaVersion', 'Releases', 'Stable'} and catalog['SchemaVersion'] == 1
    releases = catalog['Releases']
    assert len(releases) <= 3
    site = Path('site')
    site.mkdir(exist_ok=False)
    total = 0
    versions = set()
    sequences = {}
    links = []
    for release in releases:
        assert set(release) == {'Version', 'Tag', 'SourceTag', 'Assets', 'SourceProvenance'}
        version = release['Version']
        assert re.fullmatch(r'\d+\.\d+\.\d+', version) and version not in versions
        versions.add(version)
        assert release['Tag'] == 'v' + version and isinstance(release['SourceProvenance'], str)
        remote = json.loads(run('gh', 'api', f'repos/{REPOSITORY}/releases/tags/{release["Tag"]}'))
        assert remote.get('immutable') is True and not remote['draft'] and not remote['prerelease']
        remote_assets = {a['name']: a for a in remote['assets']}
        source_tag = release['SourceTag']
        assert isinstance(source_tag, str) and re.fullmatch(r'source-[A-Za-z0-9._-]{1,80}', source_tag)
        source_remote = json.loads(run('gh', 'api', f'repos/{REPOSITORY}/releases/tags/{source_tag}'))
        assert source_remote.get('immutable') is True and not source_remote['draft'] and not source_remote['prerelease']
        source_assets = {a['name']: a for a in source_remote['assets']}
        assets = release['Assets']
        assert set(assets) == {'Guardly.AppImage', 'consumer.tar.gz', 'source.tar.gz', 'NOTICE.txt'}
        directory = site / 'releases' / version
        directory.mkdir(parents=True)
        for name, expected in assets.items():
            assert set(expected) == {'Sha256', 'ByteLength'}
            assert re.fullmatch('[a-f0-9]{64}', expected['Sha256'])
            assert type(expected['ByteLength']) is int and 0 < expected['ByteLength'] < 2**31
            asset = source_assets[name] if name == 'source.tar.gz' else remote_assets[name]
            assert asset['size'] == expected['ByteLength'] and asset['digest'] == 'sha256:' + expected['Sha256']
            if name == 'source.tar.gz':
                continue
            assert expected['ByteLength'] <= (256_000_000 if name in ('Guardly.AppImage', 'consumer.tar.gz') else 1_000_000)
            total += expected['ByteLength'] if name != 'consumer.tar.gz' else 0
            assert total < 900_000_000
            run('gh', 'release', 'download', release['Tag'], '--repo', REPOSITORY,
                '--pattern', name, '--dir', str(directory))
            data = (directory / name).read_bytes()
            assert len(data) == expected['ByteLength'] and hashlib.sha256(data).hexdigest() == expected['Sha256']
        # Inspect the managed-install bootstrap without executing or extracting it.
        with tarfile.open(directory / 'consumer.tar.gz', 'r:gz') as bundle:
            members = bundle.getmembers()
            names = [m.name for m in members]
            assert len(names) == len(set(names)) and len(names) <= 20000
            assert all(m.isfile() and not m.name.startswith('/') and '..' not in Path(m.name).parts for m in members)
            assert sum(m.size for m in members) <= 512_000_000
            def member(name, limit):
                info = bundle.getmember('Guardly/' + name)
                assert info.size <= limit
                return bundle.extractfile(info).read(limit + 1)
            image = member('Guardly.AppImage', 256_000_000)
            assert hashlib.sha256(image).hexdigest() == assets['Guardly.AppImage']['Sha256']
            assert len(image) == assets['Guardly.AppImage']['ByteLength']
            config = json.loads(member('update-channel.json', 16384))
            assert config == dict(authority, BootstrapVersion=version, BootstrapSequence=config['BootstrapSequence'])
            assert type(config['BootstrapSequence']) is int and config['BootstrapSequence'] > 0
            sequences[version] = config['BootstrapSequence']
            installation = json.loads(member('installation.json', 16384))
            assert installation['status'] == 'passed' and installation['testChannel'] is False and installation['configuredChannel'] is True
            assert installation['sha256'] == assets['Guardly.AppImage']['Sha256']
            assert installation['byteLength'] == assets['Guardly.AppImage']['ByteLength']
            assert installation['channelSha256'] == hashlib.sha256(member('update-channel.json', 16384)).hexdigest()
            for name in ('install.py', 'installer.py', 'release_support.py', 'consumer_process.py', 'notices/inventory.json', 'notices/LICENSE', 'notices/THIRD_PARTY_NOTICES.md'):
                assert member(name, 4_000_000)
        # Consumer/source archives remain on Releases; updater AppImages remain direct on Pages.
        (directory / 'consumer.tar.gz').unlink()
        source_url = f'https://github.com/{REPOSITORY}/releases/download/{source_tag}/source.tar.gz'
        consumer_url = f'https://github.com/{REPOSITORY}/releases/download/{release["Tag"]}/consumer.tar.gz'
        links.append(f'<li>{html.escape(version)}: <a href="{consumer_url}">Linux managed installer download</a> '
                     f'<a href="releases/{version}/Guardly.AppImage">AppImage</a> '
                     f'<a href="{source_url}">corresponding source</a> <a href="releases/{version}/NOTICE.txt">notices</a>'
                     f'<pre>{html.escape(json.dumps(release, indent=2))}</pre></li>')
    stable = catalog['Stable']
    assert stable is None or (isinstance(stable, dict) and set(stable) == {'Version', 'MetadataTag', 'Assets'} and stable['Version'] in versions)
    if stable is not None:
        version = stable['Version']
        tag = stable['MetadataTag']
        assert re.fullmatch(r'metadata-v' + re.escape(version) + r'-r[0-9]{4,}', tag)
        remote = json.loads(run('gh', 'api', f'repos/{REPOSITORY}/releases/tags/{tag}'))
        assert remote.get('immutable') is True and not remote['draft'] and not remote['prerelease']
        assets = stable['Assets']
        assert set(assets) == {'manifest.json', 'signature.der'}
        remote_assets = {a['name']: a for a in remote['assets']}
        target = site / 'stable'
        target.mkdir()
        for name in ('manifest.json', 'signature.der'):
            expected, asset = assets[name], remote_assets[name]
            assert set(expected) == {'Sha256', 'ByteLength'} and re.fullmatch('[a-f0-9]{64}', expected['Sha256'])
            assert type(expected['ByteLength']) is int and 0 < expected['ByteLength'] <= 16384
            assert asset['size'] == expected['ByteLength'] and asset['digest'] == 'sha256:' + expected['Sha256']
            run('gh', 'release', 'download', tag, '--repo', REPOSITORY, '--pattern', name, '--dir', str(target))
            data = (target / name).read_bytes()
            assert len(data) == expected['ByteLength'] and hashlib.sha256(data).hexdigest() == expected['Sha256']
        manifest = json.loads((target / 'manifest.json').read_bytes())
        binary = next(r for r in releases if r['Version'] == version)['Assets']['Guardly.AppImage']
        assert manifest['Version'] == version and manifest['AppId'] == authority['AppId']
        assert manifest['RuntimeIdentifier'] == authority['RuntimeIdentifier']
        assert manifest['ArchiveUrl'] == BASE + f'releases/{version}/Guardly.AppImage'
        assert manifest['Sha256'] == binary['Sha256'] and manifest['ByteLength'] == binary['ByteLength']
        assert manifest['ReleaseSequence'] == sequences[version]
        with tempfile.TemporaryDirectory() as temporary:
            public_der, public_pem = Path(temporary) / 'public.der', Path(temporary) / 'public.pem'
            public_der.write_bytes(base64.b64decode(authority['PinnedKeySpkiBase64'], validate=True))
            run('openssl', 'pkey', '-pubin', '-inform', 'DER', '-in', str(public_der), '-out', str(public_pem))
            run('openssl', 'dgst', '-sha256', '-verify', str(public_pem), '-signature', str(target / 'signature.der'), str(target / 'manifest.json'))
        expiry = datetime.datetime.fromisoformat(manifest['ExpiresAt'].replace('Z', '+00:00'))
        assert expiry > datetime.datetime.now(datetime.timezone.utc)
    (site / '.nojekyll').touch()
    (site / 'authority.json').write_bytes(Path('authority.json').read_bytes())
    (site / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Guardly downloads</title>'
        '<h1>Guardly downloads</h1>' + ('<ul>' + ''.join(links) + '</ul>' if links else '<p>No application release is published yet.</p>')
        + '<p>Download the Linux managed installer archive, verify its SHA-256 against this index, '
          'extract it, then run <code>python3 install.py</code> inside its Guardly directory as your ordinary user. '
          'Python 3 and working AppImage FUSE support are required. Establish trust in this publisher through '
          'an independent trusted channel before the first installation. Existing managed installations use signed updates.</p>'
        + '<p><a href="authority.json">Public update authority</a></p>')


if __name__ == '__main__':
    main()
