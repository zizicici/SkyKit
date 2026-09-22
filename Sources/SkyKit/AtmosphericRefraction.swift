import Foundation

/// Standard-atmosphere angular correction (degrees), independently implemented
/// from NOAA's published piecewise equations. Not weather or terrain prediction.
/// https://gml.noaa.gov/grad/solcalc/calcdetails.html
public enum AtmosphericRefraction {
    public static func correction(at altitude: Double) -> Double {
        guard altitude.isFinite, (-90...90).contains(altitude), altitude < 85 else { return 0 }
        let h = altitude
        let arcseconds: Double
        if h > 5 {
            let t = tan(h * .pi / 180)
            arcseconds = 58.1 / t - 0.07 / pow(t, 3) + 0.000086 / pow(t, 5)
        } else if h > -0.575 {
            arcseconds = 1735 + h * (-518.2 + h * (103.4 + h * (-12.79 + h * 0.711)))
        } else {
            arcseconds = -20.774 / tan(h * .pi / 180)
        }
        return max(0, arcseconds / 3600)
    }
}
