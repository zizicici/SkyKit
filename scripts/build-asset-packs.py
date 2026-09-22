#!/usr/bin/env python3
"""Build one Apple-hosted pack for all distant dates. Network access exists only in this build tool.

Use the prototype Python environment. --fetch permits downloading missing pinned
DE440 source ranges; subsequent invocations are offline. Nothing is uploaded.
"""
import argparse, concurrent.futures, hashlib, json, math, os, struct, subprocess, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from jplephem.spk import SPK
from jplephem.daf import DAF

ROOT=Path(__file__).resolve().parents[1]
URL='https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440.bsp'
SIZE=119799808
MODIFIED='Mon, 21 Dec 2020 19:39:26 GMT'

def digest(data):return hashlib.sha256(data).hexdigest()
def day(year):return datetime(year,1,1,tzinfo=timezone.utc).timestamp()/86400-10957.5

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'build/asset-packs')
    parser.add_argument('--cache',type=Path,default=Path(os.environ.get('SKYKIT_CACHE','/tmp/skykit-jpl-cache'))/'hosted-de440')
    args=parser.parse_args();args.cache.mkdir(parents=True,exist_ok=True);args.output.mkdir(parents=True,exist_ok=True)
    source_hashes={}
    def read(a,b):
        path=args.cache/f'{a}-{b}.bin';hashfile=path.with_suffix('.sha256')
        if path.exists() and hashfile.exists():
            data=path.read_bytes()
            if len(data)!=b-a or digest(data)!=hashfile.read_text():raise RuntimeError(f'Corrupt range {a}:{b}')
            return a,b,data
        if not args.fetch:raise RuntimeError(f'Missing range {a}:{b}; rerun with --fetch')
        request=urllib.request.Request(URL,headers={'Range':f'bytes={a}-{b-1}','Accept-Encoding':'identity'})
        with urllib.request.urlopen(request,timeout=90) as response:
            if response.status!=206 or response.headers['Content-Range']!=f'bytes {a}-{b-1}/{SIZE}' or response.headers['Last-Modified']!=MODIFIED:
                raise RuntimeError('Unexpected DE440 source revision or partial response')
            data=response.read(b-a+1)
        if len(data)!=b-a:raise RuntimeError('Incomplete source range')
        path.write_bytes(data);hashfile.write_text(digest(data))
        return a,b,data
    sparse=args.cache/'de440-selected.bsp'
    with sparse.open('w+b') as file:
        file.truncate(SIZE)
        def store(result):
            a,b,data=result;file.seek(a);file.write(data);file.flush()
            source_hashes[f'{a}:{b}']=digest(data)
            return data
        header=store(read(0,1024));record=struct.unpack_from('<I',header,76)[0]
        while record:
            data=store(read((record-1)*1024,record*1024));record=int(struct.unpack_from('<d',data)[0])
        spk=SPK(DAF(file))
        spans=[]
        for pair in [(0,3),(0,10),(3,399),(3,301)]:
            seg=spk[pair]
            directory=store(read((seg.end_i-4)*8,seg.end_i*8))
            epoch,interval,size,count=struct.unpack('<4d',directory)
            first=math.floor(((day(2150)-44)*86400-epoch)/interval)
            last=math.ceil(((day(2650)+2)*86400-epoch)/interval)
            assert 0<=first<last<=count
            a=(seg.start_i-1+first*int(size))*8;b=(seg.start_i-1+last*int(size))*8
            spans.extend((x,min(x+262144,b)) for x in range(a,b,262144))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(read,a,b) for a,b in spans]
            for i,job in enumerate(concurrent.futures.as_completed(jobs),1):
                store(job.result())
                if i%10==0 or i==len(jobs):print(f'Source ranges {i}/{len(jobs)}',flush=True)
        arrays={pair:spk[pair].load_array() for pair in [(0,3),(0,10),(3,399),(3,301)]}
        catalog=[]
        for year in [2150]:
            start,end=day(year)-42,min(day(2650),day(2650))
            name='2150-2649';identifier=f'skykit-de440-{name}-v1'
            relative=f'SkyKit/de440/{name}.bin'
            payload=bytearray(struct.pack('<8sIdd',b'LUNAR001',4,start,end))
            for (center,body),(epoch,step,coeff) in arrays.items():
                epoch-=2451545.;first=math.floor((start-2-epoch)/step);last=math.ceil((end+2-epoch)/step)
                data=np.asarray(coeff[:,first:last,:].transpose(1,0,2),dtype='<f8')
                payload.extend(struct.pack('<IIddII',center,body,epoch+first*step,step,last-first,data.shape[2]))
                payload.extend(data.tobytes())
            source=args.output/'contents'/relative;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(payload)
            manifest=dict(assetPackID=identifier,downloadPolicy={'onDemand':{}},platforms=['iOS'],
                fileSelectors=[dict(file=relative)],sourceRoot='contents')
            manifest_file=args.output/f'{identifier}.json';manifest_file.write_text(json.dumps(manifest,indent=2)+'\n')
            archive=args.output/f'{identifier}.aar'
            # ba-package refuses to write over an existing archive, so a rerun
            # would fail. The archive is not byte-reproducible (it carries build
            # metadata); the coefficient digest recorded below is the stable one.
            archive.unlink(missing_ok=True)
            subprocess.run(['xcrun','ba-package','package',str(manifest_file),'-o',str(archive),'--quiet'],check=True)
            catalog.append(dict(id=identifier,path=relative,startTDB=start,endTDB=end,sha256=digest(payload),bytes=len(payload)))
        # Fixture parser must agree byte-for-byte with the previously validated year crop.
        fixture=json.loads((ROOT/'Tests/SkyKitTests/Fixtures/de440-ranges.json').read_text())
        expected=bytearray(struct.pack('<8sIdd',b'LUNAR001',4,fixture['start'],fixture['end']))
        for (center,body),(epoch,step,coeff) in arrays.items():
            epoch-=2451545.;first=math.floor((fixture['start']-2-epoch)/step);last=math.ceil((fixture['end']+2-epoch)/step)
            data=np.asarray(coeff[:,first:last,:].transpose(1,0,2),dtype='<f8')
            expected.extend(struct.pack('<IIddII',center,body,epoch+first*step,step,last-first,data.shape[2]));expected.extend(data.tobytes())
        assert digest(expected)==fixture['expectedSHA256']
    catalog_data=json.dumps(dict(schema=1,packs=catalog),indent=2)+'\n'
    (ROOT/'Sources/SkyKit/Resources/hosted-packs.json').write_text(catalog_data)
    (args.output/'catalog.json').write_text(catalog_data)
    provenance=dict(source=URL,sourceBytes=SIZE,lastModified=MODIFIED,rangeSHA256=source_hashes,
        catalogSHA256=digest(catalog_data.encode()),packCount=len(catalog),
        archiveSHA256={p.name:digest(p.read_bytes()) for p in sorted(args.output.glob('*.aar'))})
    provenance_data=json.dumps(provenance,indent=2)+'\n'
    (args.output/'provenance.json').write_text(provenance_data)
    (ROOT/'hosted-provenance.json').write_text(provenance_data)
    print(f'Built {len(catalog)} packs at {args.output}; no upload performed.',flush=True)

if __name__=='__main__':main()
