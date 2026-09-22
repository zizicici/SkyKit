import Foundation

/// Before UTC existed, interpret civil Date labels as UT1. Historical TT−UT1
/// estimate, not modern clock accuracy. Espenak & Meeus / NASA, 1850–1960 only.
/// https://eclipse.gsfc.nasa.gov/SEcat5/deltatpoly.html
internal enum HistoricalTime {
    static func deltaT(at unix: Double) -> Double {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let date = Date(timeIntervalSince1970: unix)
        let year = calendar.component(.year, from: date)
        let start = calendar.date(from: DateComponents(year: year, month: 1, day: 1))!
        let end = calendar.date(from: DateComponents(year: year + 1, month: 1, day: 1))!
        let y = Double(year) + date.timeIntervalSince(start) / end.timeIntervalSince(start)
        func polynomial(_ t: Double, _ coefficients: [Double]) -> Double {
            coefficients.reversed().reduce(0) { $0 * t + $1 }
        }
        switch y {
        case ..<1860: return polynomial(y - 1800, [13.72, -0.332447, 0.0068612, 0.0041116, -0.00037436, 0.0000121272, -0.0000001699, 0.000000000875])
        case ..<1900: return polynomial(y - 1860, [7.62, 0.5737, -0.251754, 0.01680668, -0.0004473624, 1 / 233174.0])
        case ..<1920: return polynomial(y - 1900, [-2.79, 1.494119, -0.0598939, 0.0061966, -0.000197])
        case ..<1941: return polynomial(y - 1920, [21.20, 0.84493, -0.076100, 0.0020936])
        default: return polynomial(y - 1950, [29.07, 0.407, -1 / 233.0, 1 / 2547.0])
        }
    }
}
