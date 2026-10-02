import Foundation

public enum PresetCommand: Equatable {
    case setting(String, String)
    case jni(String, String, Int)
    case refresh
    case colorGains(String, String, String)
    case hdrToneMapping(String)
}

/// Native Swift port of the device's picture write sequences. No process/language bridge.
public enum PicturePresetPlan {
    public static let colorTempToMtk = [0: 1, 1: 2, 2: 3, 3: 0, 4: 4, 5: 5, 8: 6]
    public static let hdrToneMappingToMtk = [0: 5, 1: 0, 2: 2, 3: 1]
    public static let hdrModes: Set<Int> = [11, 12, 13, 15, 16, 17, 18, 19, 22, 23,
                                            29, 30, 31, 32, 33, 39, 40, 41, 42, 43, 44]
    public static let settingsKeys = [
        "picture_mode", "tv_picture_light_sensor", "picture_backlight", "xiaomi_picture_backlight",
        "picture_brightness", "picture_contrast", "picture_saturation", "picture_hue", "picture_sharpness",
        "picture_color_temperature", "picture_red_gain", "picture_green_gain", "picture_blue_gain",
        "picture_local_dimming", "tv_picture_video_local_dimming", "picture_hdr_tone_mapping",
        "settings_display_hdr_color_tone", "picture_dynamic_definition", "picture_response_time",
        "tv_picture_advanced_video_color_space", "tv_picture_video_color_space",
    ]
    public static let jniKeys = [
        "g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode", "g_video__clr_temp",
        "g_video__vid_local_dimming", "g_video__light_sensor_switch",
        "g_video__vid_hdr_tone_mapping_mode", "g_video__vid_od_response_time",
        "g_video__vid_insert_black", "g_video__clr_gain_r", "g_video__clr_gain_g", "g_video__clr_gain_b",
    ]
    public static func freesyncKey(source: String) -> String {
        ["29", "30"].contains(source.trimmingCharacters(in: .whitespacesAndNewlines))
            ? "g_video__dp_adaptive_sync" : "g_video__freesync_switch"
    }

