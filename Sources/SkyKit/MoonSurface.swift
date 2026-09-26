import CoreGraphics
import Foundation
import simd

public enum MoonSurfaceObserver: Equatable, Sendable {
    case geocentric
    /// WGS84 geodetic degrees, east-positive longitude in -180...180, ellipsoidal
    /// height in meters (-1000...100000). Invalid inputs make `Moon.surface` nil.
    case earth(latitude: Double, longitude: Double, elevation: Double = 0)
}

/// Defines image up before applying the fitted photo rotation.
public enum MoonSurfaceNorth: String, Sendable {
    /// True equatorial north of the observation date (JPL Horizons convention).
    case trueOfDate
    /// Fixed ICRF/J2000 north, useful for NASA SVS lunar atlas renders.
    case icrf
}

/// Planetocentric coordinates in the approximate lunar mean Earth/polar axis frame.
public struct MoonSurfaceCoordinate: Equatable, Sendable {
    public let latitude: Double
    /// East-positive degrees, normalized to -180..<180 (accepts 0...360 catalogs).
    public let longitude: Double

    public init?(latitude: Double, longitude: Double) {
        guard latitude.isFinite, abs(latitude) <= 90, longitude.isFinite else { return nil }
        self.latitude = latitude
        // Reduce first to avoid overflow when normalizing a very large longitude.
        let reduced = longitude.truncatingRemainder(dividingBy: 360)
        self.longitude = reduced < -180 ? reduced + 360 : (reduced >= 180 ? reduced - 360 : reduced)
    }

    /// Body-fixed axes: +X = 0°N/0°E, +Y = 0°N/90°E, +Z = lunar north.
    public var unitVector: SIMD3<Double> {
        let lat = latitude * .pi / 180, lon = longitude * .pi / 180
        return SIMD3(cos(lat) * cos(lon), cos(lat) * sin(lon), sin(lat))
    }

    internal init(direction: SIMD3<Double>) {
        latitude = atan2(direction.z, hypot(direction.x, direction.y)) * 180 / .pi
        let lon = atan2(direction.y, direction.x) * 180 / .pi
        longitude = lon >= 180 ? lon - 360 : lon
    }
}

/// A projected feature, in a unit-radius disc centered at (0, 0), image Y down.
/// Back-side points also have a projected position: check `isVisible` before labeling.
public struct MoonSurfacePoint: Sendable {
    public let point: CGPoint
    /// Cosine relative to the observer; <= 0 is on/beyond the orthographic limb.
    public let emissionCosine: Double
    /// Cosine relative to the Sun; <= 0 is on the terminator or the night side.
    /// This is lighting geometry, not an estimate of photographic brightness.
    public let incidenceCosine: Double
    public var isVisible: Bool { emissionCosine > 1e-12 }
    public var isIlluminated: Bool { incidenceCosine > 1e-12 }
}

/// Immutable, approximate geometry for labeling large lunar features in photographs.
/// Orbital vectors use DE440; lunar rotation uses the small IAU 2009 analytical
/// model. A spherical Moon and orthographic projection are intentional approximations.
/// This does not recognize images, model relief/eclipses, or determine camera roll.
public struct MoonSurface: Sendable {
    public let date: Date
    public let observer: MoonSurfaceObserver
    public let northReference: MoonSurfaceNorth
    public let time: TimeScales
    public var orientationModel: MoonSurfaceOrientationModel { .iau2009 }
    /// Libration longitude/latitude of the disc center; topocentric when requested.
    public let subObserver: MoonSurfaceCoordinate
    public let subSolar: MoonSurfaceCoordinate
    /// Degrees east of `northReference`, normalized to 0..<360.
    /// This is not rotation relative to the phone or the observer's local vertical.
    public let northPolePositionAngle: Double
    /// Observer-to-Moon-center distance in km, including down-leg light time.
    public let distance: Double
    /// Degrees, based on the reference sphere radius of 1737.4 km.
    public var angularDiameter: Double { 2 * asin(1737.4 / distance) * 180 / .pi }
    /// Geometric fraction for this observer; no opposition effect or eclipse shadow.
    public var illumination: Double {
        max(0, min(1, (1 + simd_dot(observerDirection, sunDirection)) / 2))
    }
    /// Unit directions in the lunar body-fixed frame; useful for 3D lighting.
    public let observerDirection: SIMD3<Double>
    public let sunDirection: SIMD3<Double>
    /// Lunar body -> view. View +X is celestial west (image right), +Y is celestial
    /// north (image up), +Z points toward the observer. Use a distant/orthographic
    /// camera looking down -Z, then apply the photo's fitted rotation separately.
    public let bodyToView: simd_double3x3
    public var sunDirectionInView: SIMD3<Double> { bodyToView * sunDirection }

