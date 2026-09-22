#!/usr/bin/env python3
"""Refresh independent JPL Horizons regression fixtures (network required only here)."""
import csv
import datetime as dt
import json
from pathlib import Path
import urllib.parse
import urllib.request

# Public, fixed test locations. Height is meters above the reference ellipsoid.
SAMPLES = [
    ('2026-09-22T12:00:00Z', 1.3521, 103.8198, 0),
    ('2026-09-22T00:00:00Z', 1.3521, 103.8198, 0),
    ('2026-01-03T20:00:00Z', 51.5074, -0.1278, 0),
    ('2026-03-20T10:00:00Z', -33.8688, 151.2093, 0),
    ('2026-06-01T03:00:00Z', 40.7128, -74.006, 0),
    ('2026-12-21T00:00:00Z', 80, 20, 0),
    ('2026-04-15T18:00:00Z', 0, -179.9, 0),
    ('2026-04-15T18:00:00Z', 0, 179.9, 0),
    ('2027-01-01T00:00:00Z', -16.5, -68.15, 4200),
    ('2027-01-01T00:00:00Z', -16.5, -68.15, 0),
    ('2020-02-29T23:59:59Z', 89, 0, 0),
    ('2020-02-29T23:59:59Z', 90, 0, 0),
    ('2024-04-08T18:00:00Z', 25.3, -104.1, 0),
    ('2030-06-15T06:30:00Z', -89, 0, 0),
    ('2030-06-15T06:30:00Z', -90, 0, 0),
    ('2035-12-31T23:59:59Z', 35.6762, 139.6503, 40),
]
rows = []
for timestamp, latitude, longitude, elevation in SAMPLES:
    jd = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp() / 86400 + 2440587.5
    parameters = dict(COMMAND='301', EPHEM_TYPE='OBSERVER', CENTER='coord@399',
                      COORD_TYPE='GEODETIC', SITE_COORD=f'{longitude},{latitude},{elevation / 1000}',
                      TLIST=str(jd), TLIST_TYPE='JD', TIME_TYPE='UT', QUANTITIES='4',
                      APPARENT='AIRLESS', CSV_FORMAT='YES', EXTRA_PREC='YES', OBJ_DATA='NO')
    query = {'format': 'json', **{k: f"'{v}'" for k, v in parameters.items()}}
    url = 'https://ssd.jpl.nasa.gov/api/horizons.api?' + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    result = payload['result']
    if 'error' in payload or '$$SOE' not in result:
        raise RuntimeError(payload)
    line = result.split('$$SOE')[1].split('$$EOE')[0].strip()
    columns = next(csv.reader([line]))
    azimuth, altitude = map(float, columns[3:5])
    rows.append(dict(utc=timestamp, latitude=latitude, longitude=longitude,
                     elevation=elevation, azimuth=azimuth, altitude=altitude))
    print(timestamp, latitude, longitude, elevation, azimuth, altitude, flush=True)

fixture = dict(source='NASA/JPL Horizons, Moon (301), observer coordinates on Earth, airless',
               documentation='https://ssd-api.jpl.nasa.gov/doc/horizons.html',
               generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(),
               angularToleranceDegrees=1/60, samples=rows)
output = Path(__file__).resolve().parents[1] / 'Tests' / 'SkyKitTests' / 'Fixtures' / 'MoonHorizonsFixtures.json'
output.write_text(json.dumps(fixture, indent=2) + '\n')
