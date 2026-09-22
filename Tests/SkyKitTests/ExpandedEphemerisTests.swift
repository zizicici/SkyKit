import XCTest
import CryptoKit
@testable import SkyKit

final class ExpandedEphemerisTests: XCTestCase {
    private func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }

    func testExpandedOfflineCoverageAndHistoricalClockOffsets() throws {
        let engine = try XCTUnwrap(Ephemeris.shared)
        for utc in ["1850-01-01T12:00:00Z", "1900-06-01T00:00:00Z", "1960-01-01T00:00:00Z",
                    "1999-12-01T00:00:00Z", "2051-01-01T00:00:00Z", "2149-12-01T00:00:00Z"] {
            let instant = date(utc)
            XCTAssertNotNil(engine.phase(at: instant), utc)
            XCTAssertNotNil(engine.observe(at: instant, latitude: 89, longitude: 20), utc)
            let event = try XCTUnwrap(engine.nextQuarter(2, onOrAfter: instant), utc)
            XCTAssertEqual(try XCTUnwrap(engine.phase(at: event)).angle, 180, accuracy: 1e-7)
        }
        let table = try JSONDecoder().decode(EarthOrientationTable.self, from: Ephemeris.orientationData())
        // ERFA 2.0.1 eraDat reference values, including fractional pre-1972 drift.
        for (utc, offset) in [("1960-01-01T00:00:00Z", 0.943482), ("1961-08-01T00:00:00Z", 1.64757),
                              ("1965-07-01T00:00:00Z", 3.974706), ("1968-02-01T00:00:00Z", 6.185682),
                              ("1972-01-01T00:00:00Z", 10.0)] {
            XCTAssertEqual(try XCTUnwrap(table.taiMinusUTC(at: date(utc).timeIntervalSince1970)), offset, accuracy: 1e-9)
        }
        // All clock-table transitions (not just the five recent leap seconds).
        for row in table.leaps {
            for offset in [0.0, 1, 3600, 86400] {
                let instant = Date(timeIntervalSince1970: row[0] + offset)
                let time = try XCTUnwrap(table.times(at: instant))
                XCTAssertEqual(try XCTUnwrap(table.date(fromTDB: time.tdb)).timeIntervalSince(instant), 0, accuracy: 0.00005, "UTC boundary \(row[0]), offset \(offset)")
            }
        }
        for year in [1850, 1860, 1900, 1920, 1941, 1959] {
            let instant = date("\(year)-07-01T12:00:00Z")
            let time = try XCTUnwrap(table.times(at: instant))
            XCTAssertEqual(try XCTUnwrap(table.date(fromTDB: time.tdb)).timeIntervalSince(instant), 0, accuracy: 0.00005)
        }
    }

    func testStandaloneRefractionAtHorizonAndBoundaries() {
        XCTAssertEqual(AtmosphericRefraction.correction(at: 0), 1735 / 3600.0, accuracy: 1e-12)
        XCTAssertEqual(AtmosphericRefraction.correction(at: 45), 0.0161194683, accuracy: 1e-9)
        for altitude in [-90.0, -1, -0.575, 0, 5, 85, 90] {
            let value = AtmosphericRefraction.correction(at: altitude)
            XCTAssertTrue(value.isFinite && value >= 0 && value < 0.7)
        }
        XCTAssertEqual(AtmosphericRefraction.correction(at: .nan), 0)
        XCTAssertEqual(AtmosphericRefraction.correction(at: 90), 0)
    }

    struct Fixture: Decodable {
        let start, end: Double
        let ranges: [String: String]
        let expectedKernel, expectedSHA256: String
    }
    private func fixture() throws -> Fixture {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "de440-ranges", withExtension: "json"))
        return try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
    }
    private func sample() throws -> (HostedEphemerisPack, Data) {
        let f = try fixture()
        let data = try XCTUnwrap(Data(base64Encoded: f.expectedKernel))
        return (HostedEphemerisPack(id: "test-2200", path: "test.bin", startTDB: f.start,
            endTDB: f.end, sha256: f.expectedSHA256, bytes: data.count), data)
    }

    func testOldSystemStopsAtBundledCoverage() async throws {
        let (pack, _) = try sample()
        let resources = EphemerisResources(bundled: .shared, packs: [pack], source: nil)
        let last = date("2149-12-30T00:00:00Z")
        let engine = try await resources.ensureAvailable(at: last)
        XCTAssertNotNil(engine.phase(at: last))
        let far = date("2200-06-01T12:00:00Z")
        XCTAssertNil(resources.available(at: far, through: far))
        do {
            _ = try await resources.ensureAvailable(at: far)
            XCTFail("Old systems must not load distant data")
        } catch ResourceError.requiresNewerSystem { }
    }

    /// Same policy as above, but through the real `EphemerisResources.live`
    /// rather than an injected source, so the system-version gate is exercised.
    func testSystemVersionControlsExtendedEphemeris() async throws {
        let last = date("2149-12-20T12:00:00Z")
        XCTAssertNotNil(Ephemeris.available(at: last)?.phase(at: last))
        // `#available(iOS 26, *)` alone is vacuously true off iOS, so mirror the
        // implementation's `#if os(iOS)` guard instead of only its version check.
        #if os(iOS)
        if #available(iOS 26.0, *) {
            XCTAssertTrue(Ephemeris.supportsExtendedDates)
            return
        }
        #endif
        XCTAssertFalse(Ephemeris.supportsExtendedDates)
        let far = date("2200-06-01T00:00:00Z")
        XCTAssertNil(Ephemeris.available(at: far))
        do {
            _ = try await Ephemeris.ensureAvailable(at: far)
            XCTFail("Systems without a hosted source must reject distant dates")
        } catch ResourceError.requiresNewerSystem { }
    }

    func testHostedDownloadAndOfflineReopen() async throws {
        let (pack, data) = try sample()
        let source = MockHostedSource(data: data)
        let resources = EphemerisResources(bundled: .shared, packs: [pack], source: source)
        let instant = date("2200-06-01T12:00:00Z")
        XCTAssertNil(resources.available(at: instant, through: instant))
        let engine = try await resources.ensureAvailable(at: instant)
        let expected = try XCTUnwrap(engine.phase(at: instant))
        XCTAssertNotNil(engine.observe(at: instant, latitude: 89, longitude: 20))
        let event = try XCTUnwrap(engine.nextQuarter(2, onOrAfter: instant))
        XCTAssertEqual(try XCTUnwrap(engine.phase(at: event)).angle, 180, accuracy: 1e-7)
        let cached = try XCTUnwrap(resources.available(at: instant, through: instant))
        XCTAssertEqual(try XCTUnwrap(cached.phase(at: instant)).angle, expected.angle)
        let reopened = EphemerisResources(bundled: .shared, packs: [pack], source: source)
        XCTAssertNotNil(reopened.available(at: instant, through: instant))
        XCTAssertEqual(source.count, 1)
    }

    func testRejectedHostedDataAndRetry() async throws {
        let (pack, data) = try sample()
        let instant = date("2200-06-01T12:00:00Z")
        for invalid in [Data(data.dropLast()), Data(repeating: 0, count: data.count)] {
            let source = MockHostedSource(data: invalid)
            let resources = EphemerisResources(bundled: .shared, packs: [pack], source: source)
            do { _ = try await resources.ensureAvailable(at: instant); XCTFail("Corrupt data accepted") } catch { }
            XCTAssertNil(resources.available(at: instant, through: instant))
        }
        let source = MockHostedSource(data: data, failures: 1)
        let resources = EphemerisResources(bundled: .shared, packs: [pack], source: source)
        do { _ = try await resources.ensureAvailable(at: instant); XCTFail("Expected failed download") } catch { }
        XCTAssertNil(resources.available(at: instant, through: instant))
        let retried = try await resources.ensureAvailable(at: instant)
        XCTAssertNotNil(retried.phase(at: instant))
        XCTAssertEqual(source.count, 2)
    }

    func testConcurrentRequestsShareDownload() async throws {
        let (pack, data) = try sample()
        let source = MockHostedSource(data: data)
        let resources = EphemerisResources(bundled: .shared, packs: [pack], source: source)
        let instant = date("2200-06-01T12:00:00Z")
        try await withThrowingTaskGroup(of: Void.self) { group in
            for _ in 0..<12 {
                group.addTask { _ = try await resources.ensureAvailable(at: instant) }
            }
            try await group.waitForAll()
        }
        XCTAssertEqual(source.count, 1)
    }

    func testHostedCatalogCoversEveryFutureYearAndSearchBoundary() throws {
        let catalog = try HostedEphemerisCatalog.bundled()
        let engine = try XCTUnwrap(Ephemeris.shared)
        for year in 2150...2649 {
            let instant = date("\(year)-01-01T00:00:00Z")
            let start = try XCTUnwrap(engine.dynamicalTime(at: instant))
            let end = try XCTUnwrap(engine.dynamicalTime(at: instant.addingTimeInterval(40 * 86400)))
            XCTAssertTrue(catalog.packs.contains { $0.contains(start, through: end) }, "\(year)")
        }
        let instant = date("2149-12-01T00:00:00Z")
        XCTAssertTrue(catalog.packs[0].contains(try XCTUnwrap(engine.dynamicalTime(at: instant)),
            through: try XCTUnwrap(engine.dynamicalTime(at: instant.addingTimeInterval(40 * 86400)))))
        XCTAssertNil(engine.dynamicalTime(at: date("2650-01-01T00:00:00Z")))
    }
}

private final class MockHostedSource: HostedEphemerisSource, @unchecked Sendable {
    private let lock = NSLock()
    private let data: Data
    private var local: Data?
    private var requests = 0
    private var failures: Int
    init(data: Data, failures: Int = 0) { self.data = data; self.failures = failures }
    var count: Int { lock.lock(); defer { lock.unlock() }; return requests }
    func localData(for pack: HostedEphemerisPack) throws -> Data? {
        lock.lock(); defer { lock.unlock() }; return local
    }
    private func begin() throws {
        lock.lock(); defer { lock.unlock() }; requests += 1
        if failures > 0 { failures -= 1; throw URLError(.notConnectedToInternet) }
    }
    private func finish() -> Data {
        lock.lock(); defer { lock.unlock() }; local = data; return data
    }
    func download(_ pack: HostedEphemerisPack) async throws -> Data {
        try begin()
        try await Task.sleep(nanoseconds: 30_000_000)
        return finish()
    }
}
