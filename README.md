# SkyKit

Moon phase and topocentric direction from original JPL DE440 coefficients and
ERFA 2.0.1. There is no alternate engine and no approximate fallback: missing or
invalid data returns `nil`, so a caller can show an explicit unavailable state
instead of a fabricated value.

## Requirements

iOS 15+ / macOS 12+, Swift 5.10.

## Installation

```swift
.package(url: "https://github.com/zizicici/SkyKit.git", from: "1.0.0")
```

## Quick start

```swift
import SkyKit

// Geocentric phase for a date.
if let phase = Moon.phase(at: Date()) {
    print(phase.illumination, phase.angle) // 0...1, degrees (0 = new, 180 = full)
}

// Next full Moon.
let full = Moon.nextQuarter(180, onOrAfter: Date())

// Topocentric direction; `elevation` is WGS84 ellipsoidal height in meters.
if let position = Moon.position(at: Date(), latitude: 1.3521, longitude: 103.8198,
                                refraction: true) {
    print(position.azimuth, position.altitude) // degrees, azimuth clockwise from true north
}

// Camera guidance: reference-frame vector into device coordinates.
let guidance = MoonGuidance.calculate(position: position, attitude: attitude)
let marker = MoonProjection.project(guidance, imageRect: imageRect,
                                    horizontalFieldOfView: fov, zoom: zoom,
                                    markerBounds: safeBounds)
```

`MoonGuidance` expects Core Motion's `xTrueNorthZVertical` attitude and assumes a
rear camera looking along device −Z. `MoonProjection` assumes a portrait preview
whose `imageRect` comes from `AVCaptureVideoPreviewLayer`.

## Offline and on-demand coverage

- Bundled: **1850-01-01 ≤ TDB < 2150-01-01**, four necessary Sun/Earth/Moon
  segments from DE440s, with two-day coefficient padding. No refitting,
  quantization or truncation of polynomial terms. Approximately 21 MB of
  coefficients; the old 3.4 MiB figure describes the 2000–2050 prototype only.
- **iOS 15–18:** bundled coverage only, ending in 2149. No remote provider is
  created, and caches from the earlier direct-JPL prototype are never consulted.
- **iOS 26+:** one Apple-hosted, on-demand asset pack covers **2150–2649**.
  It contains the original full-DE440 coefficients, with a 42-day overlap before
  2150 for phase searches. The kernel is 35,073,756 bytes (33.45 MiB); the `.aar`
  is approximately 33 MiB. There are no decade/year downloads. The system retains
  the downloaded pack for subsequent offline use while the app remains installed.
- Callers that need a far-future date await the pack asynchronously via
  `Ephemeris.ensureAvailable(at:)`. Present-day calculations stay offline.
  Concurrent requests share one load. Missing or corrupt resources and network
  errors return no result and never invoke another astronomy engine.
- Dates before 1850 or at/after 2650 are unsupported. DE441 and an extended time
  model would be needed to extend the interval; coefficients are not extrapolated.

`available(at:through:)` checks bundled or already-local managed assets without
starting a download. `ensureAvailable(at:)` prepares the requested date and the
next phase-search window. Each resource is verified against the bundled SHA-256
and size before native loading. A private temporary snapshot avoids a race with
system-managed asset replacement; the native reader owns its coefficients after
initialization. The package contains no JPL URLSession transport. JPL byte ranges are
used only by the developer's asset-building tool. No GPS or photos are uploaded.

See [Apple-hosted asset release instructions](docs/asset-packs.md). Resource
creation and local tests do not publish the pack to App Store Connect.

## Time scales and quality

Foundation Date uses UTC/POSIX labels. Since 1960, TT uses ERFA's immutable UTC
history, including pre-1972 frequency drift, and TDB uses geocentric `eraDtdb`.
Before 1960, Date labels mean UT1 and the historical Espenak–Meeus ΔT estimate
supplies TT; `historicalTimeEstimated` marks this lower time accuracy. Foundation
cannot label 23:59:60; reverse conversion within the skipped second returns nil.
Clock-boundary arithmetic snaps within 10 microseconds, below the phase solver's
0.1 ms tolerance, to prevent floating-point rounding selecting the wrong clock era.

