// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Nativol",
    platforms: [.macOS(.v12)],
    products: [
        .executable(name: "Nativol", targets: ["NativolApp"]),
        .library(name: "NativolCore", targets: ["NativolCore"])
    ],
    targets: [
        .target(name: "NativolCore", linkerSettings: [.linkedFramework("DiskArbitration"), .linkedFramework("IOKit")]),
        .executableTarget(name: "NativolApp", dependencies: ["NativolCore"]),
        .testTarget(name: "NativolCoreTests", dependencies: ["NativolCore"])
    ]
)
