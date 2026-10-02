import Foundation
import MimonitorPresetCore

/// Captures the target at submission time; changing adb.ip cannot redirect a running preset.
final class AdbPresetDevice: PresetDevice {
    let identity: String
    private let target: String
    private let adb: AdbClient
    init(adb: AdbClient, identity: String) {
        self.adb = adb; self.identity = identity; self.target = "\(identity):5555"
    }

    private func source() throws -> String {
        let source = try adb.checkedShell("settings get global mitv.tvplayer.hdmi.last.source", target: target)
        guard ["23", "24", "29", "30"].contains(source) else {
            throw PresetError("无法确定当前输入源，未写入 FreeSync")
        }
        return source
    }

    func capture() throws -> [String: String] {
        try adb.transaction {
            let output = try adb.checkedShell("settings list global", target: target)
            var settings: [String: String] = [:]
            for line in output.split(separator: "\n") {
                guard let index = line.firstIndex(of: "=") else { continue }
                settings[String(line[..<index])] = String(line[line.index(after: index)...])
            }
            let source = try source()
            let fsKey = PicturePresetPlan.freesyncKey(source: source)
            let keys = PicturePresetPlan.jniKeys.filter { key in
                if key == "g_video__vid_hdr_tone_mapping_mode" {
                    return RegisterMap.isHdrToneMappingPictureMode(settings["picture_mode"])
                }
                if ["g_video__clr_gain_r", "g_video__clr_gain_g", "g_video__clr_gain_b"].contains(key) {
                    return settings["picture_color_temperature"] == "3"
                }
                return true
            }
            var jni = try adb.jniBatchGetChecked(keys: keys + [fsKey], target: target)
            // Settings can lag behind the OSD. If JNI reveals custom temperature, read real gains.
            if jni["g_video__clr_temp"] == "0", !keys.contains("g_video__clr_gain_r") {
                let gains = try adb.jniBatchGetChecked(keys: ["g_video__clr_gain_r", "g_video__clr_gain_g", "g_video__clr_gain_b"], target: target)
                jni.merge(gains) { _, fresh in fresh }
            }
            let values = PicturePresetPlan.normalize(settings: settings, jni: jni, source: source)
            var required = ["picture_mode", "tv_picture_light_sensor", "picture_backlight",
                            "picture_brightness", "picture_contrast", "picture_saturation", "picture_hue",
                            "picture_sharpness", "picture_color_temperature", "picture_local_dimming",
                            "picture_dynamic_definition", "picture_response_time",
                            "tv_picture_advanced_video_color_space", "freesync"]
            if values["picture_color_temperature"] == "3" {
                required += ["picture_red_gain", "picture_green_gain", "picture_blue_gain"]
            }
            if RegisterMap.isHdrToneMappingPictureMode(values["picture_mode"]) {
                required.append("settings_display_hdr_color_tone")
            }
            let missing = required.filter { values[$0] == nil }
            guard missing.isEmpty else {
                throw PresetError("画面参数读取不完整，保留原数据：\(missing.joined(separator: "、"))")
            }
            let validation = PicturePresetPlan.apply(values, source: source) { _ in }
            guard validation.ok else {
                throw PresetError("读取到无效画面参数，保留原数据：\(validation.failed.joined(separator: "、"))")
            }
            return values
        }
    }

    func apply(_ values: [String: String]) throws -> PresetApplyReport {
        try adb.transaction {
            let source = try source()
            var report = PicturePresetPlan.apply(values, source: source) { command in
                let shell: String
                let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
                switch command {
                case .setting(let key, let value): shell = "settings put global \(key) \(value)"
                case .jni(let key, let value, let update):
                    shell = adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "set", key, value, String(update)])
                case .colorGains(let r, let g, let b):
                    shell = adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setColorGains", r, g, b])
                case .hdrToneMapping(let value):
                    shell = adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setHdrToneMapping", value, "3"])
                case .refresh:
                    shell = "am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7"
                }
                try adb.checkedShell(shell, target: target)
            }
            // TvService can return a successful Parcel despite a JNI failure. Verify actual values.
            Thread.sleep(forTimeInterval: 1.8)
            do {
                let actual = try capture()
                for (label, keys) in Self.verificationKeys where report.applied.contains(label) {
                    let mismatches = keys.filter { key in
                        guard let expected = values[key] else { return false }
                        if key == "picture_mode", let e = Int(expected), let a = Int(actual[key] ?? ""),
                           RegisterMap.pictureModeGroups[e]?.contains(a) == true { return false }
                        return actual[key] != expected
                    }
                    if !mismatches.isEmpty {
                        report.applied.removeAll { $0 == label }
                        report.failed.append("\(label): 回读与目标值不一致（\(mismatches.joined(separator: "、"))）")
                    }
                }
            } catch { report.failed.append("应用后回读失败：\(error.localizedDescription)") }
            return report
        }
    }

    private static let verificationKeys: [(String, [String])] = [
        ("FreeSync", ["freesync"]), ("画面模式", ["picture_mode"]),
        ("自动调整亮度", ["tv_picture_light_sensor"]), ("背光", ["picture_backlight"]),
        ("黑色级别", ["picture_brightness"]), ("对比度", ["picture_contrast"]),
        ("饱和度", ["picture_saturation"]), ("色调", ["picture_hue"]), ("锐度", ["picture_sharpness"]),
        ("色温", ["picture_color_temperature"]),
        ("色增益", ["picture_red_gain", "picture_green_gain", "picture_blue_gain"]),
        ("精密控光", ["picture_local_dimming"]), ("HDR 色调映射", ["settings_display_hdr_color_tone"]),
        ("动态清晰度", ["picture_dynamic_definition"]), ("灰阶响应时间", ["picture_response_time"]),
        ("色域", ["tv_picture_advanced_video_color_space"]),
    ]
}
