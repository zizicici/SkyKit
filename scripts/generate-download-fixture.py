#!/usr/bin/env python3
"""Pin real DE440 HTTP ranges; use JPLEphem on a sparse file as an independent reader."""
import base64, hashlib, json, math, struct, tempfile, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from jplephem.spk import SPK

URL='https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440.bsp'
SIZE=119799808
ROOT=Path(__file__).resolve().parents[1]
YEAR=2200
start=datetime(YEAR,1,1,tzinfo=timezone.utc).timestamp()/86400-10957.5-42
end=datetime(YEAR+1,1,1,tzinfo=timezone.utc).timestamp()/86400-10957.5+42
ranges={}
with tempfile.TemporaryFile() as file:
    file.truncate(SIZE)
    def read(a,b):
        request=urllib.request.Request(URL,headers={'Range':f'bytes={a}-{b-1}','Accept-Encoding':'identity'})
        with urllib.request.urlopen(request,timeout=45) as response:
            assert response.status==206
            assert response.headers['Content-Range']==f'bytes {a}-{b-1}/{SIZE}'
            data=response.read(b-a+1)
        assert len(data)==b-a
        file.seek(a);file.write(data);file.flush()
        ranges[f'{a}:{b}']=base64.b64encode(data).decode()
        print(a,b,flush=True)
        return data
    header=read(0,1024)
    record=struct.unpack_from('<I',header,76)[0]
    while record:
        data=read((record-1)*1024,record*1024)
        record=int(struct.unpack_from('<d',data)[0])
    from jplephem.daf import DAF
    kernel=SPK(DAF(file))
    expected=bytearray(struct.pack('<8sIdd',b'LUNAR001',4,start,end))
    for center,body in [(0,3),(0,10),(3,399),(3,301)]:
        segment=kernel[center,body]
        directory=read((segment.end_i-4)*8,segment.end_i*8)
        epoch,interval,size,count=struct.unpack('<4d',directory)
        first=math.floor((start*86400-2*86400-epoch)/interval)
        last=math.ceil((end*86400+2*86400-epoch)/interval)
        read((segment.start_i-1+first*int(size))*8,(segment.start_i-1+last*int(size))*8)
        # JPLEphem independently interprets SPK records and polynomial order.
        init,step,coeff=segment.load_array()
        data=coeff[:,first:last,:].transpose(1,0,2).astype('<f8')
        expected.extend(struct.pack('<IIddII',center,body,init-2451545+first*step,step,last-first,data.shape[2]))
        expected.extend(data.tobytes())
    result=dict(source=URL,sourceBytes=SIZE,year=YEAR,start=start,end=end,ranges=ranges,
        expectedSHA256=hashlib.sha256(expected).hexdigest(),
        expectedKernel=base64.b64encode(expected).decode(),
        lunarPositionAtTDB73049=kernel[3,301].compute(2451545.,73049.).tolist())
    out=ROOT/'Tests/SkyKitTests/Fixtures/de440-ranges.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,separators=(',',':'))+'\n')
    print('HTTP bytes',sum(len(base64.b64decode(x)) for x in ranges.values()),'kernel bytes',len(expected))
