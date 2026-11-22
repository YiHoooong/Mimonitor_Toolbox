import Foundation

/// 通过单次 ADB shell 读取到的设备标识（型号 + 稳定唯一标识）。
struct DeviceIdentity: Equatable {
    let model: String
    let serial: String

    /// 一次 shell 往返同时采集型号、序列号与网卡 MAC。
    /// 同型号的多台显示器（如多台 MiTV-MUFF1）型号完全相同，
    /// 需要靠 ro.serialno / ro.boot.serialno（或 MAC 兜底）区分具体机器。
    static let probeShellCommand =
        "printf 'model=%s\\nserial=%s\\nboot_serial=%s\\nwlan_mac=%s\\neth_mac=%s\\n' "
        + "\"$(getprop ro.product.model)\" "
        + "\"$(getprop ro.serialno)\" "
        + "\"$(getprop ro.boot.serialno)\" "
        + "\"$(cat /sys/class/net/wlan0/address 2>/dev/null)\" "
        + "\"$(cat /sys/class/net/eth0/address 2>/dev/null)\""

    private static let invalidSerials: Set<String> = [
        "unknown",
        "none",
        "null",
        "n/a",
        "0123456789abcdef",
        "0000000000000000",
        "00000000"
    ]

    private static let invalidMacs: Set<String> = [
        "000000000000",
        "020000000000",
        "ffffffffffff"
    ]

    /// 解析 `probeShellCommand` 的输出；同时兼容只返回单行型号的输出（如旧版 mock / 单测）。
    static func parse(shellOutput: String) -> DeviceIdentity? {
        let lines = shellOutput
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !lines.isEmpty else { return nil }

        let knownKeys: Set<String> = ["model", "serial", "boot_serial", "wlan_mac", "eth_mac"]
        var fields: [String: String] = [:]
        var hasStructuredFields = false

        for line in lines {
            guard let eqIndex = line.firstIndex(of: "=") else { continue }
            let key = String(line[..<eqIndex]).trimmingCharacters(in: .whitespacesAndNewlines)
            guard knownKeys.contains(key) else { continue }
            hasStructuredFields = true
            let value = String(line[line.index(after: eqIndex)...])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            fields[key] = value
        }

        if hasStructuredFields {
            let model = fields["model"] ?? ""
            guard isValidModel(model) else { return nil }
            let resolvedSerial =
                normalizedSerial(fields["serial"])
                ?? normalizedSerial(fields["boot_serial"])
                ?? normalizedMac(fields["wlan_mac"])
                ?? normalizedMac(fields["eth_mac"])
                ?? ""
            return DeviceIdentity(model: model, serial: resolvedSerial)
        }

        // 兼容仅返回单行 ro.product.model 的情况
        let raw = lines[0]
        guard isValidModel(raw) else { return nil }
        return DeviceIdentity(model: raw, serial: "")
    }

    private static func isValidModel(_ raw: String) -> Bool {
        let lower = raw.lowercased()
        let looksLikeError = raw.isEmpty
            || lower.contains("error")
            || lower.contains("offline")
            || lower.contains("unauthorized")
            || lower.contains("not found")
            || lower.contains("adb:")
        return !looksLikeError
    }

    private static func normalizedSerial(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let lower = trimmed.lowercased()
        guard !invalidSerials.contains(lower),
              !lower.contains("error"),
              !lower.contains("not found") else { return nil }
        return trimmed
    }

    private static func normalizedMac(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let cleaned = raw
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: "-", with: "")
            .lowercased()
        guard cleaned.count == 12,
              cleaned.allSatisfy({ $0.isHexDigit }),
              !invalidMacs.contains(cleaned) else { return nil }
        return "MAC-\(cleaned.uppercased())"
    }
}

/// 本地持久化的已知显示器记录。
/// 用稳定序列号（或兜底 MAC / IP）作为主键，支持给同型号多台机器设置独立备注，
/// 并在路由器 DHCP 分配新 IP 后通过序列号自动匹配并保留备注。
struct KnownDevice: Codable, Equatable, Identifiable {
    let id: String
    var ip: String
    var model: String
    var serial: String
    var alias: String
    var lastSeenAt: Date

    var trimmedAlias: String {
        alias.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var hasAlias: Bool {
        !trimmedAlias.isEmpty
    }

    /// 优先显示用户备注，其次型号，最后兜底 IP。
    var displayName: String {
        if hasAlias { return trimmedAlias }
        return model.isEmpty ? ip : model
    }

    /// 列表/下拉菜单中展示的完整标签，如「左屏 (192.168.1.20)」或「MiTV-MUFF1 (192.168.1.20)」。
    var label: String {
        if ip.isEmpty { return displayName }
        if hasAlias { return "\(trimmedAlias) (\(ip))" }
        return model.isEmpty ? ip : "\(model) (\(ip))"
    }

    /// 状态栏连接详情，如「左屏 · 192.168.1.20」或「MiTV-MUFF1 · 192.168.1.20」。
    var statusDetail: String {
        if ip.isEmpty { return displayName }
        if hasAlias { return "\(trimmedAlias) · \(ip)" }
        return model.isEmpty ? ip : "\(model) · \(ip)"
    }

    /// 弹窗里展示的序列号/标识文案。
    var identityDescription: String {
        serial.isEmpty ? "未获取到序列号（按 IP 记录）" : serial
    }
}

/// 已知设备列表的持久化与纯函数更新逻辑。
enum KnownDeviceStore {
    static let storageKey = "known_devices_v1"

