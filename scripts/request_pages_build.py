#!/usr/bin/env python3
"""Request the pinned repository's existing Pages build and verify its media."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sync_original_media import FILES, REPOSITORY, load_manifest

API_ROOT = 'https://api.github.com/repos/' + REPOSITORY
PAGES_ORIGIN = 'https://7y8yfpf64t-oss.github.io/kaunertal-medien'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise RuntimeError('Redirect rejected for a pinned GitHub URL')


def api_request(method, path):
    allowed = {
        ('GET', '/pages'), ('GET', '/git/ref/heads/main'),
        ('POST', '/pages/builds'), ('GET', '/pages/builds/latest'),
    }
    if (method, path) not in allowed:
        raise ValueError('Unapproved GitHub API operation')
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise RuntimeError('The repository-scoped workflow token is missing')
    request = Request(API_ROOT + path, method=method, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'Kaunertal-Original-Media-Sync/1',
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            expected = 201 if method == 'POST' else 200
            if response.status != expected:
                raise RuntimeError('Unexpected GitHub API status: ' + str(response.status))
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError('GitHub Pages/API request failed: ' + method + ' ' + path +
                           ' HTTP ' + str(error.code) + '; check repository token permissions and Pages build support') from None


def verify_public_files(root):
    files = load_manifest(root)
    total = 0
    for name in FILES:
        url = PAGES_ORIGIN + '/' + name
        request = Request(url, headers={'Accept-Encoding': 'identity',
                                       'User-Agent': 'Kaunertal-Original-Media-Sync/1'})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.url != url:
                raise RuntimeError('Published original did not return its pinned URL: ' + name)
            size, sha = 0, hashlib.sha256()
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > files[name]['size']:
                    raise RuntimeError('Published original exceeds expected size: ' + name)
                sha.update(block)
        if size != files[name]['size'] or sha.hexdigest() != files[name]['sha256']:
            raise RuntimeError('Published original failed size/SHA256 verification: ' + name)
        print('Published original verified: ' + name + ' (' + str(size) + ' bytes)', flush=True)
        total += size
    return {'verified_files': len(FILES), 'verified_bytes': total}


def verify_public_with_retry(root):
    # A successful build can precede CDN propagation. Never turn a mismatch into success.
    for attempt in range(6):
        try:
            return verify_public_files(root)
        except (RuntimeError, OSError) as error:
            if attempt == 5:
                raise RuntimeError('Pages built, but the six public originals could not all be verified: ' + str(error)) from None
            print('Public verification is not complete; retrying after CDN propagation (' + str(attempt + 1) + '/6)', flush=True)
            time.sleep(10)


def publish(expected_commit, *, api, verify_public, sleep=time.sleep, attempts=60):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise ValueError('Expected commit must be a complete Git SHA')
    site = api('GET', '/pages')
    if (site.get('source') != {'branch': 'main', 'path': '/'} or
            site.get('html_url', '').rstrip('/') != PAGES_ORIGIN or
            site.get('build_type', 'legacy') != 'legacy'):
        raise RuntimeError('Pages must already use the pinned main/root branch and public URL; no settings were changed')
    if api('GET', '/git/ref/heads/main').get('object', {}).get('sha') != expected_commit:
        raise RuntimeError('Remote main changed after checkout/push; rerun safely from the latest main')
    requested = api('POST', '/pages/builds')
    if (requested.get('url') != API_ROOT + '/pages/builds/latest' or
            requested.get('status') not in {'queued', 'building', 'built'}):
        raise RuntimeError('GitHub did not confirm the expected Pages build request')
    print('Pages build requested for commit ' + expected_commit, flush=True)
    for attempt in range(attempts):
        build = api('GET', '/pages/builds/latest')
        status, commit = build.get('status'), build.get('commit')
        print('Pages poll ' + str(attempt + 1) + ': ' + str(status) + ' commit=' + str(commit), flush=True)
        if status not in {'queued', 'building', 'built', 'errored'}:
            raise RuntimeError('Unknown Pages build status: ' + str(status))
        if commit == expected_commit:
            if status == 'errored' or (build.get('error') or {}).get('message'):
                raise RuntimeError('Pages build failed for the expected commit; inspect the repository Pages build log')
            if status == 'built':
                result = verify_public()
                if result.get('verified_files') != len(FILES):
                    raise RuntimeError('Public verification did not confirm all six originals')
                return {'status': 'built', 'commit': expected_commit, **result}
        if attempt + 1 < attempts:
            sleep(10)
    raise RuntimeError('Pages build timed out without confirming the expected commit; no successful publication claimed')


if __name__ == '__main__':
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Run only in the pinned GitHub repository on main')
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    print(json.dumps(publish(commit, api=api_request, verify_public=lambda: verify_public_with_retry(root)), indent=2))
