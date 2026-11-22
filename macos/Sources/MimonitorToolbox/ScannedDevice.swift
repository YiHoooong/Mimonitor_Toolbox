import Foundation

struct ScannedDevice: Equatable, Identifiable {
    let ip: String
    let model: String
    let serial: String

    init(ip: String, model: String, serial: String = "") {
        self.ip = ip
        self.model = model
        self.serial = serial
    }

    var id: String { ip }
    var isMonitor: Bool { model.lowercased().contains("mitv") }
    var label: String { "\(model) (\(ip))" }
}
