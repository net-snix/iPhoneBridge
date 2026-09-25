// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "iPhoneBridge",
    platforms: [.macOS("26.0")],
    targets: [.executableTarget(name: "iPhoneBridge", path: "Sources/iPhoneBridge"),
              .testTarget(name: "iPhoneBridgeTests", dependencies: ["iPhoneBridge"], path: "tests/iPhoneBridgeTests")]
)
