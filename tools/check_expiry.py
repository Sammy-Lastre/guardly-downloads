"""Fail the scheduled check when published metadata needs operator renewal."""
import datetime
import json
import urllib.request
import hashlib


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check(catalog, manifest, now=None):
    if catalog['Stable'] is None:
        return 'No application release is published; no expiry is due.'
    assert manifest['Version'] == catalog['Stable']['Version']
    expiry = datetime.datetime.fromisoformat(manifest['ExpiresAt'].replace('Z', '+00:00'))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if expiry - now < datetime.timedelta(hours=48):
        raise RuntimeError('Stable update metadata expires within 48 hours or has expired. The publisher must renew locally with sign_release.py refresh and deploy a new immutable metadata release. No key is available in Actions.')
    return 'Published metadata expiry is more than 48 hours away.'


def main():
    catalog = json.loads(open('catalog.json').read())
    manifest = None
    if catalog['Stable'] is not None:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open('https://sammy-lastre.github.io/guardly-downloads/stable/manifest.json', timeout=30) as response:
            assert response.status == 200
            data = response.read(16385)
            assert len(data) <= 16384
            expected = catalog['Stable']['Assets']['manifest.json']
            assert len(data) == expected['ByteLength'] and hashlib.sha256(data).hexdigest() == expected['Sha256'], 'Deployed manifest differs from the reviewed catalog; inspect deployment before renewal.'
            manifest = json.loads(data)
    print(check(catalog, manifest))


if __name__ == '__main__':
    main()
