import Foundation

struct ScannedDevice: Equatable, Identifiable {
    let ip: String
    let model: String
    var id: String { ip }
    var isMonitor: Bool { model.lowercased().contains("mitv") }
    var label: String { "\(model) (\(ip))" }
}