The offline IERS finals2000A snapshot covers 1973-01-02 through 2027-09-25, with
observed UT1/polar motion through 2026-09-17 and predictions thereafter. Interpolate
continuous UT1−TAI on a TAI timeline, including leap boundaries. Celestial pole
offsets are converted from IAU 2000A to IAU 2006. Missing offsets are zero and their
observation status is tracked separately. Outside the snapshot, UT1=UTC (or the
pre-UTC UT1 label), pole offsets are zero, and quality is `approximate`.

IERS Bulletin C 72 confirms the leap table before 2027-07-01. Later UTC dates assume
no additional leap seconds and set `leapSecondsAssumed`; future calendar times and
Earth rotation are provisional, especially centuries ahead. Downloading more
position coefficients does not remove those uncertainties. EOP/leap resources
are updated with package releases, independently of the hosted coefficient pack.

## Models and verification

One immutable native kernel supports concurrent reads. WGS84 ellipsoidal GPS
height is required. Native ENU becomes `(N, -E, U)` for Core Motion. The standalone
standard-refraction helper implements NOAA's published equations; it is an
approximate atmospheric model, not a weather prediction. Moon phases represent
geocentric geometric illumination. Lunar libration and eclipse shadows are not
implemented. Compass and camera calibration still require outdoor device testing.

`provenance.json` pins exported sources and resources; `validation.json` records
expanded coefficient checks against JPLEphem. The 343,120 expanded position/velocity
comparisons have a maximum cross-reader position difference of 1.46 cm. At the
worst sample, 50-digit decimal evaluation attributes this to the reference reader’s
SPK-seconds rounding: native differs by 0.012 mm, reference by 14.59 mm. This is
implementation agreement, not absolute ephemeris accuracy. The core computes the
Chebyshev argument from the local record epoch to avoid loss from subtracting a
distant kernel epoch. Historical 2000–2050 core accuracy
reports remain under `prototypes/ephemeris`; their numerical error claims
must not be presented as absolute accuracy for the entire expanded date range.

```sh
# Prepare the prototype per its README, then export the package's coefficient subset:
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/export-resources.py --finals /path/to/finals2000A.all
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/validate-resources.py
swift test
# Build the single Apple-hosted pack (first run requires --fetch):
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/build-asset-packs.py --fetch
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/validate-asset-packs.py
```

`hosted-provenance.json` records source-range hashes and the generated archive.
`hosted-validation.json` verifies all coefficient bytes and 548,148 position/velocity
samples, including every interval boundary. Same-source JPLEphem comparison differs
by at most 5.31 cm because of time argument rounding; this is a reader regression
check, not an absolute ephemeris accuracy claim. The raw DE440 fixture independently
checks the builder's crop and supplies offline mock-transport tests. Tests cover
old-system policy, the 2149/2150 boundary, one-pack coverage, corruption, retry,
concurrency and reopening locally available data. Apple-hosted delivery itself
requires a published pack or Apple's local managed-asset test server.

## Sources and notices

- [JPL DE440s](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp) and [DE440](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440.bsp): original position coefficients.
- [SPK type 2 format](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/spk.html) and [DAF format](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/daf.html).
- [ERFA 2.0.1](https://github.com/liberfa/erfa/tree/v2.0.1): BSD 3-clause; ship `ERFA-LICENSE.txt` with any app that links this package.
- [IERS finals2000A](https://maia.usno.navy.mil/ser7/finals2000A.all), [format](https://maia.usno.navy.mil/ser7/readme.finals2000A), [Bulletin C 72](https://hpiers.obspm.fr/iers/bul/bulc/bulletinc.72).
- [NASA historical ΔT polynomials](https://eclipse.gsfc.nasa.gov/SEcat5/deltatpoly.html), used only before 1960, without the correction specific to ELP-2000/82.
- [NOAA standard refraction](https://gml.noaa.gov/grad/solcalc/calcdetails.html).
- [ChineseCalendar](https://github.com/ytliu0/ChineseCalendar): phase-method and validation reference; its tables/runtime code are not bundled.
