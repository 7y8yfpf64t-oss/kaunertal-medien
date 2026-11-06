#!/usr/bin/env python3
"""Copy six pinned public originals into this media repository, without re-encoding."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = '7y8yfpf64t-oss/kaunertal-medien'
SOURCE_ORIGIN = 'https://kaunertal.cc'
FILES = (
    'kaunertal-trailer-v6.mp4',
    'kaunertal-trailer-poster.jpg',
    'web_audio/neoncholy_1up_2_few.mp3',
    'web_audio/neoncholy_legacy_of_crosbo.mp3',
    'web_audio/neoncholy_sunday_glide.mp3',
    'web_audio/neoncholy_punch_up.mp3',
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_manifest(root):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate manifest key: ' + key)
            result[key] = value
        return result
    manifest = json.loads((root / 'media-sync.json').read_text(), object_pairs_hook=unique)
    if (set(manifest) != {'schema', 'repository', 'source_origin', 'files'} or
            manifest['schema'] != 1 or manifest['repository'] != REPOSITORY or
            manifest['source_origin'] != SOURCE_ORIGIN or set(manifest['files']) != set(FILES)):
        raise ValueError('Manifest must use the pinned repository, origin and exact six paths')
    for name, item in manifest['files'].items():
        if (set(item) != {'size', 'sha256'} or type(item['size']) is not int or
                not 0 < item['size'] < 100 * 1024 * 1024 or
                not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
            raise ValueError('Invalid original size or checksum: ' + name)
    return manifest['files']


def target_path(root, name):
    if name not in FILES:
        raise ValueError('Unapproved destination path')
    target = root / name
    for path in [target, *target.parents]:
        if path == root:
            break
        if path.is_symlink():
            raise ValueError('Symlink destination is forbidden: ' + name)
    if target.exists() and not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('Destination must be a regular file: ' + name)
    if target.parent.exists() and not target.parent.is_dir():
        raise ValueError('Destination parent is not a directory: ' + name)
    return target


def matches(path, expected):
    return path.is_file() and path.stat().st_size == expected['size'] and digest(path) == expected['sha256']


class PinnedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # The verified source URL is the entire pin, not a user-supplied host.
        if newurl != request.full_url:
            raise ValueError('Source redirect differs from the pinned public URL')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, destination, expected):
    if url not in {SOURCE_ORIGIN + '/' + name for name in FILES}:
        raise ValueError('Unapproved source URL')
    request = Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'Kaunertal-Original-Media-Sync/1'})
    for attempt in range(3):
        try:
            with build_opener(PinnedRedirect()).open(request, timeout=30) as response:
                if response.status != 200 or response.url != url:
                    raise ValueError('Pinned public source did not return HTTP 200')
                size = 0
                with destination.open('wb') as output:
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > expected['size']:
                            raise ValueError('Downloaded file exceeds its pinned original size')
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            if not matches(destination, expected):
                raise ValueError('Downloaded original failed size/SHA256 verification')
            return
        except (URLError, TimeoutError, OSError):
            destination.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def sync(root, *, fetch=download):
    root = Path(root).resolve()
    files = load_manifest(root)
    original = {}
    changed, skipped = [], []
    for name in FILES:
        path = target_path(root, name)
        original[name] = digest(path) if path.exists() else None
        (skipped if matches(path, files[name]) else changed).append(name)
    # All complete downloads are verified before any repository file is changed.
    # The temporary directory lives on the destination filesystem for os.replace.
    with tempfile.TemporaryDirectory(prefix='.media-sync-', dir=root) as temporary:
        staging = Path(temporary)
        for number, name in enumerate(changed):
            downloaded = staging / str(number)
            fetch(SOURCE_ORIGIN + '/' + name, downloaded, files[name])
            if downloaded.is_symlink() or not matches(downloaded, files[name]):
                raise ValueError('Staged original failed size/SHA256 verification: ' + name)
        for name in FILES:
            path = target_path(root, name)
            current = digest(path) if path.exists() else None
            if current != original[name]:
                raise ValueError('Destination changed during verification: ' + name)
        for number, name in enumerate(changed):
            path = target_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / str(number), path)
        for name in FILES:
            if not matches(target_path(root, name), files[name]):
                raise ValueError('Final repository original failed verification: ' + name)
    return {'repository': REPOSITORY, 'changed': changed, 'skipped': skipped,
            'verified_files': len(FILES), 'verified_bytes': sum(item['size'] for item in files.values())}


if __name__ == '__main__':
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Run only in the pinned GitHub repository on main')
    print(json.dumps(sync(Path(__file__).resolve().parents[1]), indent=2))