    static func load(defaults: UserDefaults = .standard, key: String = storageKey) -> [KnownDevice] {
        guard let data = defaults.data(forKey: key) else { return [] }
        return (try? JSONDecoder().decode([KnownDevice].self, from: data)) ?? []
    }

    static func save(_ devices: [KnownDevice], defaults: UserDefaults = .standard, key: String = storageKey) {
        guard let data = try? JSONEncoder().encode(devices) else { return }
        defaults.set(data, forKey: key)
    }

    static func canonicalID(ip: String, serial: String) -> String {
        let trimmedSerial = serial.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmedSerial.isEmpty ? "ip:\(ip)" : trimmedSerial
    }

    /// 新增或更新一台设备：
    /// - 同一序列号换了 IP（DHCP 变更）：更新 IP 并保留原备注；
    /// - 之前没读到序列号留下的 `ip:<ip>` 占位记录：无缝迁移到真实序列号并继承备注；
    /// - 另一台设备的旧 IP 被当前新设备占用：清空旧设备的 IP（保留其序列号与备注，待下次重扫恢复），
    ///   避免设备下拉列表出现重复 IP。
    static func upsert(
        _ devices: [KnownDevice],
        ip: String,
        model: String,
        serial: String,
        now: Date = Date()
    ) -> [KnownDevice] {
        let trimmedIP = ip.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedIP.isEmpty else { return devices }
        let trimmedModel = model.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedSerial = serial.trimmingCharacters(in: .whitespacesAndNewlines)
        let id = canonicalID(ip: trimmedIP, serial: trimmedSerial)
        let legacyIPID = "ip:\(trimmedIP)"

        var updated = devices
        var inheritedAlias = ""
        var inheritedModel = ""

        // 如果拿到了真实序列号，先合并并移除同 IP 的旧占位记录
        if !trimmedSerial.isEmpty, let legacyIdx = updated.firstIndex(where: { $0.id == legacyIPID }) {
            inheritedAlias = updated[legacyIdx].trimmedAlias
            inheritedModel = updated[legacyIdx].model
            updated.remove(at: legacyIdx)
        }

        // 如果本次没拿到序列号，但已有一条带真实序列号的记录正是这个 IP，
        // 优先更新那条记录，避免重复新建 `ip:<ip>` 占位项。
        let targetIndex: Int?
        if trimmedSerial.isEmpty {
            targetIndex = updated.firstIndex(where: { $0.ip == trimmedIP })
                ?? updated.firstIndex(where: { $0.id == id })
        } else {
            targetIndex = updated.firstIndex(where: { $0.id == id })
        }

        if let idx = targetIndex {
            var existing = updated.remove(at: idx)
            existing.ip = trimmedIP
            if !trimmedModel.isEmpty {
                existing.model = trimmedModel
            } else if existing.model.isEmpty && !inheritedModel.isEmpty {
                existing.model = inheritedModel
            }
            if !trimmedSerial.isEmpty {
                existing.serial = trimmedSerial
            }
            if existing.trimmedAlias.isEmpty && !inheritedAlias.isEmpty {
                existing.alias = inheritedAlias
            }
            existing.lastSeenAt = now
            // 如果其他序列号的旧记录还挂着同一个 IP，清空其 IP 避免下拉框重复
            for i in updated.indices where updated[i].ip == trimmedIP {
                updated[i].ip = ""
            }
            updated.insert(existing, at: 0)
        } else {
            // 新记录：如果其他旧记录还占着这个 IP，清空其 IP 但保留备注
            for i in updated.indices where updated[i].ip == trimmedIP {
                updated[i].ip = ""
            }
            let record = KnownDevice(
                id: id,
                ip: trimmedIP,
                model: trimmedModel.isEmpty ? inheritedModel : trimmedModel,
                serial: trimmedSerial,
                alias: inheritedAlias,
                lastSeenAt: now
            )
            updated.insert(record, at: 0)
        }

        return updated
    }

    static func setAlias(_ devices: [KnownDevice], id: String, alias: String) -> [KnownDevice] {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        return devices.map { device in
            guard device.id == id else { return device }
            var copy = device
            copy.alias = trimmed
            return copy
        }
    }

    static func remove(_ devices: [KnownDevice], id: String) -> [KnownDevice] {
        devices.filter { $0.id != id }
    }

    static func device(in devices: [KnownDevice], matchingIP ip: String) -> KnownDevice? {
        let trimmed = ip.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return devices.first { $0.ip == trimmed }
    }
}