    internal init(date: Date, observer: MoonSurfaceObserver, north: MoonSurfaceNorth,
                  time: TimeScales, vectors: [Double]) {
        self.date = date
        self.observer = observer
        self.northReference = north
        self.time = time
        let rotation = MoonSurfaceOrientation.rotation(at: vectors[0])
        let toObserver = SIMD3(vectors[1], vectors[2], vectors[3])
        let toSun = SIMD3(vectors[4], vectors[5], vectors[6])
        let referenceNorth = north == .icrf ? SIMD3(0, 0, 1) : SIMD3(vectors[7], vectors[8], vectors[9])
        distance = simd_length(toObserver)
        let direction = simd_normalize(toObserver)
        observerDirection = rotation * direction
        sunDirection = rotation * simd_normalize(toSun)
        subObserver = MoonSurfaceCoordinate(direction: observerDirection)
        subSolar = MoonSurfaceCoordinate(direction: sunDirection)
        // The Moon stays far from the celestial poles for terrestrial observers.
        let right = simd_normalize(simd_cross(referenceNorth, direction))
        let up = simd_cross(direction, right)
        bodyToView = simd_double3x3(rows: [rotation * right, rotation * up, observerDirection])
        let poleInView = bodyToView * SIMD3(0, 0, 1)
        northPolePositionAngle = Moon.normalizedDegrees(atan2(-poleInView.x, poleInView.y) * 180 / .pi)
    }

    /// Project a catalog coordinate. Rotation is clockwise in the displayed image,
    /// in degrees from the default celestial-north-up view. Apply any mirror to the
    /// image/point separately. For pixel placement: center + radius * result.point.
    public func project(_ coordinate: MoonSurfaceCoordinate, rotation: Double = 0) -> MoonSurfacePoint? {
        guard rotation.isFinite else { return nil }
        let vector = coordinate.unitVector
        let view = bodyToView * vector
        let angle = rotation.truncatingRemainder(dividingBy: 360) * .pi / 180
        let x = view.x * cos(angle) + view.y * sin(angle)
        let y = view.x * sin(angle) - view.y * cos(angle)
        return MoonSurfacePoint(point: CGPoint(x: x, y: y),
                                emissionCosine: view.z,
                                incidenceCosine: simd_dot(vector, sunDirection))
    }

    /// Inverse of the same orthographic projection, for picking a surface coordinate.
    /// Input is centered, unit-radius, image-Y-down; nil outside the lunar disc.
    /// The result is a coordinate, not the identity/boundary of a named feature.
    public func coordinate(at point: CGPoint, rotation: Double = 0) -> MoonSurfaceCoordinate? {
        let x = Double(point.x), y = Double(point.y)
        guard x.isFinite, y.isFinite, rotation.isFinite else { return nil }
        let radiusSquared = x * x + y * y
        guard radiusSquared <= 1 else { return nil }
        let angle = rotation.truncatingRemainder(dividingBy: 360) * .pi / 180
        let view = SIMD3(x * cos(angle) + y * sin(angle),
                         x * sin(angle) - y * cos(angle), sqrt(max(0, 1 - radiusSquared)))
        return MoonSurfaceCoordinate(direction: bodyToView.transpose * view)
    }
}
