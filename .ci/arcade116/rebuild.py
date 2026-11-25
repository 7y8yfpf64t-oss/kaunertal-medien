"""Reassemble public sprite pages from the previous public media and positional layout recipes."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile

from PIL import Image, features

ROOT = Path(__file__).resolve().parent
ROW = struct.Struct('<QQ16sI')

def check(value, msg):
    if not value:
        raise ValueError(msg)

def checked_file(base, meta):
    name = meta['file']
    check(name.startswith('map-packs/fight_club_') and Path(name).name == name[10:], name)
    data = (base/name).read_bytes()
    check(len(data) == meta['size'] and hashlib.sha256(data).hexdigest() == meta['sha256'], name)
    return data

def unpack(data):
    check(data[:8] == b'GDPC\x04\0\0\0', 'Unsupported public pack')
    base, offset = struct.unpack_from('<QQ',data,24)
    count, = struct.unpack_from('<I',data,offset)
    result = {}
    cursor = offset + 4
    for _ in range(count):
        length, = struct.unpack_from('<I',data,cursor)
        cursor += 4
        name = data[cursor:cursor+length].rstrip(b'\0').decode()
        cursor += length
        start, size, digest, flags = ROW.unpack_from(data,cursor)
        cursor += ROW.size
        start += base
        payload = data[start:start+size]
        check(flags == 0 and name not in result and start+size<=offset, name)
        check(digest==hashlib.md5(payload).digest(), 'bad original entry '+name)
        result[name] = payload
    check(cursor == len(data), 'Trailing pack data')
    return result

def package(entries, names):
    out = io.BytesIO()
    head = bytearray(128)
    struct.pack_into('<4sIIIIIQ',head,0,b'GDPC',4,4,7,2,2,128)
    out.write(head)
    directory = []
    for n in sorted(names):
        name = n.encode()
        payload = entries[n]
        field = name + bytes(-len(name)%4)
        directory.append(struct.pack('<I',len(field))+field+
                         ROW.pack(out.tell()-128,len(payload),hashlib.md5(payload).digest(),0))
        out.write(payload)
        out.write(bytes(-out.tell()%32))
    at = out.tell()
    out.write(struct.pack('<I',len(directory)))
    out.write(b''.join(directory))
    out.seek(32)
    out.write(struct.pack('<Q',at))
    return out.getvalue()

def bake_page(src, bank, spec):
    path, width, height, expected, frames = spec
    check(src[:4] == b'GST2' and src[56:60] == b'RIFF', path)
    old = Image.open(io.BytesIO(src[56:])).convert('RGBA')
    sheet = Image.new('RGBA',(width,height))
    all_frames = (f for row in bank['cells'] for f in row)
    for old_frame, strips in zip(all_frames,frames,strict=True):
        cell = Image.new('RGBA',tuple(old_frame['rect'][2:]))
        for s in old_frame['strips']:
            x,y,w,h = s['rect']
            cell.paste(old.crop((x,y,x+w,y+h)),tuple(s['offset']))
        next_strips = []
        for ox,oy,x,y,w,h in strips:
            sheet.paste(cell.crop((ox,oy,ox+w,oy+h)),(x,y))
            next_strips.append({'offset':[ox,oy],'size':[w,h],'page':0,'rect':[x,y,w,h]})
        old_frame['strips'] = next_strips
    output = io.BytesIO()
    sheet.save(output,format='WEBP',lossless=True,method=4,quality=80,exact=False)
    image = output.getvalue()
    header = bytearray(src[:52])
    struct.pack_into('<II',header,8,width,height)
    struct.pack_into('<HH',header,40,width,height)
    result = bytes(header)+struct.pack('<I',len(image))+image
    check(hashlib.sha256(result).hexdigest()==expected, 'CTEX mismatch: '+path)
    return result

def recipe(job):
    return json.loads(gzip.decompress(job.read_bytes()) if job.suffix=='.gz' else job.read_bytes())

def rebuild(job,entries,output):
    if 'replace' in job:
        for entry, meta in job['replace'].items():
            payload = (ROOT/meta['file']).read_bytes()
            check(entry in entries and hashlib.sha256(payload).hexdigest()==meta['sha256'],entry)
            entries[entry] = payload
    else:
        cel = job['cel']
        data = json.loads(entries[cel])
        for section,spec in job['sections'].items():
            bank = data['super'] if section=='@super' else data['actions'][section]
            texture = spec[0]
            entries[texture] = bake_page(entries[texture],bank,spec)
        encoded = (json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        check(hashlib.sha256(encoded).hexdigest()==job['cel_sha256'],cel)
        entries[cel] = encoded
    results = []
    distributed = set()
    for record in job['output']:
        name = record['file']; names = record['entries']
        check(not distributed.intersection(names),'duplicate output entries')
        distributed.update(names)
        content = package(entries,names)
        check(len(content)==record['size'] and hashlib.sha256(content).hexdigest()==record['sha256'],'Mismatch: '+name)
        oid = hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
        if output:
            target = output/name
            with target.open('xb') as file:
                file.write(content)
        results.append({'path':name,'size':len(content),'sha256':record['sha256'],'git_blob':oid})
    check(distributed==entries.keys(),'Path list changed unexpectedly')
    return results

def build_one(spec,base,output):
    config = recipe(spec)
    entries = {}
    for item in config['input']:
        additions = unpack(checked_file(base,item))
        check(not entries.keys() & additions.keys(),'Duplicate input entries')
        entries.update(additions)
    rows = rebuild(config,entries,output)
    print(f'{spec.name}: verified {len(rows)} PCKs, {sum(r["size"] for r in rows):,} bytes',flush=True)
    return rows

def versions():
    check(Image.__version__=='13.0.0' and features.version('webp')=='1.6.0',
          f'Use Pillow 13.0.0 with libwebp 1.6.0; got {Image.__version__} and {features.version("webp")}')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True,type=Path,help='Checkout of the pinned V115 public media')
    p.add_argument('--output',type=Path,help='New directory for the completed media; omit for verification only')
    p.add_argument('--jobs',type=int,default=3)
    args=p.parse_args()
    versions()
    check(1<=args.jobs<=8,'Expected 1–8 workers')
    specs=[*sorted((ROOT/'layouts').glob('*.json.gz')),ROOT/'ring.json']
    check(len(specs)==25,'24 fighter layout recipes and ring expected')
    expected=json.loads((ROOT/'expected.json').read_bytes())['files']
    if args.output and args.output.exists():raise ValueError('Choose a new output directory')
    parent=args.output.parent if args.output else None
    with tempfile.TemporaryDirectory(dir=parent) as folder:
        directory=Path(folder)/'media' if args.output else None
        if directory:(directory/'map-packs').mkdir(parents=True)
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            jobs=[pool.submit(build_one,s,args.base,directory) for s in specs]
            rows=[row for job in jobs for row in job.result()]
        check(sorted(rows,key=lambda r:r['path'])==expected,'Release manifest mismatch')
        if directory:directory.rename(args.output)
    print(f'Verified every SHA-256 and Git blob ID for {len(rows)} media files ({sum(f["size"] for f in rows):,} bytes).')
if __name__=='__main__':main()
