import XCTest
@testable import SkyKit

final class SkyKitTests: XCTestCase {
    func date(_ string: String) -> Date { ISO8601DateFormatter().date(from: string)! }

    func testBundledKernelAndKnownPhaseUseNativeCore() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        let phase = try XCTUnwrap(engine.phase(at: date("2026-09-22T12:00:00Z")))
        // Native validation with independently prepared ERFA time arguments.
        XCTAssertEqual(phase.angle, 130.0078985766636, accuracy: 1e-8)
        XCTAssertEqual(phase.illumination, 0.8220020330701003, accuracy: 1e-10)
        XCTAssertFalse(phase.leapSecondsAssumed)
    }

    func testTimeScaleGoldenValuesAndLeapSecond() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        let samples = [
            ("2000-01-01T12:00:00Z", 0.0007428703703703703, 0.0007428692212302617),
            ("2016-12-31T23:59:59Z", 6209.500777592593, 6209.50077759202),
            ("2017-01-01T00:00:00Z", 6209.5008007407405, 6209.5008007401675),
            ("2026-09-22T12:00:00Z", 9761.000800740741, 9761.000800722322)
        ]
        for (utc, tt, tdb) in samples {
            let actual = try XCTUnwrap(engine.timeScales(at: date(utc)))
            XCTAssertEqual(actual.tt, tt, accuracy: 1e-10)
            XCTAssertEqual(actual.tdb, tdb, accuracy: 1e-10)
            XCTAssertLessThan(abs(actual.ut1 - actual.tt) * 86400, 100)
        }
        let before = try XCTUnwrap(engine.timeScales(at: date(samples[1].0)))
        let after = try XCTUnwrap(engine.timeScales(at: date(samples[2].0)))
        // Foundation skips the label 23:59:60, but two SI seconds elapsed.
        XCTAssertEqual((after.tt - before.tt) * 86400, 2, accuracy: 1e-5)
        XCTAssertEqual((after.ut1 - before.ut1) * 86400, 2, accuracy: 0.0001)
    }

    func testOrientationCoverageAndExpiredDataAreExplicit() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        let past = try XCTUnwrap(engine.timeScales(at: date("2024-04-08T18:00:00Z")))
        XCTAssertEqual(past.orientationQuality, .observed)
        XCTAssertTrue(past.hasObservedPoleOffsets)
        let current = try XCTUnwrap(engine.timeScales(at: date("2026-09-22T12:00:00Z")))
        XCTAssertEqual(current.orientationQuality, .predicted)
        let future = try XCTUnwrap(engine.timeScales(at: date("2030-01-01T00:00:00Z")))
        XCTAssertEqual(future.orientationQuality, .approximate)
        XCTAssertTrue(future.leapSecondsAssumed)
        XCTAssertEqual(future.xp, 0); XCTAssertEqual(future.yp, 0)
        XCTAssertEqual(future.ut1, date("2030-01-01T00:00:00Z").timeIntervalSince1970 / 86400 - 10957.5)
    }

    func testBoundariesAndInvalidCoordinatesReturnNoNativeResult() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        for utc in ["1849-12-01T00:00:00Z", "2150-01-01T00:00:00Z"] {
            XCTAssertNil(engine.phase(at: date(utc)))
            XCTAssertNil(engine.observe(at: date(utc), latitude: 0, longitude: 0))
        }
        let now = date("2026-09-22T12:00:00Z")
        XCTAssertNil(engine.observe(at: now, latitude: 91, longitude: 0))
        XCTAssertNil(engine.observe(at: now, latitude: 0, longitude: .nan))
        XCTAssertNil(engine.observe(at: now, latitude: 0, longitude: 0, ellipsoidalHeight: .infinity))
        XCTAssertNil(engine.nextQuarter(4, onOrAfter: now))
        XCTAssertNil(engine.phase(at: Date(timeIntervalSince1970: .nan)))
    }

    func testQuarterUTCRoundTripAndInclusiveSearch() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        let event = try XCTUnwrap(engine.nextQuarter(2, onOrAfter: date("2026-09-22T12:00:00Z")))
        XCTAssertEqual(event.timeIntervalSince(date("2026-09-26T16:49:00Z")), 0, accuracy: 60)
        let phase = try XCTUnwrap(engine.phase(at: event))
        XCTAssertEqual(phase.angle, 180, accuracy: 1e-7)
        let same = try XCTUnwrap(engine.nextQuarter(2, onOrAfter: event))
        XCTAssertEqual(same.timeIntervalSince(event), 0, accuracy: 0.0001)
        let next = try XCTUnwrap(engine.nextQuarter(2, onOrAfter: event.addingTimeInterval(1)))
        XCTAssertTrue((29*86400...30*86400).contains(next.timeIntervalSince(event)))
    }

    func testUTCRoundTripAcrossEveryLeapBoundary() throws {
        let data = try Ephemeris.orientationData()
        let table = try JSONDecoder().decode(EarthOrientationTable.self, from: data)
        for boundary in ["2006-01-01T00:00:00Z", "2009-01-01T00:00:00Z", "2012-07-01T00:00:00Z",
                         "2015-07-01T00:00:00Z", "2017-01-01T00:00:00Z"] {
            for offset in [-2.0, -1, 0, 1, 2] {
                let utc = date(boundary).addingTimeInterval(offset)
                let t = try XCTUnwrap(table.times(at: utc))
                let roundTrip = try XCTUnwrap(table.date(fromTDB: t.tdb))
                XCTAssertEqual(roundTrip.timeIntervalSince(utc), 0, accuracy: 0.00001)
            }
        }
    }

    func testENUConventionAndConcurrentReads() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        let utc = date("2026-09-22T12:00:00Z")
        let expected = try XCTUnwrap(engine.observe(at: utc, latitude: 1.3521, longitude: 103.8198))
        let az = expected.azimuth * .pi / 180, alt = expected.altitude * .pi / 180
        XCTAssertEqual(expected.eastNorthUp.x, sin(az)*cos(alt), accuracy: 1e-12)
        XCTAssertEqual(expected.eastNorthUp.y, cos(az)*cos(alt), accuracy: 1e-12)
        XCTAssertEqual(expected.eastNorthUp.z, sin(alt), accuracy: 1e-12)
        let lock = NSLock(); var mismatches = 0
        DispatchQueue.concurrentPerform(iterations: 100) { _ in
            let value = engine.observe(at: utc, latitude: 1.3521, longitude: 103.8198)
            if value?.eastNorthUp != expected.eastNorthUp {
                lock.lock(); mismatches += 1; lock.unlock()
            }
        }
        XCTAssertEqual(mismatches, 0)
    }

    func testCorruptResourceFailsClosed() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data("invalid kernel".utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        let data = try Ephemeris.orientationData()
        XCTAssertThrowsError(try Ephemeris(kernelURL: url, orientationData: data))
        XCTAssertThrowsError(try Ephemeris(kernelURL: url, orientationData: Data("{}".utf8)))
    }
}
