import Foundation
import NativeSky

public enum EarthOrientationQuality: String, Sendable {
    case observed, predicted, approximate
}

public struct TimeScales: Sendable {
    public let tdb: Double
    public let tt: Double
    public let ut1: Double
    public let xp: Double
    public let yp: Double
    public let dX: Double
    public let dY: Double
    public let orientationQuality: EarthOrientationQuality
    public let hasObservedPoleOffsets: Bool
    public let leapSecondsAssumed: Bool
    public let historicalTimeEstimated: Bool
}

struct EarthOrientationTable: Decodable {
    let schema: Int
    let rows: [[Double]]
    let leaps: [[Double]]
    let leapSecondsConfirmedBefore: Double

    func validate() throws {
        guard schema == 2, rows.count > 1, !leaps.isEmpty,
              leapSecondsConfirmedBefore.isFinite else { throw EphemerisError.invalidResource }
        for (index, row) in rows.enumerated() {
            guard row.count == 8, row.allSatisfy(\.isFinite),
                  abs(row[1]) <= 0.001, abs(row[2]) <= 0.001,
                  abs(row[3]) < 864, abs(row[4]) <= 1e-4, abs(row[5]) <= 1e-4,
                  [0, 1].contains(row[6]), [0, 1, 2].contains(row[7]),
                  index == 0 || row[0] - rows[index - 1][0] == 1 else { throw EphemerisError.invalidResource }
        }
        for (index, row) in leaps.enumerated() {
            guard row.count == 3, row.allSatisfy(\.isFinite), row[1] >= 0, row[1] < 100, abs(row[2]) <= 0.01,
                  index == 0 || row[0] > leaps[index - 1][0] else { throw EphemerisError.invalidResource }
        }
    }

    func taiMinusUTC(at unix: Double) -> Double? {
        guard unix.isFinite, unix >= leaps[0][0] else { return nil }
        guard let row = leaps.last(where: { $0[0] <= unix }) else { return nil }
        return row[1] + (unix - row[0]) / 86400 * row[2]
    }

    func times(at date: Date) -> TimeScales? {
        let unix = date.timeIntervalSince1970
        guard unix.isFinite, unix >= -3786825600, unix < 21458736000 else { return nil }
        let dat = taiMinusUTC(at: unix)
        let utc = unix / 86400 - 10957.5
        let tt = utc + (dat.map { $0 + 32.184 } ?? HistoricalTime.deltaT(at: unix)) / 86400
        let tdb = tt + lunar_tdb_minus_tt(tt) / 86400
        guard tdb.isFinite else { return nil }
        let mjd = utc + 51544.5
        var xp = 0.0, yp = 0.0, dx = 0.0, dy = 0.0
        var ut1 = utc
        var quality = EarthOrientationQuality.approximate
        var observedOffsets = false
        if let dat, mjd >= rows[0][0], mjd <= rows[rows.count - 1][0] {
            let index = min(Int(floor(mjd - rows[0][0])), rows.count - 2)
            let a = rows[index], b = rows[index + 1]
            // Use a continuous TAI timeline and UT1-TAI across leap seconds.
            let datA = taiMinusUTC(at: (a[0] - 40587) * 86400) ?? 32
            let datB = taiMinusUTC(at: (b[0] - 40587) * 86400) ?? 32
            let span = (b[0] - a[0]) * 86400 + datB - datA
            let weight = max(0, min(1, ((mjd - a[0]) * 86400 + dat - datA) / span))
            func interpolate(_ column: Int) -> Double { a[column] + (b[column] - a[column]) * weight }
            xp = interpolate(1); yp = interpolate(2)
            ut1 = utc + (dat + interpolate(3)) / 86400
            if a[7] > 0, b[7] > 0 { dx = interpolate(4); dy = interpolate(5) }
            observedOffsets = a[7] == 1 && b[7] == 1
            quality = a[6] == 0 && b[6] == 0 ? .observed : .predicted
        }
        return TimeScales(tdb: tdb, tt: tt, ut1: ut1, xp: xp, yp: yp, dX: dx, dY: dy,
            orientationQuality: quality, hasObservedPoleOffsets: observedOffsets,
            leapSecondsAssumed: unix >= leapSecondsConfirmedBefore, historicalTimeEstimated: dat == nil)
    }

    func date(fromTDB tdb: Double) -> Date? {
        guard tdb.isFinite, (-54787...237500).contains(tdb) else { return nil }
        var tt = tdb
        for _ in 0..<3 { tt = tdb - lunar_tdb_minus_tt(tt) / 86400 }
        let taiSeconds = (tt + 10957.5) * 86400 - 32.184
        // Before UTC, Date labels represent UT1 and use historical Delta T.
        // A Double day converted to Unix seconds can land a few microseconds
        // below an exact clock step. Snap only within 10 µs, well below the
        // phase solver's 0.1 ms resolution, not across the leap-second gap.
        let boundaryTolerance = 0.00001
        if taiSeconds < leaps[0][0] + leaps[0][1] - boundaryTolerance {
            let ttSeconds = taiSeconds + 32.184
            var unix = ttSeconds
            for _ in 0..<5 { unix = ttSeconds - HistoricalTime.deltaT(at: unix) }
            guard unix < leaps[0][0] else { return nil }
            return Date(timeIntervalSince1970: unix)
        }
        guard let entry = leaps.last(where: { taiSeconds >= $0[0] + $0[1] - boundaryTolerance }) else { return nil }
        // Invert both modern leap steps and pre-1972 UTC frequency drift.
        let unix = max(entry[0], entry[0] + (taiSeconds - entry[0] - entry[1]) / (1 + entry[2] / 86400))
        if let next = leaps.first(where: { $0[0] > entry[0] }), unix >= next[0] { return nil }
        return Date(timeIntervalSince1970: unix)
    }
}
