import Foundation

/// 开机自启动（LaunchAgent）。
/// 原版在 Windows 写注册表 Run 键；macOS 等价物是写 ~/Library/LaunchAgents/<label>.plist。
/// 写入后下次登录自动加载（当前会话可用 launchctl 加载，这里仅写文件并提示重启登录）。
enum Autostart {
    static let label = "com.mimonitor.toolbox"

    static func plistURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    static func isEnabled() -> Bool {
        FileManager.default.fileExists(atPath: plistURL().path)
    }

    static func setEnabled(_ enabled: Bool, executablePath: String) throws {
        let url = plistURL()
        if enabled {
            guard !executablePath.isEmpty else {
                throw NSError(domain: "com.mimonitor.toolbox.autostart", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "无法定位当前可执行文件路径"])
            }
            let plist: [String: Any] = [
                "Label": label,
                "ProgramArguments": [executablePath, "--minimized"],
                "RunAtLoad": true,
                "ProcessType": "Interactive",
            ]
            let dir = url.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            try data.write(to: url, options: .atomic)
        } else if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
    }
}
