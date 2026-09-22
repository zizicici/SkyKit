import XCTest
// Deliberately not @testable: these cases double as a check that the package's
// public surface is complete enough for an app to build a Moon finder on.
import SkyKit

final class MoonPositionTests: XCTestCase {
    private func position(at date: Date, latitude: Double, longitude: Double,
                          elevation: Double = 0, refraction: Bool = false) throws -> MoonPosition {
        try XCTUnwrap(Moon.position(at: date, latitude: latitude, longitude: longitude,
                                    elevation: elevation, refraction: refraction))
    }

    // Independent fixtures: PyEphem 4.2.1, sea level, pressure=0 (no refraction).
    // UTC dates; exercises both hemispheres, a low Moon, the Arctic and the date line.
    func testPositionsAgainstIndependentEphemeris() throws {
        let samples: [(String, Double, Double, Double, Double)] = [
            ("2026-09-22T12:00:00Z", 1.3521, 103.8198, 129.44073, 55.49172),
            ("2026-09-22T00:00:00Z", 1.3521, 103.8198, 224.50294, -61.21699),
            ("2026-01-03T20:00:00Z", 51.5074, -0.1278, 89.31475, 32.66284),
            ("2026-03-20T10:00:00Z", -33.8688, 151.2093, 270.57579, -18.17343),
            ("2026-06-01T03:00:00Z", 40.7128, -74.006, 146.86423, 12.54194),
            ("2026-12-21T00:00:00Z", 80, 20, 251.39823, 24.19351),
            ("2026-04-15T18:00:00Z", 0, -179.9, 86.90703, 21.78684)
        ]
        for (timestamp, latitude, longitude, azimuth, altitude) in samples {
            let date = try XCTUnwrap(ISO8601DateFormatter().date(from: timestamp))
            let result = try position(at: date, latitude: latitude, longitude: longitude)
            let azimuthError = abs(Moon.normalizedDegrees(result.azimuth - azimuth + 180) - 180)
            XCTAssertLessThan(azimuthError, 1.0 / 60, timestamp)
            XCTAssertEqual(result.altitude, altitude, accuracy: 1.0 / 60, timestamp)
        }
    }

    func testMoonDirectionAgainstJPLHorizons() throws {
        struct Sample: Decodable {
            let utc: String
            let latitude, longitude, elevation, azimuth, altitude: Double
        }
        struct Fixtures: Decodable {
            let angularToleranceDegrees: Double
            let samples: [Sample]
        }
        let url = try XCTUnwrap(Bundle.module.url(forResource: "MoonHorizonsFixtures", withExtension: "json"))
        let fixtures = try JSONDecoder().decode(Fixtures.self, from: Data(contentsOf: url))
        XCTAssertEqual(fixtures.samples.count, 16)
        var maximumError = 0.0
        for sample in fixtures.samples {
            let date = try XCTUnwrap(ISO8601DateFormatter().date(from: sample.utc))
            let actual = try position(at: date, latitude: sample.latitude,
                longitude: sample.longitude, elevation: sample.elevation)
            let expected = MoonPosition(azimuth: sample.azimuth, altitude: sample.altitude)
            if abs(sample.latitude) == 90 {
                // North has no unique horizontal direction at the geographic
                // poles. Compare altitude, not different azimuth conventions.
                XCTAssertEqual(actual.altitude, expected.altitude, accuracy: fixtures.angularToleranceDegrees)
                XCTAssertTrue(actual.azimuth.isFinite)
                continue
            }
            let a = actual.northWestUp
            let b = expected.northWestUp
            // Angular separation is stable at the zenith, where azimuth alone is not.
            let separation = acos(max(-1, min(1, a.x * b.x + a.y * b.y + a.z * b.z))) * 180 / .pi
            maximumError = max(maximumError, separation)
            XCTAssertLessThan(separation, fixtures.angularToleranceDegrees,
                              "\(sample.utc), \(sample.latitude), \(sample.longitude), \(sample.elevation)m")
        }
        let attachment = XCTAttachment(string: "Maximum JPL Horizons angular error: \(maximumError * 60) arcminutes across 14 directional fixtures; 2 exact-pole fixtures check altitude only.")
        attachment.name = "JPL Moon accuracy"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testRefractionRaisesLowMoonWithoutChangingAzimuth() throws {
        let date = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-06-01T03:00:00Z"))
        let airless = try position(at: date, latitude: 40.7128, longitude: -74.006)
        let visible = try position(at: date, latitude: 40.7128, longitude: -74.006, refraction: true)
        XCTAssertEqual(visible.azimuth, airless.azimuth, accuracy: 0.00001)
        XCTAssertGreaterThan(visible.altitude, airless.altitude)
        XCTAssertLessThan(visible.altitude - airless.altitude, 0.1)
    }

    func testNativeVectorAndEllipsoidalHeightReachCameraFrame() throws {
        let date = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-09-22T12:00:00Z"))
        let native = try XCTUnwrap(Ephemeris.shared?.observe(at: date,
            latitude: 89, longitude: 103.8, ellipsoidalHeight: 2400))
        let actual = try position(at: date, latitude: 89, longitude: 103.8, elevation: 2400)
        XCTAssertEqual(actual.northWestUp.x, native.eastNorthUp.y, accuracy: 1e-14)
        XCTAssertEqual(actual.northWestUp.y, -native.eastNorthUp.x, accuracy: 1e-14)
        XCTAssertEqual(actual.northWestUp.z, native.eastNorthUp.z, accuracy: 1e-14)
        XCTAssertEqual(actual.orientationQuality, .predicted)

        let future = try XCTUnwrap(ISO8601DateFormatter().date(from: "2040-01-01T12:00:00Z"))
        let approximate = try position(at: future, latitude: 1.35, longitude: 103.8)
        XCTAssertEqual(approximate.orientationQuality, .approximate)
        XCTAssertTrue(approximate.leapSecondsAssumed)
    }

    func testPolesAndDateLineRemainFiniteAndContinuous() throws {
        let date = Date(timeIntervalSince1970: 1_790_078_400)
        for latitude in [-90.0, 0, 90] {
            let east = try position(at: date, latitude: latitude, longitude: 180)
            let west = try position(at: date, latitude: latitude, longitude: -180)
            XCTAssertTrue(east.azimuth.isFinite && east.altitude.isFinite)
            XCTAssertEqual(east.azimuth, west.azimuth, accuracy: 0.00001)
            XCTAssertEqual(east.altitude, west.altitude, accuracy: 0.00001)
            XCTAssertTrue((0..<360).contains(east.azimuth))
            XCTAssertTrue((-90...90).contains(east.altitude))
        }
    }

    func testUnsupportedDateAndInvalidCoordinatesReturnNoResult() throws {
        let unsupported = try XCTUnwrap(ISO8601DateFormatter().date(from: "2700-01-01T00:00:00Z"))
        XCTAssertNil(Moon.phase(at: unsupported))
        XCTAssertNil(Moon.nextQuarter(180, onOrAfter: unsupported))
        XCTAssertNil(Moon.position(at: unsupported, latitude: 0, longitude: 0))
        XCTAssertNil(Moon.position(at: Date(), latitude: .nan, longitude: 0))
        // Only the four quarter angles are accepted.
        XCTAssertNil(Moon.nextQuarter(45, onOrAfter: Date()))
    }
}
