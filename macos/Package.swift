// swift-tools-version:5.9
import PackageDescription

var targets: [Target] = [
    .target(name: "MimonitorPresetCore", path: "Sources/MimonitorPresetCore"),
    .testTarget(name: "MimonitorPresetCoreTests", dependencies: ["MimonitorPresetCore"],
                path: "Tests/MimonitorPresetCoreTests"),
]
#if os(macOS)
targets += [
    .executableTarget(name: "MimonitorToolbox", dependencies: ["MimonitorPresetCore"],
                      path: "Sources/MimonitorToolbox"),
]
#else
// Foundation-only device/state tests can run on Linux; the actual app remains macOS-native.
targets += [
    .target(name: "MimonitorToolbox", dependencies: ["MimonitorPresetCore"],
            path: "Sources/MimonitorToolbox",
            exclude: ["App.swift", "AppState.swift", "AppState+Presets.swift", "ContentView.swift", "MenuBarCatalog.swift",
                      "NetworkScan.swift", "Platform", "Views"]),
]
#endif
targets += [.testTarget(name: "MimonitorToolboxTests", dependencies: ["MimonitorToolbox", "MimonitorPresetCore"],
                        path: "Tests/MimonitorToolboxTests")]

let package = Package(
    name: "MimonitorToolbox",
    platforms: [.macOS(.v13)],
    targets: targets
)
