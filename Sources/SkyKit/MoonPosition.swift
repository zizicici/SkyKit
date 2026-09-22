import Foundation

/// Topocentric position of the Moon’s center. Azimuth is clockwise from true north.
/// Altitude can include standard atmospheric refraction; obstructions are not modeled.
public struct MoonPosition {
    public let azimuth: Double
    public let altitude: Double
    public var orientationQuality: EarthOrientationQuality
    public var leapSecondsAssumed: Bool
    /// Native ENU is converted to NWU once; retain the vector at zenith/poles.
    public var nativeNorthWestUp: SIMD3<Double>?

    public init(azimuth: Double, altitude: Double,
                orientationQuality: EarthOrientationQuality = .approximate,
                leapSecondsAssumed: Bool = false,
                nativeNorthWestUp: SIMD3<Double>? = nil) {
        self.azimuth = azimuth
        self.altitude = altitude
        self.orientationQuality = orientationQuality
        self.leapSecondsAssumed = leapSecondsAssumed
        self.nativeNorthWestUp = nativeNorthWestUp
    }

    /// North / west / up, matching Core Motion's xTrueNorthZVertical frame.
    public var northWestUp: SIMD3<Double> {
        if let nativeNorthWestUp { return nativeNorthWestUp }
        let az = azimuth * .pi / 180
        let alt = altitude * .pi / 180
        return SIMD3(cos(alt) * cos(az), -cos(alt) * sin(az), sin(alt))
    }
}
