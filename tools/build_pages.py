"""Build a bounded Pages artifact from immutable, digest-pinned Release assets."""
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import tempfile

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
    links = []
    for release in releases:
        assert set(release) == {'Version', 'Tag', 'Assets', 'SourceProvenance'}
        version = release['Version']
        assert re.fullmatch(r'\d+\.\d+\.\d+', version) and version not in versions
        versions.add(version)
        assert release['Tag'] == 'v' + version and isinstance(release['SourceProvenance'], str)
        remote = json.loads(run('gh', 'api', f'repos/{REPOSITORY}/releases/tags/{release["Tag"]}'))
        assert remote.get('immutable') is True and not remote['draft'] and not remote['prerelease']
        remote_assets = {a['name']: a for a in remote['assets']}
        assets = release['Assets']
        assert set(assets) == {'Guardly.AppImage', 'manifest.json', 'signature.der', 'source.tar.gz', 'NOTICE.txt'}
        directory = site / 'releases' / version
        directory.mkdir(parents=True)
        for name, expected in assets.items():
            assert set(expected) == {'Sha256', 'ByteLength'}
            assert re.fullmatch('[a-f0-9]{64}', expected['Sha256'])
            assert type(expected['ByteLength']) is int and 0 < expected['ByteLength'] < 2**31
            asset = remote_assets[name]
            assert asset['size'] == expected['ByteLength'] and asset['digest'] == 'sha256:' + expected['Sha256']
            if name == 'source.tar.gz':
                continue
            assert expected['ByteLength'] <= (256_000_000 if name == 'Guardly.AppImage' else 1_000_000)
            total += expected['ByteLength']
            assert total < 900_000_000
            run('gh', 'release', 'download', release['Tag'], '--repo', REPOSITORY,
                '--pattern', name, '--dir', str(directory))
            data = (directory / name).read_bytes()
            assert len(data) == expected['ByteLength'] and hashlib.sha256(data).hexdigest() == expected['Sha256']
        manifest = json.loads((directory / 'manifest.json').read_bytes())
        assert manifest['Version'] == version and manifest['AppId'] == authority['AppId']
        assert manifest['RuntimeIdentifier'] == authority['RuntimeIdentifier']
        assert manifest['ArchiveUrl'] == BASE + f'releases/{version}/Guardly.AppImage'
        assert manifest['Sha256'] == assets['Guardly.AppImage']['Sha256']
        assert manifest['ByteLength'] == assets['Guardly.AppImage']['ByteLength']
        with tempfile.TemporaryDirectory() as temporary:
            public_der, public_pem = Path(temporary) / 'public.der', Path(temporary) / 'public.pem'
            public_der.write_bytes(base64.b64decode(authority['PinnedKeySpkiBase64'], validate=True))
            run('openssl', 'pkey', '-pubin', '-inform', 'DER', '-in', str(public_der), '-out', str(public_pem))
            run('openssl', 'dgst', '-sha256', '-verify', str(public_pem), '-signature', str(directory / 'signature.der'), str(directory / 'manifest.json'))
        source_url = f'https://github.com/{REPOSITORY}/releases/download/{release["Tag"]}/source.tar.gz'
        links.append(f'<li>{html.escape(version)}: <a href="releases/{version}/Guardly.AppImage">AppImage</a> '
                     f'<a href="{source_url}">corresponding source</a> <a href="releases/{version}/NOTICE.txt">notices</a>'
                     f'<pre>{html.escape(json.dumps(release, indent=2))}</pre></li>')
    stable = catalog['Stable']
    assert stable is None or stable in versions
    if stable is not None:
        import datetime
        directory = site / 'releases' / stable
        manifest = json.loads((directory / 'manifest.json').read_bytes())
        expiry = datetime.datetime.fromisoformat(manifest['ExpiresAt'].replace('Z', '+00:00'))
        assert expiry > datetime.datetime.now(datetime.timezone.utc)
        target = site / 'stable'
        target.mkdir()
        for name in ('manifest.json', 'signature.der'):
            (target / name).write_bytes((directory / name).read_bytes())
    (site / '.nojekyll').touch()
    (site / 'authority.json').write_bytes(Path('authority.json').read_bytes())
    (site / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Guardly downloads</title>'
        '<h1>Guardly downloads</h1>' + ('<ul>' + ''.join(links) + '</ul>' if links else '<p>No application release is published yet.</p>')
        + '<p><a href="authority.json">Public update authority</a></p>')


if __name__ == '__main__':
    main()
