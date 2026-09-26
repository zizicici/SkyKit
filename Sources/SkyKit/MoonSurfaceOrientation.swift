import Foundation
import simd

/// An explicit model choice for surface labels, independent of the DE440 orbit.
public enum MoonSurfaceOrientationModel: String, Sendable {
    /// IAU 2009 trigonometric approximation to the lunar mean Earth/polar axis frame.
    /// Not the high-precision DE440 principal-axis libration solution.
    case iau2009
}

enum MoonSurfaceOrientation {
    /// ICRF -> IAU_MOON (mean Earth/polar axis approximation).
    /// Coefficients: NAIF pck00011.tpc, BODY301_* and BODY3_NUT_PREC_ANGLES.
    /// The pole polynomials use TDB centuries; W uses TDB days since J2000.
    static func rotation(at tdb: Double) -> simd_double3x3 {
        let centuries = tdb / 36525
        let angles: [(Double, Double)] = [
            (125.045, -1935.5364525), (250.089, -3871.072905),
            (260.008, 475263.3328725), (176.625, 487269.629985),
            (357.529, 35999.0509575), (311.589, 964468.49931),
            (134.963, 477198.869325), (276.617, 12006.300765),
            (34.226, 63863.5132425), (15.134, -5806.6093575),
            (119.743, 131.84064), (239.961, 6003.1503825),
            (25.053, 473327.79642)
        ]
        let raTerms = [-3.8787, -0.1204, 0.0700, -0.0172, 0, 0.0072, 0, 0, 0, -0.0052, 0, 0, 0.0043]
        let decTerms = [1.5419, 0.0239, -0.0278, 0.0068, 0, -0.0029, 0.0009, 0, 0, 0.0008, 0, 0, -0.0009]
        let meridianTerms = [3.5610, 0.1208, -0.0642, 0.0158, 0.0252, -0.0066, -0.0047,
                             -0.0046, 0.0028, 0.0052, 0.0040, 0.0019, -0.0044]
        var ra = 269.9949 + 0.0031 * centuries
        var dec = 66.5392 + 0.0130 * centuries
        var meridian = 38.3213 + 13.17635815 * tdb - 1.4e-12 * tdb * tdb
        for index in angles.indices {
            let angle = Moon.normalizedDegrees(angles[index].0 + angles[index].1 * centuries) * .pi / 180
            ra += raTerms[index] * sin(angle)
            dec += decTerms[index] * cos(angle)
            meridian += meridianTerms[index] * sin(angle)
        }
        ra *= .pi / 180
        dec *= .pi / 180
        meridian = Moon.normalizedDegrees(meridian) * .pi / 180
        let equatorX = SIMD3(-sin(ra), cos(ra), 0)
        let equatorY = SIMD3(-sin(dec) * cos(ra), -sin(dec) * sin(ra), cos(dec))
        let pole = SIMD3(cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec))
        let x = cos(meridian) * equatorX + sin(meridian) * equatorY
        let y = -sin(meridian) * equatorX + cos(meridian) * equatorY
        return simd_double3x3(rows: [x, y, pole])
    }
}
