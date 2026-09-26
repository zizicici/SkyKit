import XCTest
import simd
import NativeSky
import SkyKit

final class MoonSurfaceTests: XCTestCase {
    private struct Fixtures: Decodable {
        let angularToleranceDegrees: Double
        let nasa: [Sample]
        let horizons: [Sample]
    }
    private struct Sample: Decodable {
        struct Observer: Decodable {
            let latitude: Double, longitude: Double, elevation: Double
        }
        let utc: String
        let observer: Observer?
        let diameterArcseconds: Double
        let subObserver: [Double]
        let subSolar: [Double]
        let northPolePositionAngle: Double
    }
    private func date(_ string: String) -> Date { ISO8601DateFormatter().date(from: string)! }
    private func surface(_ timestamp: String = "2026-09-22T12:00:00Z",
                         observer: MoonSurfaceObserver = .geocentric,
                         north: MoonSurfaceNorth = .trueOfDate) throws -> MoonSurface {
        try XCTUnwrap(Moon.surface(at: date(timestamp), observer: observer, north: north))
    }
    private func angularDifference(_ a: Double, _ b: Double) -> Double {
        abs((a - b + 540).truncatingRemainder(dividingBy: 360) - 180)
    }

    func testAgainstIndependentNASAAndHorizonsGeometry() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "MoonSurfaceFixtures", withExtension: "json"))
        let fixtures = try JSONDecoder().decode(Fixtures.self, from: Data(contentsOf: url))
        var maximumAngle = 0.0, maximumProjection = 0.0
        let samples = fixtures.nasa.map { ($0, MoonSurfaceNorth.icrf) }
            + fixtures.horizons.map { ($0, MoonSurfaceNorth.trueOfDate) }
        for (sample, north) in samples {
            let observer = sample.observer.map {
                MoonSurfaceObserver.earth(latitude: $0.latitude, longitude: $0.longitude, elevation: $0.elevation)
            } ?? .geocentric
            let value = try surface(sample.utc, observer: observer, north: north)
            let pairs = [
                (value.subObserver.longitude, sample.subObserver[0]),
                (value.subObserver.latitude, sample.subObserver[1]),
                (value.subSolar.longitude, sample.subSolar[0]),
                (value.subSolar.latitude, sample.subSolar[1]),
                (value.northPolePositionAngle, sample.northPolePositionAngle)
            ]
            for (index, pair) in pairs.enumerated() {
                let (actual, expected) = pair
                let difference = angularDifference(actual, expected)
                maximumAngle = max(maximumAngle, difference)
                XCTAssertLessThan(difference, fixtures.angularToleranceDegrees,
                                  "\(sample.utc) field \(index): actual \(actual), reference \(expected)")
            }
            XCTAssertEqual(value.angularDiameter * 3600, sample.diameterArcseconds,
                           accuracy: 0.2, sample.utc)
            // Independently project catalog points using the reference libration
            // angles and spherical trigonometry, without SkyKit's rotation matrix.
            for (latitude, longitude) in [(34.7244, -14.9086), (8.3487, 30.8346), (17.0, 59.1), (-43.3, -11.2)] {
                let coordinate = try XCTUnwrap(MoonSurfaceCoordinate(latitude: latitude, longitude: longitude))
                let actual = try XCTUnwrap(value.project(coordinate))
                let phi = latitude * .pi / 180
                let delta = (longitude - sample.subObserver[0]) * .pi / 180
                let b = sample.subObserver[1] * .pi / 180
                let p = sample.northPolePositionAngle * .pi / 180
                let east = cos(phi) * sin(delta)
                let north = sin(phi) * cos(b) - cos(phi) * sin(b) * cos(delta)
                let expectedX = east * cos(p) - north * sin(p)
                let expectedY = -east * sin(p) - north * cos(p)
                let error = hypot(Double(actual.point.x) - expectedX, Double(actual.point.y) - expectedY)
                maximumProjection = max(maximumProjection, error)
                XCTAssertLessThan(error, 0.004, sample.utc)
            }
        }
        print("Surface references: max angle error \(maximumAngle) deg; max projected error \(maximumProjection) lunar radii")
    }

    func testCenterFarSideAndIllumination() throws {
        let value = try surface()
        XCTAssertEqual(value.orientationModel, .iau2009)
        let center = try XCTUnwrap(value.project(value.subObserver))
        XCTAssertEqual(center.point.x, 0, accuracy: 1e-12)
        XCTAssertEqual(center.point.y, 0, accuracy: 1e-12)
        XCTAssertEqual(center.emissionCosine, 1, accuracy: 1e-12)
        XCTAssertTrue(center.isVisible)
        let farSide = try XCTUnwrap(MoonSurfaceCoordinate(latitude: -value.subObserver.latitude,
                                                         longitude: value.subObserver.longitude + 180))
        XCTAssertFalse(try XCTUnwrap(value.project(farSide)).isVisible)
        let solar = try XCTUnwrap(value.project(value.subSolar))
        XCTAssertEqual(solar.incidenceCosine, 1, accuracy: 1e-12)
        XCTAssertTrue(solar.isIlluminated)
        let midnight = try XCTUnwrap(MoonSurfaceCoordinate(latitude: -value.subSolar.latitude,
                                                          longitude: value.subSolar.longitude + 180))
        XCTAssertFalse(try XCTUnwrap(value.project(midnight)).isIlluminated)
        let phase = try XCTUnwrap(Moon.phase(at: value.date))
        XCTAssertEqual(value.illumination, phase.illumination, accuracy: 1e-8)
    }

    func testProjectionRoundTripsAcrossRotationAndLibration() throws {
        for timestamp in ["2026-01-03T12:00:00Z", "2026-04-15T12:00:00Z", "2026-09-22T12:00:00Z"] {
            let value = try surface(timestamp)
            for rotation in [0.0, 37, 90, 180, -83, 360] {
                for x in [-0.6, 0, 0.6] {
                    for y in [-0.6, 0, 0.6] {
                        let point = CGPoint(x: x, y: y)
                        let coordinate = try XCTUnwrap(value.coordinate(at: point, rotation: rotation))
                        let projected = try XCTUnwrap(value.project(coordinate, rotation: rotation))
                        XCTAssertTrue(projected.isVisible)
                        XCTAssertEqual(projected.point.x, x, accuracy: 1e-12)
                        XCTAssertEqual(projected.point.y, y, accuracy: 1e-12)
                    }
                }
            }
        }
    }

    func testClockwiseImageRotationAndRenderingAxes() throws {
        let value = try surface()
        let mare = try XCTUnwrap(MoonSurfaceCoordinate(latitude: 8.3487, longitude: 30.8346))
        let plain = try XCTUnwrap(value.project(mare))
        let rotated = try XCTUnwrap(value.project(mare, rotation: 90))
        XCTAssertGreaterThan(plain.point.x, 0) // Lunar east is image right near this date.
        XCTAssertEqual(rotated.point.x, -plain.point.y, accuracy: 1e-12)
        XCTAssertEqual(rotated.point.y, plain.point.x, accuracy: 1e-12)
        XCTAssertEqual(simd_determinant(value.bodyToView), 1, accuracy: 1e-12)
        let observer = value.bodyToView * value.observerDirection
        XCTAssertEqual(observer.x, 0, accuracy: 1e-12)
        XCTAssertEqual(observer.y, 0, accuracy: 1e-12)
        XCTAssertEqual(observer.z, 1, accuracy: 1e-12)
        XCTAssertEqual(value.sunDirectionInView.z, 2 * value.illumination - 1, accuracy: 1e-12)
    }

    func testNorthReferenceChangesOnlyImageRotation() throws {
        let ofDate = try surface()
        let icrf = try surface(north: .icrf)
        XCTAssertEqual(ofDate.northReference, .trueOfDate)
        XCTAssertEqual(icrf.northReference, .icrf)
        XCTAssertEqual(ofDate.subObserver, icrf.subObserver)
        XCTAssertEqual(ofDate.subSolar, icrf.subSolar)
        XCTAssertEqual(ofDate.illumination, icrf.illumination)
        let coordinate = try XCTUnwrap(MoonSurfaceCoordinate(latitude: 34.7244, longitude: -14.9086))
        let expected = try XCTUnwrap(ofDate.project(coordinate))
        let adjusted = try XCTUnwrap(icrf.project(coordinate,
            rotation: icrf.northPolePositionAngle - ofDate.northPolePositionAngle))
        XCTAssertEqual(expected.point.x, adjusted.point.x, accuracy: 1e-12)
        XCTAssertEqual(expected.point.y, adjusted.point.y, accuracy: 1e-12)
    }

    func testTopocentricObserverChangesLibrationAndPropagatesTimeQuality() throws {
        let geo = try surface()
        let observer = MoonSurfaceObserver.earth(latitude: 1.3521, longitude: 103.8198)
        let local = try surface(observer: observer)
        XCTAssertEqual(local.observer, observer)
        XCTAssertGreaterThan(simd_length(local.observerDirection - geo.observerDirection), 0.001)
        XCTAssertGreaterThan(abs(local.distance - geo.distance), 100)
        XCTAssertEqual(local.time.orientationQuality, .predicted)
        XCTAssertFalse(local.time.leapSecondsAssumed)
        let future = try surface("2050-01-01T00:00:00Z", observer: observer)
        XCTAssertEqual(future.time.orientationQuality, .approximate)
        XCTAssertTrue(future.time.leapSecondsAssumed)
        XCTAssertTrue(try surface("1900-01-01T00:00:00Z").time.historicalTimeEstimated)
    }

    func testInvalidDatesObserversCoordinatesAndPicksAreRejected() throws {
        for instant in [Date(timeIntervalSince1970: .nan), Date(timeIntervalSince1970: .infinity),
                        date("1849-12-01T00:00:00Z"), date("2700-01-01T00:00:00Z")] {
            XCTAssertNil(Moon.surface(at: instant))
        }
        let now = date("2026-09-22T12:00:00Z")
        for observer in [MoonSurfaceObserver.earth(latitude: 91, longitude: 0),
                         .earth(latitude: .nan, longitude: 0),
                         .earth(latitude: 0, longitude: 181),
                         .earth(latitude: 0, longitude: .infinity),
                         .earth(latitude: 0, longitude: 0, elevation: -1001),
                         .earth(latitude: 0, longitude: 0, elevation: .nan)] {
            XCTAssertNil(Moon.surface(at: now, observer: observer))
        }
        XCTAssertNil(MoonSurfaceCoordinate(latitude: 91, longitude: 0))
        XCTAssertNil(MoonSurfaceCoordinate(latitude: .nan, longitude: 0))
        XCTAssertNil(MoonSurfaceCoordinate(latitude: 0, longitude: .infinity))
        XCTAssertEqual(MoonSurfaceCoordinate(latitude: 0, longitude: 345)?.longitude, -15)
        XCTAssertEqual(MoonSurfaceCoordinate(latitude: 0, longitude: -195)?.longitude, 165)
        let value = try surface()
        XCTAssertNil(value.coordinate(at: CGPoint(x: 1.001, y: 0)))
        XCTAssertNil(value.coordinate(at: CGPoint(x: Double.nan, y: 0)))
        XCTAssertNil(value.coordinate(at: .zero, rotation: .infinity))
        XCTAssertNil(value.project(value.subObserver, rotation: .nan))
        let limb = try XCTUnwrap(value.coordinate(at: CGPoint(x: 1, y: 0)))
        XCTAssertFalse(try XCTUnwrap(value.project(limb)).isVisible)
    }

    func testConcurrentSurfaceReadsAreDeterministic() throws {
        let instant = date("2026-09-22T12:00:00Z")
        let expected = try surface()
        let lock = NSLock()
        var failures = 0
        DispatchQueue.concurrentPerform(iterations: 100) { _ in
            let actual = Moon.surface(at: instant)
            if actual?.subObserver != expected.subObserver || actual?.sunDirection != expected.sunDirection {
                lock.lock(); failures += 1; lock.unlock()
            }
        }
        XCTAssertEqual(failures, 0)
    }

    func testNativeInvalidInputLeavesOutputUntouched() {
        var output = [Double](repeating: 123, count: 10)
        XCTAssertEqual(lunar_surface_vectors(nil, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &output), -1)
        XCTAssertEqual(output, [Double](repeating: 123, count: 10))
    }
}
