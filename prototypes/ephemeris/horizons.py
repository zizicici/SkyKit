#!/usr/bin/env python3
"""Optional refresh of independent Horizons fixtures, including DUT1 and ICRF direction."""
import csv
from datetime import datetime, timezone
import json
import urllib.parse
import urllib.request

from prepare import ROOT


def main():
    old = json.loads((ROOT.parents[1] / 'moontakeTests' / 'MoonHorizonsFixtures.json').read_text())
    rows = []
    for sample in old['samples']:
        date = datetime.fromisoformat(sample['utc'].replace('Z', '+00:00'))
        parameters = dict(COMMAND='301', EPHEM_TYPE='OBSERVER', CENTER='coord@399',
                          COORD_TYPE='GEODETIC', SITE_COORD=f"{sample['longitude']},{sample['latitude']},{sample['elevation']/1000}",
                          TLIST=str(date.timestamp()/86400+2440587.5), TLIST_TYPE='JD', TIME_TYPE='UT',
                          QUANTITIES='4,45,49', ANG_FORMAT='DEG', APPARENT='AIRLESS', CSV_FORMAT='YES',
                          EXTRA_PREC='YES', OBJ_DATA='NO')
        query = {'format': 'json', **{key: f"'{value}'" for key, value in parameters.items()}}
        url = 'https://ssd.jpl.nasa.gov/api/horizons.api?' + urllib.parse.urlencode(query)
        with urllib.request.urlopen(url, timeout=45) as response:
            payload = json.load(response)
        text = payload.get('result', '')
        if 'error' in payload or '$$SOE' not in text:
            raise RuntimeError(payload)
        columns = next(csv.reader([text.split('$$SOE')[1].split('$$EOE')[0].strip()]))
        az, alt, ra, dec, dut1 = map(float, columns[3:8])
        eop_lines = [line.strip() for line in text.splitlines() if 'EOP' in line or 'eop.' in line]
        rows.append({**sample, 'azimuth': az, 'altitude': alt, 'rightAscension': ra,
                     'declination': dec, 'dut1': dut1, 'earthOrientationSource': eop_lines})
        print(sample['utc'], sample['latitude'], dut1, flush=True)
    result = dict(source='JPL Horizons Moon 301, quantities 4/45/49, WGS84, airless',
                  documentation='https://ssd.jpl.nasa.gov/horizons/manual.html',
                  generatedAt=datetime.now(timezone.utc).isoformat(), samples=rows)
    (ROOT / 'horizons-fixtures.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
