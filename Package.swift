// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "iPhoneBridge",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "iPhoneBridge", path: "Sources/iPhoneBridge")]
)
