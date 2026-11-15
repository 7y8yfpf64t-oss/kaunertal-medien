#!/usr/bin/env python3
"""Rebuild one content-addressed map pack from a verified base and binary recipe.

Usage from the media repository root: python3 scripts/apply_pck_delta.py RECIPE.json
Only the Python standard library is required; neither network nor Godot is used.
"""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def reconstruct(recipe_path, source, target):
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    if recipe.get('schema') != 1:
        raise ValueError('Unsupported map recipe schema')
    src, dst = recipe['source'], recipe['target']
    if source.stat().st_size != src['size'] or digest(source) != src['sha256']:
        raise ValueError('Base map size or SHA-256 does not match the recipe')
    if target.exists():
        if target.stat().st_size == dst['size'] and digest(target) == dst['sha256']:
            print('Already verified:', target, dst['sha256'])
            return
        raise ValueError('Refusing to replace an existing different target')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with source.open('rb') as original, tempfile.NamedTemporaryFile(
            mode='wb', dir=target.parent, prefix=target.name + '.', suffix='.building', delete=False
        ) as output:
            temporary = Path(output.name)
            written = 0
            checksum = hashlib.sha256()
            for op in recipe['ops']:
                if op[0] == 'copy' and len(op) == 3:
                    offset, length = op[1:]
                    if type(offset) is not int or type(length) is not int or offset < 0 or length < 0 or offset + length > src['size']:
                        raise ValueError('Copy range lies outside the verified source')
                    original.seek(offset)
                    remaining = length
                    while remaining:
                        data = original.read(min(remaining, 1024 * 1024))
                        if not data:
                            raise ValueError('Unexpected end of verified base map')
                        output.write(data)
                        checksum.update(data)
                        written += len(data)
                        remaining -= len(data)
                elif op[0] == 'data' and len(op) == 2:
                    data = base64.b64decode(op[1], validate=True)
                    output.write(data)
                    checksum.update(data)
                    written += len(data)
                else:
                    raise ValueError('Unknown map recipe operation')
                if written > dst['size']:
                    raise ValueError('Reconstruction exceeds target size')
            if written != dst['size'] or checksum.hexdigest() != dst['sha256']:
                raise ValueError('Reconstructed map size or SHA-256 differs from the export')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        print('Reconstructed and verified:', target, written, dst['sha256'])
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pack_path(root, descriptor):
    name = descriptor.get('file', '')
    sha = descriptor.get('sha256', '')
    size = descriptor.get('size')
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise ValueError('Invalid expected SHA-256')
    if type(size) is not int or not 0 < size < 50 * 1024 * 1024:
        raise ValueError('Invalid ring package size')
    if not isinstance(name, str) or not re.fullmatch(r'map-packs/fight_club_(?:bar|dojo|gym|hall|lobby|ringroom|ringtechnik)\.' + sha[:16] + r'\.pck', name):
        raise ValueError('Map path must name a content-addressed Punch Club pack under map-packs')
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError('Map path escapes the media repository')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe', type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    recipe = json.loads(args.recipe.read_text(encoding='utf-8'))
    source = pack_path(root, recipe['source'])
    target = pack_path(root, recipe['target'])
    if source == target:
        raise ValueError('Source and target must be distinct')
    reconstruct(args.recipe, source, target)


if __name__ == '__main__':
    main()