    public static func normalize(settings: [String: String], jni: [String: String], source: String) -> [String: String] {
        var values: [String: String] = [:]
        for key in settingsKeys {
            if let raw = settings[key], let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) {
                values[key] = String(value)
            }
        }
        func override(_ jniKey: String, _ keys: [String], map: [Int: Int]? = nil) {
            guard let raw = jni[jniKey], let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else { return }
            let mapped = map == nil ? value : map?[value]
            if let mapped { for key in keys { values[key] = String(mapped) } }
            else { for key in keys { values.removeValue(forKey: key) } }
        }
        override("g_disp__disp_back_light", ["picture_backlight", "xiaomi_picture_backlight"])
        override("g_video__vid_gamut_mapping_mode", ["tv_picture_advanced_video_color_space", "tv_picture_video_color_space"])
        override("g_video__clr_temp", ["picture_color_temperature"], map: [1: 0, 2: 1, 3: 2, 0: 3, 4: 4, 5: 5, 6: 8])
        override("g_video__vid_local_dimming", ["picture_local_dimming", "tv_picture_video_local_dimming"])
        override("g_video__light_sensor_switch", ["tv_picture_light_sensor"])
        override("g_video__vid_hdr_tone_mapping_mode", ["picture_hdr_tone_mapping"])
        override("g_video__vid_hdr_tone_mapping_mode", ["settings_display_hdr_color_tone"], map: [5: 0, 0: 1, 2: 2, 1: 3])
        override("g_video__vid_od_response_time", ["picture_response_time"])
        override("g_video__vid_insert_black", ["picture_dynamic_definition"])
        override("g_video__clr_gain_r", ["picture_red_gain"])
        override("g_video__clr_gain_g", ["picture_green_gain"])
        override("g_video__clr_gain_b", ["picture_blue_gain"])
        let fsKey = freesyncKey(source: source)
        if let raw = jni[fsKey], let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) {
            let onValue = fsKey == "g_video__freesync_switch" ? 3 : 1
            if value == 0 || value == onValue { values["freesync"] = value == onValue ? "1" : "0" }
        }
        return values
    }

    public static func apply(_ values: [String: String], source: String,
                             execute: (PresetCommand) throws -> Void) -> PresetApplyReport {
        var report = PresetApplyReport()
        func number(_ key: String, range: ClosedRange<Int>? = nil) throws -> Int {
            guard let raw = values[key], let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw PresetError("\(key) 没有有效值")
            }
            if let range, !range.contains(value) { throw PresetError("\(key) 的值 \(value) 超出范围") }
            return value
        }
        func text(_ key: String, range: ClosedRange<Int>? = nil) throws -> String {
            String(try number(key, range: range))
        }
        func item(_ label: String, _ commands: () throws -> [PresetCommand]) {
            do {
                for command in try commands() { try execute(command) }
                report.applied.append(label)
            } catch { report.failed.append("\(label): \(error.localizedDescription)") }
        }
        func jniSetting(_ label: String, _ key: String, _ setting: String,
                        alias: String? = nil, range: ClosedRange<Int>) {
            item(label) {
                let value = try text(setting, range: range)
                var commands: [PresetCommand] = [.jni(key, value, 3), .setting(setting, value)]
                if let alias { commands.append(.setting(alias, value)) }
                commands.append(.refresh)
                return commands
            }
        }
        // FreeSync locks the monitor in game mode. It MUST precede picture_mode.
        if values["freesync"] == nil {
            report.skipped.append("FreeSync：预设未记录此项，保持现状")
        } else {
            item("FreeSync") {
                let key = freesyncKey(source: source)
                let on = try number("freesync", range: 0...1) == 1
                return [.jni(key, on ? (key == "g_video__freesync_switch" ? "3" : "1") : "0", 3), .refresh]
            }
        }
        // Mode changes reset parameter defaults, so every parameter follows this command.
        item("画面模式") { [.setting("picture_mode", try text("picture_mode", range: 0...100))] }
        item("自动调整亮度") {
            let value = try text("tv_picture_light_sensor", range: 0...1)
            return [.jni("g_video__light_sensor_switch", value, 1), .setting("tv_picture_light_sensor", value)]
        }
        item("背光") {
            let value = try text("picture_backlight", range: 1...100)
            return [.jni("g_disp__disp_back_light", value, 3), .refresh,
                    .setting("picture_backlight", value), .setting("xiaomi_picture_backlight", value)]
        }
        for (label, key) in [("黑色级别", "picture_brightness"), ("对比度", "picture_contrast"),
                             ("饱和度", "picture_saturation"), ("色调", "picture_hue"), ("锐度", "picture_sharpness")] {
            item(label) { [.setting(key, try text(key, range: 0...100))] }
        }
        item("色温") {
            let value = try number("picture_color_temperature")
            guard let mtk = colorTempToMtk[value] else { throw PresetError("未知色温枚举") }
            return [.jni("g_video__clr_temp", String(mtk), 3), .setting("picture_color_temperature", String(value)), .refresh]
        }
        if (try? number("picture_color_temperature")) == 3 {
            item("色增益") {
                let r = try text("picture_red_gain", range: 524...1524)
                let g = try text("picture_green_gain", range: 524...1524)
                let b = try text("picture_blue_gain", range: 524...1524)
                return [.jni("g_video__clr_temp", "0", 3), .setting("picture_color_temperature", "3"),
                        .colorGains(r, g, b), .setting("picture_red_gain", r),
                        .setting("picture_green_gain", g), .setting("picture_blue_gain", b), .refresh]
            }
        } else { report.skipped.append("色增益：仅自定义色温生效") }
        jniSetting("精密控光", "g_video__vid_local_dimming", "picture_local_dimming",
                   alias: "tv_picture_video_local_dimming", range: 0...3)
        if let mode = try? number("picture_mode"), hdrModes.contains(mode) {
            item("HDR 色调映射") {
                let ui = try number("settings_display_hdr_color_tone")
                guard let mtk = hdrToneMappingToMtk[ui] else { throw PresetError("未知 HDR 色调映射枚举") }
                return [.hdrToneMapping(String(mtk)), .setting("picture_hdr_tone_mapping", String(mtk)),
                        .setting("settings_display_hdr_color_tone", String(ui)), .refresh]
            }
        } else { report.skipped.append("HDR 色调映射：当前预设不是 HDR 模式") }
        jniSetting("动态清晰度", "g_video__vid_insert_black", "picture_dynamic_definition", range: 0...3)
        jniSetting("灰阶响应时间", "g_video__vid_od_response_time", "picture_response_time", range: 1...3)
        jniSetting("色域", "g_video__vid_gamut_mapping_mode", "tv_picture_advanced_video_color_space",
                   alias: "tv_picture_video_color_space", range: 0...7)
        return report
    }
}
