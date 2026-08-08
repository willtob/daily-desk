// swift-tools-version: 5.9
import PackageDescription

// Swift 5 language mode on purpose: this is a small single-actor app and
// Swift 6's strict concurrency buys nothing here but ceremony.
let package = Package(
    name: "ESPNewsWidget",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "ESPNewsWidget",
            path: "Sources/ESPNewsWidget"
        )
    ]
)
