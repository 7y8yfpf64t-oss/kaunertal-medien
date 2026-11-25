import base64, hashlib, json, os, pathlib, subprocess, tempfile, urllib.error, urllib.request
from datetime import datetime, timezone, timedelta
repo = '7y8yfpf64t-oss/kaunertal-medien'
parent = '5c285300c1fce1187bee35af4e2b373277780c04'
candidate = 'b3fd078f25c929d792eee54bb4693879f41b8421'
filename = 'map-packs/fight_club_ringroom.179da0608870dae0.pck'
dest = 'refs/heads/arcade-118-media-verified'
base = 'https://api.github.com/repos/'+repo+'/'
def api(path, data=None):
    req = urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json','User-Agent':'kaunertal-media-release'})
    with urllib.request.urlopen(req,timeout=50) as response:
        return json.load(response)
def verify_ref():
    return api('git/ref/'+dest[5:])['object']['sha']==candidate
if api('git/ref/heads/main')['object']['sha'] not in (parent,candidate):
    raise RuntimeError('Production parent changed')
root = pathlib.Path.cwd()
with tempfile.TemporaryDirectory() as tmp:
    ring = pathlib.Path(tmp)/'ring.pck'
    original = 'https://raw.githubusercontent.com/'+repo+'/'+parent+'/map-packs/fight_club_ringroom.68b26fd03ae085af.pck'
    with urllib.request.urlopen(original,timeout=40) as response:
        ring.write_bytes(response.read())
    assert hashlib.sha256(ring.read_bytes()).hexdigest()=='68b26fd03ae085af2d0e979f595f8b2a900ebbc286981e503d01ab6412802487'
    subprocess.run(['git','apply','--binary',str(root/'.ci/arcade118/ring.patch')],cwd=tmp,check=True)
    body=ring.read_bytes()
    assert len(body)==8029880 and hashlib.sha256(body).hexdigest()=='179da0608870dae09e5732129379d1b0d916a6eb807608951ff39e94aba000fe'
    blob=api('git/blobs',{'encoding':'base64','content':base64.b64encode(body).decode()})['sha']
    assert blob=='9247f4525d475ae356201e024f4f7a56136d5792'
    print('Verified ring blob',blob,flush=True)
tree=api('git/trees',{'base_tree':'88c6fd58e82c10967786c298dacb3a0a0f17e1fc','tree':[{'path':filename,'type':'blob','mode':'100644','sha':blob}]})['sha']
assert tree=='a3e49289cde1e4ecf28e96a2f683b9f6e42dabc2'
print('Verified ring tree',tree,flush=True)
# Send a standard Git receive-pack request for the four new objects only.
# The existing parent tree has hundreds of large assets and is already on origin.
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(['git','init','--bare','--quiet',tmp],check=True)
    def git(args,data):
        return subprocess.check_output(['git','-C',tmp]+args,input=data)
    assert git(['hash-object','-w','--stdin'],body).decode().strip()==blob
    sub='73057b12e1af7e74c0c95927f7be8d1d43b6e0ca'
    for address in (sub,tree):
        rows=api('git/trees/'+address)['tree']
        source=b''.join((row['mode'].lstrip('0')+' '+row['path']).encode()+b'\x00'+bytes.fromhex(row['sha']) for row in rows)
        assert git(['hash-object','-t','tree','-w','--stdin'],source).decode().strip()==address
    assert git(['hash-object','-t','commit','-w','--stdin'],(root/'.ci/arcade118/commit.txt').read_bytes()).decode().strip()==candidate
    pack=git(['pack-objects','--stdout','--no-reuse-delta','--window=0'],('\n'.join([blob,sub,tree,candidate])+'\n').encode())
    assert pack[:4]==b'PACK' and int.from_bytes(pack[8:12],'big')==4
headers={'Authorization':'Basic '+base64.b64encode(('x-access-token:'+os.environ['GH_TOKEN']).encode()).decode(),'User-Agent':'git/2.47.0'}
git_url='https://github.com/'+repo+'.git'
with urllib.request.urlopen(urllib.request.Request(git_url+'/info/refs?service=git-receive-pack',headers=headers),timeout=30) as response:
    refs=response.read()
assert (parent+' refs/heads/main').encode() in refs or (candidate+' refs/heads/main').encode() in refs
if (candidate+' '+dest).encode() not in refs:
    line=('0'*40+' '+candidate+' '+dest+'\x00report-status\n').encode()
    payload=('%04x'%(4+len(line))).encode()+line+b'0000'+pack
    post={**headers,'Content-Type':'application/x-git-receive-pack-request','Accept':'application/x-git-receive-pack-result'}
    with urllib.request.urlopen(urllib.request.Request(git_url+'/git-receive-pack',headers=post,data=payload),timeout=75) as response:
        receipt=response.read().decode()
    print('Git receive-pack',receipt,flush=True)
    assert 'unpack ok' in receipt and ('ok '+dest) in receipt or verify_ref()
assert verify_ref()
print('Verified exact canonical commit',candidate,'on',dest)
