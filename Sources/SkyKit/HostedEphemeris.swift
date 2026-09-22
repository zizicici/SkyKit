import Foundation
#if os(iOS)
import BackgroundAssets
import System
#endif

public enum ResourceError: Error {
    case unsupportedDate, requiresNewerSystem, unavailableAssetPack, invalidAssetPack
}

internal struct HostedEphemerisPack: Codable, Sendable {
    let id, path: String
    let startTDB, endTDB: Double
    let sha256: String
    let bytes: Int

    func contains(_ start: Double, through end: Double) -> Bool {
        start >= startTDB && start < endTDB && end >= start && end < endTDB
    }
}

internal struct HostedEphemerisCatalog: Codable, Sendable {
    let schema: Int
    let packs: [HostedEphemerisPack]

    static func bundled() throws -> Self {
        guard let url = Bundle.module.url(forResource: "hosted-packs", withExtension: "json") else { throw EphemerisError.invalidResource }
        let catalog = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        guard catalog.schema == 1, catalog.packs.count == 1, Set(catalog.packs.map(\.id)).count == 1,
              catalog.packs.allSatisfy({ pack in
                  pack.startTDB.isFinite && pack.endTDB.isFinite && pack.startTDB < pack.endTDB
                  && pack.startTDB >= 54744.5 && pack.endTDB <= 237407.5
                  && pack.id.hasPrefix("skykit-de440-") && pack.path.hasPrefix("SkyKit/de440/")
                  && !pack.path.contains("..") && pack.sha256.count == 64
                  && pack.sha256.allSatisfy(\.isHexDigit) && (28...50_000_000).contains(pack.bytes)
              }) else { throw EphemerisError.invalidResource }
        return catalog
    }
}

/// Transport is injected so old-system policy and failure paths are testable
/// without a network or an App Store entitlement. Only Apple-hosted data is used
/// in production; there is no URLSession/JPL transport in the app.
internal protocol HostedEphemerisSource: Sendable {
    func localData(for pack: HostedEphemerisPack) throws -> Data?
    func download(_ pack: HostedEphemerisPack) async throws -> Data
}

#if os(iOS)
@available(iOS 26.0, *)
internal struct AppleHostedEphemerisSource: HostedEphemerisSource {
    func localData(for pack: HostedEphemerisPack) throws -> Data? {
        // contents() pins access to the data while the system manages files.
        // It does not initiate downloads. Missing local files are simply absent.
        try? AssetPackManager.shared.contents(at: FilePath(pack.path), searchingInAssetPackWithID: pack.id)
    }

    func download(_ pack: HostedEphemerisPack) async throws -> Data {
        let manager = AssetPackManager.shared
        let asset: AssetPack
        if #available(iOS 27.0, *) {
            guard let found = try await manager.manifest.assetPack(withID: pack.id) else {
                throw ResourceError.unavailableAssetPack
            }
            asset = found
        } else {
            asset = try await manager.assetPack(withID: pack.id)
        }
        if #available(iOS 26.4, *) {
            try await manager.ensureLocalAvailability(of: asset, requireLatestVersion: false)
        } else {
            try await manager.ensureLocalAvailability(of: asset)
        }
        return try manager.contents(at: FilePath(pack.path), searchingInAssetPackWithID: pack.id)
    }
}
#endif
