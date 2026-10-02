import Foundation

public struct PicturePreset: Codable, Equatable, Identifiable {
    public static let baselineID = "__baseline__"
    public var id: String
    public var name: String
    public var values: [String: String]
    public init(id: String = UUID().uuidString, name: String, values: [String: String]) {
        self.id = id; self.name = name; self.values = values
    }
}

public struct ScheduledPresetTask: Codable, Equatable, Identifiable {
    public var id: String
    public var presetID: String
    public var start: String
    public var end: String
    public var enabled: Bool
    public init(id: String = UUID().uuidString, presetID: String, start: String, end: String,
                enabled: Bool = true) {
        self.id = id; self.presetID = presetID; self.start = start; self.end = end
        self.enabled = enabled
    }
    public var isValid: Bool {
        guard !id.isEmpty, !presetID.isEmpty,
              let start = PresetSchedule.minute(start), let end = PresetSchedule.minute(end)
        else { return false }
        return start != end
    }
    public func contains(minute: Int) -> Bool {
        guard enabled, isValid, (0..<1440).contains(minute),
              let start = PresetSchedule.minute(start), let end = PresetSchedule.minute(end)
        else { return false }
        return start < end ? start <= minute && minute < end : minute >= start || minute < end
    }
}

public enum PresetSchedule {
    public static func timeDate(_ text: String, calendar: Calendar = .current) -> Date? {
        guard let minute = Self.minute(text) else { return nil }
        // A stable reference day avoids normalizing 02:30 away on today's DST boundary.
        return calendar.date(from: DateComponents(year: 2001, month: 1, day: 15,
                                                   hour: minute / 60, minute: minute % 60, second: 0))
    }
    public static func timeText(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }
    public static func minute(_ text: String) -> Int? {
        let chars = Array(text.utf8)
        guard chars.count == 5, chars[2] == 58,
              [chars[0], chars[1], chars[3], chars[4]].allSatisfy({ (48...57).contains($0) })
        else { return nil }
        let hour = Int(chars[0] - 48) * 10 + Int(chars[1] - 48)
        let minute = Int(chars[3] - 48) * 10 + Int(chars[4] - 48)
        return hour < 24 && minute < 60 ? hour * 60 + minute : nil
    }
    public static func activeTask(_ tasks: [ScheduledPresetTask], minute: Int) -> ScheduledPresetTask? {
        tasks.last { $0.contains(minute: minute) }
    }
    public static func occurrenceStart(_ task: ScheduledPresetTask, minute: Int,
                                       now: Date = Date(), calendar: Calendar = .current) -> Date? {
        guard let start = Self.minute(task.start), let end = Self.minute(task.end) else { return nil }
        var day = calendar.startOfDay(for: now)
        if start > end && minute < end { day = calendar.date(byAdding: .day, value: -1, to: day) ?? day }
        return calendar.date(bySettingHour: start / 60, minute: start % 60, second: 0, of: day)
    }
}

/// The ordinary, non-preset state. This is NOT an automatic task snapshot.
public struct PresetBaseline: Codable, Equatable {
    public var deviceIdentity: String
    public var values: [String: String]
}

/// Scheduling remembers identity only; returning uses the preset's latest values.
public struct PresetTaskSession: Codable, Equatable {
    public var previousPresetID: String?
    public var deviceIdentity: String
    public var taskID: String
    public var appliedTargetID: String?
    public var occurrenceStart: Date?
}

public struct PresetConfiguration: Codable, Equatable {
    public static let menuBarPresetLimit = 4
    public var normalizedMenuBarPresetIDs: [String] {
        let valid = Set(presets.map(\.id)).union([PicturePreset.baselineID])
        var seen = Set<String>()
        return Array(menuBarPresetIDs.filter { valid.contains($0) && seen.insert($0).inserted }
            .prefix(Self.menuBarPresetLimit))
    }
    public func canEnableMenuBarPreset(id: String) -> Bool {
        guard id == PicturePreset.baselineID || presets.contains(where: { $0.id == id }) else { return false }
        let enabled = normalizedMenuBarPresetIDs
        return enabled.contains(id) || enabled.count < Self.menuBarPresetLimit
    }
    public var menuBarPresetIDs: [String] = []
    public var menuBarPresets: [PicturePreset] {
        let enabled = Set(normalizedMenuBarPresetIDs)
        var result: [PicturePreset] = []
        if enabled.contains(PicturePreset.baselineID) {
            result.append(PicturePreset(id: PicturePreset.baselineID, name: "无预设", values: [:]))
        }
        result.append(contentsOf: presets.filter { enabled.contains($0.id) })
        return result
    }
    public var presets: [PicturePreset] = []
    public var tasks: [ScheduledPresetTask] = []
    public var baseline: PresetBaseline?
    public var activePresetID: String?
    public var activeDeviceIdentity: String?
    public var session: PresetTaskSession?
    public var applicationIncomplete = false
    public init() {}
    private enum CodingKeys: String, CodingKey {
        case presets, tasks, baseline, activePresetID, activeDeviceIdentity, session, applicationIncomplete, menuBarPresetIDs
    }
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        presets = try container.decodeIfPresent([PicturePreset].self, forKey: .presets) ?? []
        tasks = try container.decodeIfPresent([ScheduledPresetTask].self, forKey: .tasks) ?? []
        baseline = try container.decodeIfPresent(PresetBaseline.self, forKey: .baseline)
        activePresetID = try container.decodeIfPresent(String.self, forKey: .activePresetID)
        activeDeviceIdentity = try container.decodeIfPresent(String.self, forKey: .activeDeviceIdentity)
        session = try container.decodeIfPresent(PresetTaskSession.self, forKey: .session)
        applicationIncomplete = try container.decodeIfPresent(Bool.self, forKey: .applicationIncomplete) ?? false
        menuBarPresetIDs = try container.decodeIfPresent([String].self, forKey: .menuBarPresetIDs) ?? []
        menuBarPresetIDs = normalizedMenuBarPresetIDs
    }
}

public struct PresetApplyReport: Equatable {
    public var applied: [String]
    public var failed: [String]
    public var skipped: [String]
    public var ok: Bool { failed.isEmpty && !applied.isEmpty }
    public init(applied: [String] = [], failed: [String] = [], skipped: [String] = []) {
        self.applied = applied; self.failed = failed; self.skipped = skipped
    }
    public var summary: String {
        "成功 \(applied.count) 项，跳过 \(skipped.count) 项，失败 \(failed.count) 项"
    }
}

public struct PresetError: LocalizedError {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public protocol PresetDevice {
    var identity: String { get }
    func capture() throws -> [String: String]
    func apply(_ values: [String: String]) throws -> PresetApplyReport
}

/// Accessed on the same serial queue as PresetEngine. Failed decode never overwrites saved data.
public final class PresetStore {
    public let defaults: UserDefaults
    public private(set) var configuration = PresetConfiguration()
    public private(set) var loadError: String?
    private let key = "picture_presets_v1"
    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: key) {
            do { configuration = try JSONDecoder().decode(PresetConfiguration.self, from: data) }
            catch { loadError = "预设配置无法读取，已保留原数据：\(error.localizedDescription)" }
        }
    }
    func save(_ configuration: PresetConfiguration) throws {
        if let loadError { throw PresetError(loadError) }
        let data = try JSONEncoder().encode(configuration)
        defaults.set(data, forKey: key)
        self.configuration = configuration
    }
}
