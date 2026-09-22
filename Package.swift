// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "SkyKit",
    platforms: [.iOS(.v15), .macOS(.v12)],
    products: [.library(name: "SkyKit", targets: ["SkyKit"])],
    targets: [
        .target(name: "NativeSky", publicHeadersPath: "include",
                cSettings: [.headerSearchPath("erfa")], cxxSettings: [.headerSearchPath("erfa")]),
        .target(name: "SkyKit", dependencies: ["NativeSky"], resources: [.process("Resources")]),
        .testTarget(name: "SkyKitTests", dependencies: ["SkyKit", "NativeSky"], resources: [.process("Fixtures")])
    ],
    cxxLanguageStandard: .cxx17
)
