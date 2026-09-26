# Lunar surface geometry for photographs

`Moon.surface(at:observer:north:)` supplies the orientation and lighting needed to label
large lunar features in a photograph or render an approximate globe. It returns an
immutable `MoonSurface`, without loading textures, requesting GPS, or analyzing
the photo. `Ephemeris.surface(at:observer:north:)` also works with an explicitly loaded
kernel. Synchronous availability and asynchronous `Ephemeris.ensureAvailable(at:)`
follow the existing resource policy; unavailable dates produce `nil`.

## Model and accuracy

The existing DE440/ERFA orbit, time-scale and observer calculations are retained.
The surface model uses the IAU 2009 trigonometric rotation approximation published
in NAIF's `pck00011.tpc`. The public `orientationModel` is always `.iau2009`; it is
an explicit choice for this API, never a fallback for a missing DE440 resource.
It adds no runtime resources or dependencies. It is not the high-precision lunar
principal-axis rotation encoded in a binary PCK.

Both the observer-to-Moon and Sun-to-Moon light-time legs are evaluated from
DE440. The lunar rotation is evaluated at the Moon's emission time. Optional
WGS84 coordinates account for topocentric libration. Position angle uses the
IAU 2006/2000A true equatorial pole at reception by default. `north: .icrf` uses
fixed ICRF/J2000 north instead. The result records this choice as `northReference`.
The remaining surface geometry is geometric: no aberration, gravitational
deflection, atmospheric refraction, terrain, finite solar disc, or eclipse shadow.
These approximations do not change `Moon.position`'s apparent-direction model.

Offline regression fixtures include 24 NASA SVS geocentric samples through 2026
(DE421 reference) and 16 JPL Horizons samples spanning 1850–2149, including seven
Earth-surface observers at both poles, the date line and 4200 m elevation.
NASA position angles use ICRF north; Horizons uses true-of-date north. The tests
select the matching reference rather than treating the precession offset as error.
Tests require the sub-observer/sub-solar coordinates and pole position angle to
agree within 0.1°. Independent spherical-trigonometry projections of four catalog
landmarks must agree within 0.004 lunar radii. These are acceptance thresholds on
the sampled dates, not an accuracy guarantee across the whole ephemeris range.
On the checked-in 40-sample fixture, the maximum angle difference is 0.007555°
and the maximum projected landmark difference is 0.00011271 lunar radii (about
0.023 pixels for a 400-pixel-diameter disc). This measures agreement with the
reference's spherical projection, not alignment accuracy on real photos.
Future UTC/Earth-orientation uncertainty is exposed in `surface.time`; coverage
alone does not guarantee an equally accurate surface orientation centuries away.

Projection is **orthographic onto a sphere** of radius 1737.4 km. Finite viewing
distance contributes up to roughly 0.005 lunar radii of projection error for an
Earth observer, most relevant near the limb; terrain adds another source of
error. A tolerance of 0.004 radii means 0.8 pixels on a 400-pixel-diameter lunar
disc, before those approximation errors or errors in fitting the actual photo.
This is intended for major maria, not precision crater mapping or occultations.
Blur, overexposure, low resolution and incorrect photo alignment may dominate.

## Coordinate and rotation conventions

- Surface latitude is planetocentric, north positive, −90...90 degrees.
- Longitude is east positive. The failable coordinate initializer accepts any
  finite longitude, including catalog values in 0...360, and wraps to −180..<180.
- Body axes are +X at 0°N/0°E, +Y at 0°N/90°E, +Z at the lunar north pole.
  They approximate the lunar mean Earth/polar axis frame. Do not interpret them
  as DE440 principal axes, or use Earth map projections for lunar coordinates.
- `bodyToView` maps these axes to +X image right/celestial west, +Y image
  up/celestial north, +Z toward the observer. It is a proper rotation, not a
  reflection. `sunDirectionInView` is suitable for positioning a distant light;
  a renderer's light-ray direction may need its negative.
- `northPolePositionAngle` increases eastward from the selected celestial north. Positive
  values put the lunar north pole toward image **left**, for a north-up image.
- `project` returns centered unit-radius coordinates with **Y down**. Its optional
  `rotation` is in degrees, clockwise on the displayed image, applied after the
  default celestial-north-up projection. It is independent of position angle.
- Photo EXIF orientation must be normalized before fitting. If an imported photo
  is mirrored, undo that reflection before matching and picking.

`subObserver` projects to the disc center. It includes optical libration, the
chosen approximate physical rotation and, when requested, topocentric parallax.
`subSolar` is the point directly facing the Sun. `surface.illumination` uses the
selected observer; it can differ slightly from the geocentric `Moon.phase` result.

## App integration

1. Use the capture instant in UTC, not the date the image was imported or saved.
2. Obtain `Moon.surface`; `.geocentric` needs no saved GPS. Use `.earth` only if
   capture coordinates are available and appropriate for the user's preference.
3. Fit the lunar **disc** center, radius and rotation to the original image.
   A crescent's lit-area centroid is not its disc center. Device attitude is only
   an initial estimate of photo orientation. Provide manual alignment when needed.
4. Project catalog coordinates; hide points where `isVisible == false`, and
   normally hide unlit features. A feature's center being dark does not mean its
   entire extended region is dark. The incidence cosine is not a visibility
   guarantee or a brightness model.
5. Place labels using `pixel = center + radius * projected.point`. Save the fit
   parameters if annotations should reopen in the same place.
6. Convert a tap into centered unit-radius image coordinates and call
   `coordinate(at:rotation:)`. It returns nil outside the disc. Feature names and
   containing-region queries require a separate catalog/polygon layer; nearest
   center points do not establish a mare's boundary.

Near the limb, the inverse projection magnifies pixel errors. Avoid promising
precise picking there. `project` includes back-side results with `isVisible=false`
so callers can distinguish occlusion from invalid input; it does not clamp them
onto the visible hemisphere. `coordinate` always selects the front hemisphere.

## Reproducible reference data

```sh
python3 scripts/generate-surface-fixtures.py  # Network used only to refresh fixtures
swift test --filter MoonSurfaceTests          # Offline
```

The fixture records source URLs and the SHA-256 of NASA's downloaded annual table.
Horizons quantities 13, 14, 15 and 17 supply angular diameter, sub-observer point,
sub-solar point and north-pole position angle. Expected projections are derived
from these reference angles, independently of SkyKit's body-to-view matrices.

Sources:

- [NAIF pck00011](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc), lunar `BODY301_*` and Earth-Moon `BODY3_NUT_PREC_ANGLES` coefficients.
- [NAIF lunar reference frames](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_de440_250416.tf), including distinctions between analytical IAU_MOON, ME and PA frames.
- [NASA SVS 2026](https://svs.gsfc.nasa.gov/5587/), credit NASA Scientific Visualization Studio.
- [JPL Horizons observer quantities](https://ssd.jpl.nasa.gov/horizons/manual.html).
