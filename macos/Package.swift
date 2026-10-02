// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MimonitorToolbox",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "MimonitorToolbox",
            path: "Sources/MimonitorToolbox"
        ),
        .testTarget(
            name: "MimonitorToolboxTests",
            dependencies: ["MimonitorToolbox"],
            path: "Tests/MimonitorToolboxTests"
        )
    ]
)
