import Foundation

extension Ephemeris {
    /// Old systems intentionally have no remote provider and never consult the
    /// obsolete direct-download cache, even if it exists from a previous build.
    public static var supportsExtendedDates: Bool {
        #if os(iOS)
        if #available(iOS 26.0, *) { return true }
        #endif
        return false
    }

    public static func available(at date: Date, through end: Date? = nil) -> Ephemeris? {
        EphemerisResources.live.available(at: date, through: end ?? date)
    }

    public static func ensureAvailable(at date: Date) async throws -> Ephemeris {
        try await EphemerisResources.live.ensureAvailable(at: date)
    }
}

internal final class EphemerisResources: @unchecked Sendable {
    static let live: EphemerisResources = {
        var source: (any HostedEphemerisSource)?
        #if os(iOS)
        if #available(iOS 26.0, *) { source = AppleHostedEphemerisSource() }
        #endif
        return EphemerisResources(bundled: .shared, packs: (try? HostedEphemerisCatalog.bundled())?.packs ?? [], source: source)
    }()

    private let bundled: Ephemeris?
    private let packs: [HostedEphemerisPack]
    private let source: (any HostedEphemerisSource)?
    private let lock = NSLock()
    private var loaded = [String: Ephemeris]()
    private let downloads = HostedEphemerisDownloads()

    init(bundled: Ephemeris?, packs: [HostedEphemerisPack], source: (any HostedEphemerisSource)?) {
        self.bundled = bundled; self.packs = packs; self.source = source
    }

    private func pack(at date: Date, through end: Date) -> HostedEphemerisPack? {
        guard let startTDB = bundled?.dynamicalTime(at: date), let endTDB = bundled?.dynamicalTime(at: end) else { return nil }
        return packs.first { $0.contains(startTDB, through: endTDB) }
    }

    func available(at date: Date, through end: Date) -> Ephemeris? {
        guard date <= end else { return nil }
        if let bundled, bundled.covers(date, through: end) { return bundled }
        guard let source, let pack = pack(at: date, through: end) else { return nil }
        lock.lock(); defer { lock.unlock() }
        if let engine = loaded[pack.id], engine.covers(date, through: end) { return engine }
        guard let data = try? source.localData(for: pack), let engine = try? load(data, pack: pack),
              engine.covers(date, through: end) else { return nil }
        loaded[pack.id] = engine
        return engine
    }

    func ensureAvailable(at date: Date) async throws -> Ephemeris {
        guard let bundled, bundled.dynamicalTime(at: date) != nil else { throw ResourceError.unsupportedDate }
        // An old system must not fetch far-future data just because a phase
        // search near the last bundled date could cross into the next year.
        guard let source else {
            guard bundled.timeScales(at: date) != nil else { throw ResourceError.requiresNewerSystem }
            return bundled
        }
        let end = max(date, min(date.addingTimeInterval(40 * 86400), Date(timeIntervalSince1970: 21458735900)))
        if let engine = available(at: date, through: end) { return engine }
        guard let pack = pack(at: date, through: end) else { throw ResourceError.unsupportedDate }
        let engine = try await downloads.load(pack, source: source)
        guard engine.covers(date, through: end) else { throw ResourceError.invalidAssetPack }
        remember(engine, id: pack.id)
        return engine
    }

    private func remember(_ engine: Ephemeris, id: String) {
        lock.lock(); loaded[id] = engine; lock.unlock()
    }

    private func load(_ data: Data, pack: HostedEphemerisPack) throws -> Ephemeris {
        try Self.loadVerified(data, pack: pack)
    }

    static func loadVerified(_ data: Data, pack: HostedEphemerisPack) throws -> Ephemeris {
        guard data.count == pack.bytes else { throw ResourceError.invalidAssetPack }
        // The native reader owns a copy after initialization. A private temporary
        // snapshot prevents a managed-file replacement between hash and native read.
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: file) }
        try data.write(to: file, options: .atomic)
        return try Ephemeris(kernelURL: file, orientationData: Ephemeris.orientationData(), expectedHash: pack.sha256)
    }
}

private actor HostedEphemerisDownloads {
    private var pending = [String: Task<Ephemeris, Error>]()

    func load(_ pack: HostedEphemerisPack, source: any HostedEphemerisSource) async throws -> Ephemeris {
        if let task = pending[pack.id] { return try await task.value }
        let task = Task {
            let data = try await source.download(pack)
            return try EphemerisResources.loadVerified(data, pack: pack)
        }
        pending[pack.id] = task
        do {
            // Keep successful tasks so a late concurrent caller cannot start a
            // second load between task completion and the caller's cache update.
            return try await task.value
        } catch {
            pending[pack.id] = nil
            throw error
        }
    }
}
