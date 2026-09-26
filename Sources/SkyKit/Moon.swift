import Foundation

/// Moon phase, topocentric direction and surface geometry from DE440/ERFA orbital
/// vectors. Surface orientation explicitly uses the approximate IAU 2009 model.
/// Missing or unsupported ephemeris data returns nil without an alternate engine.
public enum Moon {
    /// Geocentric phase for `date`, or nil when no kernel covers it.
    public static func phase(at date: Date) -> MoonPhase? {
        Ephemeris.available(at: date)?.phase(at: date)
    }

    /// Lunar atlas geometry, with approximate IAU 2009 surface orientation.
    /// No GPS is required: the default is the view from Earth's center.
    /// Availability and resource preparation follow the same policy as `phase`.
    public static func surface(at date: Date, observer: MoonSurfaceObserver = .geocentric,
                               north: MoonSurfaceNorth = .trueOfDate) -> MoonSurface? {
        Ephemeris.available(at: date)?.surface(at: date, observer: observer, north: north)
    }

    /// First new (0), first-quarter (90), full (180) or last-quarter (270) Moon
    /// on or after `start`. Other angles are rejected.
    public static func nextQuarter(_ angle: Double, onOrAfter start: Date, limitDays: Double = 40) -> Date? {
        guard [0, 90, 180, 270].contains(angle) else { return nil }
        return Ephemeris.available(at: start, through: start.addingTimeInterval(limitDays * 86400))?
            .nextQuarter(Int(angle / 90), onOrAfter: start, limitDays: limitDays)
    }

    /// Topocentric direction to the Moon's center. `elevation` is WGS84
    /// ellipsoidal height in meters, not altitude above mean sea level.
    public static func position(at date: Date, latitude: Double, longitude: Double,
                                elevation: Double = 0, refraction: Bool = false) -> MoonPosition? {
        guard let result = Ephemeris.available(at: date)?.observe(at: date, latitude: latitude,
            longitude: longitude, ellipsoidalHeight: elevation) else { return nil }
        let adjustment = refraction ? AtmosphericRefraction.correction(at: result.altitude) : 0
        var position = MoonPosition(azimuth: result.azimuth, altitude: result.altitude + adjustment,
            orientationQuality: result.time.orientationQuality,
            leapSecondsAssumed: result.time.leapSecondsAssumed)
        if adjustment == 0 {
            let enu = result.eastNorthUp
            position.nativeNorthWestUp = SIMD3(enu.y, -enu.x, enu.z)
        }
        return position
    }

    /// Wraps any angle into 0..<360 degrees.
    public static func normalizedDegrees(_ degrees: Double) -> Double {
        let value = degrees.truncatingRemainder(dividingBy: 360)
        return value < 0 ? value + 360 : value
    }
}
