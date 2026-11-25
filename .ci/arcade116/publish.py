"""Stage canonical V115 inputs and create a verified candidate containing only approved V116 blobs."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

PIN = '6c5596ec595d19f167cbb69e4d3969bb4eafee81'
PROOF = '9be489806a6de8d61fc3f76a26a3fa9f4276a3d92cd839254a242a8824ea9f3d'
REPO = '7y8yfpf64t-oss/kaunertal-medien'
CANDIDATE = 'refs/heads/arcade116-verified-media'
RECIPE = Path(__file__).resolve().parent
NAME = re.compile(r'map-packs/fight_club_(?:fighter_[a-z0-9_]+|ringroom)\.([0-9a-f]{16})\.pck')
BOT = {'GIT_AUTHOR_NAME':'github-actions[bot]', 'GIT_COMMITTER_NAME':'github-actions[bot]',
       'GIT_AUTHOR_EMAIL':'41898282+github-actions[bot]@users.noreply.github.com',
       'GIT_COMMITTER_EMAIL':'41898282+github-actions[bot]@users.noreply.github.com'}

def check(ok, message):
    if not ok:
        raise ValueError(message)

def proof():
    raw = (RECIPE/'expected.json').read_bytes()
    check(hashlib.sha256(raw).hexdigest() == PROOF, 'Expected release proof differs from the local acceptance')
    manifest = json.loads(raw)
    expected = {}
    for f in manifest['files']:
        path = f['path']
        m = NAME.fullmatch(path)
        check(m and f['sha256'].startswith(m[1]) and len(f['sha256']) == 64 and re.fullmatch('[0-9a-f]{40}', f['git_blob']), path)
        check(path not in expected and 0 < f['size'] < 50*1024**2, path)
        expected[path] = f
    check(manifest['previous_media_pin'] == PIN and len(expected) == 73, 'Wrong release or file count')
    return expected

def inputs(dest):
    proof()
    layouts = [json.loads((RECIPE/'ring.json').read_bytes())]
    layouts.extend(json.loads(gzip.decompress(p.read_bytes())) for p in sorted((RECIPE/'layouts').glob('*.json.gz')))
    check(len(layouts) == 25, 'Expected 24 fighters and one ring')
    files = {}
    for layout in layouts:
        for f in layout['input']:
            name = f['file']
            match = NAME.fullmatch(name)
            check(match and f['sha256'].startswith(match[1]) and (name not in files or files[name] == f), name)
            files[name] = f
    (dest/'map-packs').mkdir(parents=True, exist_ok=True)
    def download(f):
        path = dest/f['file']
        url = f'https://raw.githubusercontent.com/{REPO}/{PIN}/{f["file"]}'
        subprocess.run(['curl','--silent','--show-error','--fail','--retry','4','--retry-all-errors',
                        '--connect-timeout','20','--max-time','150','--output',str(path),url],check=True)
        check(path.stat().st_size == f['size'] and hashlib.sha256(path.read_bytes()).hexdigest() == f['sha256'],f['file'])
    with ThreadPoolExecutor(max_workers=5) as pool:
        for ready in as_completed(pool.submit(download,f) for f in files.values()): ready.result()
    print(f'{len(files)} verified inputs at {PIN}')

def verified_output(root):
    expected = proof()
    present = {p.relative_to(root).as_posix() for p in root.rglob('*') if not p.is_dir()}
    check(present == set(expected), 'Output does not contain exactly the 73 approved PCKs')
    for path,f in expected.items():
        file = root/path
        check(file.is_file() and not file.is_symlink() and file.stat().st_size == f['size'],path)
        data = file.read_bytes()
        check(hashlib.sha256(data).hexdigest() == f['sha256'],path+' SHA-256')
        header = b'blob '+str(len(data)).encode()+b'\0'
        check(hashlib.sha1(header+data).hexdigest() == f['git_blob'],path+' Git blob')
    return expected

def make_commit(repo, root, expected, parent, ancestor):
    def git(*args,**kwargs):
        return subprocess.check_output(['git','-C',str(repo),*args],text=True,**kwargs).strip()
    git('merge-base','--is-ancestor',ancestor,parent)
    lines = git('ls-tree','-r',parent,'--','map-packs')
    previous = {line.split('\t',1)[1]:line.split('\t',1)[0] for line in lines.splitlines()}
    with tempfile.TemporaryDirectory() as t:
        env = {**os.environ,**BOT,'GIT_INDEX_FILE':str(Path(t)/'index')}
        git('read-tree',parent,env=env)
        added = set()
        for path,f in expected.items():
            mode = '100644 blob '+f['git_blob']
            if path in previous:
                check(previous[path]==mode,'Refusing to replace '+path)
                continue
            oid = git('hash-object','--no-filters','-w',str(root/path))
            check(oid == f['git_blob'],path)
            git('update-index','--add','--cacheinfo','100644,'+oid+','+path,env=env)
            added.add(path)
        if not added: return parent
        tree = git('write-tree',env=env)
        delta = git('diff-tree','--no-renames','-r','--name-status',parent+'^{tree}',tree)
        check(set(delta.splitlines()) == {'A\t'+path for path in added},'Production tree changed outside the approved additions')
        return git('commit-tree',tree,'-p',parent,'-m','Arcade 116: 73 verified immutable media packs',env=env)

def publish(root):
    expected = verified_output(root)
    check(os.getenv('GITHUB_REPOSITORY') == REPO,'Wrong production repository')
    with tempfile.TemporaryDirectory() as t:
        repo = Path(t)/'production.git'
        subprocess.run(['git','clone','--quiet','--bare','--filter=blob:none','--single-branch','--branch','main',
                        f'https://github.com/{REPO}.git',str(repo)],check=True)
        git = ['git','-C',str(repo)]
        parent = subprocess.check_output(git+['rev-parse','refs/heads/main'],text=True).strip()
        commit = make_commit(repo,root,expected,parent,PIN)
        existing = subprocess.check_output(git+['ls-remote','--refs','origin',CANDIDATE],text=True).strip()
        if existing:
            subprocess.run(git+['fetch','--quiet','--filter=blob:none','origin',CANDIDATE],check=True)
            existing = subprocess.check_output(git+['rev-parse','FETCH_HEAD'],text=True).strip()
            tree = subprocess.check_output(git+['rev-parse',commit+'^{tree}'],text=True).strip()
            existing_tree = subprocess.check_output(git+['rev-parse',existing+'^{tree}'],text=True).strip()
            parents = subprocess.check_output(git+['show','-s','--format=%P',existing],text=True).split()
            check(existing_tree==tree and (existing==parent or parents==[parent]),
                  'Existing candidate differs from the approved tree or production base')
            commit = existing
        else:
            subprocess.run(git+['-c','credential.helper=!gh auth git-credential','push','origin',commit+':'+CANDIDATE],check=True)
        remote = subprocess.check_output(git+['ls-remote','--refs','origin',CANDIDATE],text=True).split()
        check(remote==[commit,CANDIDATE], 'Candidate ref did not resolve to the approved commit')
        with open(os.environ['GITHUB_OUTPUT'],'a') as out:
            print('commit='+commit,file=out)
            print('base='+parent,file=out)
        print('Verified candidate:',commit,'production base:',parent)
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as out:
            print(f'Candidate [{commit}](https://github.com/{REPO}/commit/{commit}) contains all 73 verified media blobs. Production base: `{parent}`. The workflow published only `{CANDIDATE}`.',file=out)

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['inputs','verify','publish'])
    parser.add_argument('directory',type=Path)
    args = parser.parse_args()
    {'inputs':inputs,'verify':verified_output,'publish':publish}[args.action](args.directory.resolve())
