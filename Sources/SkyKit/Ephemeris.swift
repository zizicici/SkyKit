import CryptoKit
import Foundation
import NativeSky

public enum EphemerisError: Error { case invalidResource, unavailableKernel }

public struct MoonPhase: Sendable {
    public let angle: Double
    public let illumination: Double
    public let leapSecondsAssumed: Bool
    public let historicalTimeEstimated: Bool
}

public struct MoonObservation: Sendable {
    public let eastNorthUp: SIMD3<Double>
    public let azimuth: Double
    public let altitude: Double
    public let time: TimeScales
}

/// One immutable kernel per app process. Concurrent reads are supported by the
/// native core; ARC retains this object through calls and releases it on deinit.
public final class Ephemeris: @unchecked Sendable {
    public static let shared: Ephemeris? = try? Ephemeris()
    private let handle: UnsafeMutableRawPointer
    private let earth: EarthOrientationTable
    private let coverage: Range<Double>

    public convenience init() throws {
        guard let kernel = Bundle.module.url(forResource: KernelResource.name, withExtension: "bin"),
              let eop = Bundle.module.url(forResource: "earth-orientation", withExtension: "json") else {
            throw EphemerisError.invalidResource
        }
        try self.init(kernelURL: kernel, orientationData: Data(contentsOf: eop))
    }

    /// Resource loading is fail-closed. There is no alternate astronomy engine.
    public convenience init(kernelURL: URL, orientationData: Data) throws {
        try self.init(kernelURL: kernelURL, orientationData: orientationData, expectedHash: KernelResource.sha256)
    }

    internal init(kernelURL: URL, orientationData: Data, expectedHash: String) throws {
        let earth = try JSONDecoder().decode(EarthOrientationTable.self, from: orientationData)
        try earth.validate()
        // Verify the coefficient file, not only its header. The temporary Data
        // is released before the native library allocates its permanent copy.
        let bounds: Range<Double> = try {
            let data = try Data(contentsOf: kernelURL)
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            guard data.count >= 28, digest == expectedHash else { throw EphemerisError.invalidResource }
            let start = data.withUnsafeBytes { Double(bitPattern: UInt64(littleEndian: $0.loadUnaligned(fromByteOffset: 12, as: UInt64.self))) }
            let end = data.withUnsafeBytes { Double(bitPattern: UInt64(littleEndian: $0.loadUnaligned(fromByteOffset: 20, as: UInt64.self))) }
            guard start.isFinite, end.isFinite, start >= -54786.5, end <= 237407.5, start < end else { throw EphemerisError.invalidResource }
            return start..<end
        }()
        guard let handle = kernelURL.path.withCString({ lunar_open($0) }) else { throw EphemerisError.unavailableKernel }
        self.earth = earth
        self.handle = handle
        self.coverage = bounds
    }

    deinit { lunar_close(handle) }

    public func timeScales(at date: Date) -> TimeScales? {
        guard let time = earth.times(at: date), coverage.contains(time.tdb) else { return nil }
        return time
    }

    internal func dynamicalTime(at date: Date) -> Double? { earth.times(at: date)?.tdb }

    internal func covers(_ date: Date, through end: Date) -> Bool {
        timeScales(at: date) != nil && timeScales(at: end) != nil
    }

    internal static func orientationData() throws -> Data {
        guard let url = Bundle.module.url(forResource: "earth-orientation", withExtension: "json") else { throw EphemerisError.invalidResource }
        return try Data(contentsOf: url)
    }

    public func phase(at date: Date) -> MoonPhase? {
        guard let time = timeScales(at: date) else { return nil }
        var angle = 0.0, fraction = 0.0
        guard lunar_phase(handle, time.tdb, time.tt, &angle) == 0,
              lunar_illumination(handle, time.tdb, &fraction) == 0 else { return nil }
        return MoonPhase(angle: angle, illumination: fraction, leapSecondsAssumed: time.leapSecondsAssumed, historicalTimeEstimated: time.historicalTimeEstimated)
    }

    public func nextQuarter(_ quarter: Int, onOrAfter date: Date, limitDays: Double = 40) -> Date? {
        guard (0...3).contains(quarter), let time = timeScales(at: date) else { return nil }
        var result = 0.0
        guard lunar_quarter(handle, time.tdb, Int32(quarter), limitDays, &result) == 0 else { return nil }
        return earth.date(fromTDB: result)
    }

    /// WGS84 ellipsoidal height (meters); angles are degrees. Airless Moon center.
    public func observe(at date: Date, latitude: Double, longitude: Double,
                        ellipsoidalHeight: Double = 0) -> MoonObservation? {
        guard let time = timeScales(at: date) else { return nil }
        var output = [Double](repeating: 0, count: 8)
        guard lunar_observe(handle, time.tdb, time.tt, time.ut1, time.xp, time.yp, time.dX, time.dY,
                            latitude, longitude, ellipsoidalHeight, &output) == 0 else { return nil }
        return MoonObservation(eastNorthUp: SIMD3(output[3], output[4], output[5]),
                                azimuth: output[6], altitude: output[7], time: time)
    }
}
