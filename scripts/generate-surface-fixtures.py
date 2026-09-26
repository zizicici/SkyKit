#!/usr/bin/env python3
"""Refresh lunar surface references; the Swift tests themselves run offline.

NASA SVS supplies independent DE421 geocentric samples. JPL Horizons supplies
high-precision lunar orientation with both geocentric and topocentric observers.
No samples are calculated using SkyKit or its IAU 2009 rotation approximation.
"""
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

NASA_URL = 'https://svs.gsfc.nasa.gov/vis/a000000/a005500/a005587/mooninfo_2026.json'
HORIZONS_URL = 'https://ssd.jpl.nasa.gov/api/horizons.api'
SAMPLES = [
    ('1850-01-02T00:00:00Z', None),
    ('1900-06-15T12:00:00Z', None),
    ('1950-12-31T00:00:00Z', None),
    ('2000-01-01T12:00:00Z', None),
    ('2024-04-08T18:00:00Z', None),
    ('2026-09-22T12:00:00Z', None),
    ('2050-06-15T12:00:00Z', None),
    ('2100-01-01T00:00:00Z', None),
    ('2149-12-30T00:00:00Z', None),
    ('2026-09-22T12:00:00Z', (1.3521, 103.8198, 0)),
    ('2026-03-20T10:00:00Z', (-33.8688, 151.2093, 0)),
    ('2026-01-03T20:00:00Z', (51.5074, -0.1278, 0)),
    ('2027-01-01T00:00:00Z', (-16.5, -68.15, 4200)),
    ('2020-02-29T23:59:59Z', (90, 0, 0)),
    ('2030-06-15T06:30:00Z', (-90, 0, 0)),
    ('2026-04-15T18:00:00Z', (0, 179.9, 0)),
]


def download(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def horizons(sample):
    timestamp, observer = sample
    jd = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp() / 86400 + 2440587.5
    parameters = dict(COMMAND='301', EPHEM_TYPE='OBSERVER', CENTER='500@399',
                      TLIST=str(jd), TLIST_TYPE='JD', TIME_TYPE='UT',
                      QUANTITIES='13,14,15,17', APPARENT='AIRLESS',
                      CSV_FORMAT='YES', EXTRA_PREC='YES', OBJ_DATA='NO')
    location = None
    if observer:
        latitude, longitude, elevation = observer
        location = dict(latitude=latitude, longitude=longitude, elevation=elevation)
        parameters.update(CENTER='coord@399', COORD_TYPE='GEODETIC',
                          SITE_COORD=f'{longitude},{latitude},{elevation / 1000}')
    query = {'format': 'json', **{key: f"'{value}'" for key, value in parameters.items()}}
    payload = json.loads(download(HORIZONS_URL + '?' + urllib.parse.urlencode(query)))
    result = payload.get('result', '')
    if 'error' in payload or '$$SOE' not in result:
        raise RuntimeError(payload)
    columns = next(csv.reader([result.split('$$SOE')[1].split('$$EOE')[0].strip()]))
    diameter, lon, lat, solar_lon, solar_lat, angle = map(float, columns[3:9])
    return dict(utc=timestamp, observer=location, diameterArcseconds=diameter,
                subObserver=[lon, lat], subSolar=[solar_lon, solar_lat],
                northPolePositionAngle=angle)


def main():
    nasa_bytes = download(NASA_URL)
    nasa_rows = json.loads(nasa_bytes)
    nasa = []
    for row in nasa_rows:
        instant = dt.datetime.strptime(row['time'], '%d %b %Y %H:%M UT').replace(tzinfo=dt.timezone.utc)
        if instant.day not in (1, 15) or instant.hour != 12:
            continue
        nasa.append(dict(utc=instant.strftime('%Y-%m-%dT%H:%M:%SZ'), observer=None,
                         diameterArcseconds=row['diameter'],
                         subObserver=[row['subearth']['lon'], row['subearth']['lat']],
                         subSolar=[row['subsolar']['lon'], row['subsolar']['lat']],
                         northPolePositionAngle=row['posangle']))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jpl = list(pool.map(horizons, SAMPLES))
    fixture = dict(
        schema=1,
        generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(),
        sources=dict(nasa=NASA_URL, nasaSHA256=hashlib.sha256(nasa_bytes).hexdigest(),
                     nasaNorthReference='ICRF/J2000', horizonsNorthReference='true equator of date',
                     horizons=HORIZONS_URL, quantities='13,14,15,17',
                     documentation='https://ssd.jpl.nasa.gov/horizons/manual.html'),
        angularToleranceDegrees=0.1,
        nasa=nasa, horizons=jpl)
    output = Path(__file__).resolve().parents[1] / 'Tests/SkyKitTests/Fixtures/MoonSurfaceFixtures.json'
    output.write_text(json.dumps(fixture, indent=2) + '\n')
    print(f'Wrote {len(nasa)} NASA and {len(jpl)} Horizons samples to {output}')


if __name__ == '__main__':
    main()
