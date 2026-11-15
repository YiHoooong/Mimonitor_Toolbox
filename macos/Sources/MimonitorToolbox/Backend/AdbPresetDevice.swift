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

    private static let captureSettingKeys: [String] = Array(Set(
        PicturePresetPlan.settingsKeys + [
            MemcSupport.gameSettingKey,
            PicturePresetPlan.soundOutputKey,
            "mitv.tvplayer.hdmi.last.source",
        ]
    )).sorted()

    private static let captureJniKeys: [String] = Array(Set(
        PicturePresetPlan.jniKeys + [
            "g_video__freesync_switch",
            "g_video__dp_adaptive_sync",
            "g_fusion_picture__hdmi_edid_version",
        ]
    )).sorted()

    private func source() throws -> String {
        var source = ""
        if adb.isDaemonMode && identity == adb.ip,
           let q = try? adb.daemonQuery(settingsKeys: ["mitv.tvplayer.hdmi.last.source"], targetIp: identity, check: true),
           let v = q.settings["mitv.tvplayer.hdmi.last.source"],
           !v.isEmpty, v != "null", v != "N/A" {
            source = v
        } else {
            source = try adb.checkedShell("settings get global mitv.tvplayer.hdmi.last.source", target: target)
        }
        guard ["23", "24", "29", "30"].contains(source) else {
            throw PresetError("无法确定当前输入源，未写入 FreeSync")
        }
        return source
    }

    func capture() throws -> [String: String] {
        try adb.transaction {
            var settings: [String: String] = [:]
            var jni: [String: String] = [:]
            var source = ""
            var daemonUsed = false

            if adb.isDaemonMode && identity == adb.ip,
               let q = try? adb.daemonQuery(
                settingsKeys: Self.captureSettingKeys,
                jniKeys: Self.captureJniKeys,
                targetIp: identity,
                check: false
               ),
               let rawSource = q.settings["mitv.tvplayer.hdmi.last.source"],
               ["23", "24", "29", "30"].contains(rawSource) {
                let fsKey = PicturePresetPlan.freesyncKey(source: rawSource)
                if q.jni[fsKey] != nil && q.jni["g_disp__disp_back_light"] != nil {
                    for (k, v) in q.settings where !v.isEmpty && v != "null" && v != "N/A" {
                        settings[k] = v
                    }
                    for (k, v) in q.jni {
                        jni[k] = String(v)
                    }
                    source = rawSource
                    daemonUsed = true
                }
            }

            if !daemonUsed {
                let output = try adb.checkedShell("settings list global", target: target)
                for line in output.split(separator: "\n") {
                    guard let index = line.firstIndex(of: "=") else { continue }
                    settings[String(line[..<index])] = String(line[line.index(after: index)...])
                }
                source = try self.source()
                let fsKey = PicturePresetPlan.freesyncKey(source: source)
                let extraKeys = fsKey == "g_video__freesync_switch"
                    ? [fsKey, "g_fusion_picture__hdmi_edid_version"]
                    : [fsKey]
                let keys = PicturePresetPlan.jniKeys.filter { key in
                    if key == "g_video__vid_hdr_tone_mapping_mode" {
                        return RegisterMap.isHdrToneMappingPictureMode(settings["picture_mode"])
                    }
                    if ["g_video__clr_gain_r", "g_video__clr_gain_g", "g_video__clr_gain_b"].contains(key) {
                        return settings["picture_color_temperature"] == "3"
                    }
                    return true
                }
                jni = try adb.jniBatchGetChecked(keys: keys + extraKeys, target: target)
                // Settings can lag behind the OSD. If JNI reveals custom temperature, read real gains.
                if jni["g_video__clr_temp"] == "0", !keys.contains("g_video__clr_gain_r") {
                    let gains = try adb.jniBatchGetChecked(keys: ["g_video__clr_gain_r", "g_video__clr_gain_g", "g_video__clr_gain_b"], target: target)
                    jni.merge(gains) { _, fresh in fresh }
                }
            }

            let values = PicturePresetPlan.normalize(settings: settings, jni: jni, source: source)
            var required = ["picture_mode", "tv_picture_light_sensor", "picture_backlight",
                            "picture_brightness", "picture_contrast", "picture_saturation", "picture_hue",
                            "picture_sharpness", "picture_color_temperature", "picture_local_dimming",
                            "picture_dynamic_definition", MemcSupport.primarySettingKey,
                            "picture_response_time",
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

    private func shellCommand(for command: PresetCommand) -> String {
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        switch command {
        case .setting(let key, let value):
            return "settings put global \(AdbClient.shellQuoteIfNeeded(key)) \(AdbClient.shellQuoteIfNeeded(value))"
        case .jni(let key, let value, let update):
            return adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "set", key, value, String(update)])
        case .colorGains(let r, let g, let b):
            return adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setColorGains", r, g, b])
        case .hdrToneMapping(let value):
            return adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setHdrToneMapping", value, "3"])
        case .soundOutput(let value):
            return adb.buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setSoundOutput", value])
        case .refresh:
            return "am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7"
        }
    }

    private func batchOp(for command: PresetCommand) -> DaemonBatchOp? {
        switch command {
        case .setting(let key, let value):
            return .put(key: key, value: value)
        case .jni(let key, let value, let update):
            guard let intVal = Int(value) else { return nil }
            return .jniSet(key: key, value: intVal, upd: update)
        case .colorGains(let r, let g, let b):
            guard let ri = Int(r), let gi = Int(g), let bi = Int(b) else { return nil }
            return .colorGains(red: ri, green: gi, blue: bi)
        case .hdrToneMapping(let value):
            guard let intVal = Int(value) else { return nil }
            return .hdrToneMapping(value: intVal, upd: 3)
        case .soundOutput(let value):
            guard let intVal = Int(value) else { return nil }
            return .soundOutput(deviceId: intVal)
        case .refresh:
            return .refreshPq
        }
    }

    func apply(_ values: [String: String]) throws -> PresetApplyReport {
        try adb.transaction {
            let source = try source()
            var hdmi320: Bool? = nil
            if PicturePresetPlan.freesyncKey(source: source) == "g_video__freesync_switch",
               values["freesync"] != nil {
                let edidMap = try adb.jniBatchGetChecked(keys: ["g_fusion_picture__hdmi_edid_version"], target: target)
                if let edidRaw = edidMap["g_fusion_picture__hdmi_edid_version"] {
                    hdmi320 = PicturePresetPlan.parseHdmiEdidFlags(edidRaw: edidRaw, freesyncRaw: nil).mode320
                }
            }

            var report: PresetApplyReport?
            if adb.isDaemonMode && identity == adb.ip {
                var collected: [PresetCommand] = []
                let planned = PicturePresetPlan.apply(values, source: source, hdmi320: hdmi320) { command in
                    collected.append(command)
                }
                let batchOps = collected.compactMap(batchOp(for:))
                if batchOps.count == collected.count, !batchOps.isEmpty {
                    let coalesced = AdbClient.coalesceBatchOps(batchOps)
                    if let (ok, _, steps) = try? adb.daemonBatch(coalesced, check: false),
                       ok && steps.allSatisfy(\.ok) {
                        report = planned
                    }
                } else if collected.isEmpty {
                    report = planned
                }
            }

            if report == nil {
                report = PicturePresetPlan.apply(values, source: source, hdmi320: hdmi320) { command in
                    try adb.checkedShell(shellCommand(for: command), target: target)
                }
            }

            guard var finalReport = report else {
                return PresetApplyReport(applied: [], failed: ["未生成预设应用计划"])
            }

            // TvService can return a successful Parcel despite a JNI failure. Verify actual values.
            Thread.sleep(forTimeInterval: 1.8)
            do {
                let actual = try capture()
                for (label, keys) in Self.verificationKeys where finalReport.applied.contains(label) {
                    let mismatches = keys.filter { key in
                        guard let expected = values[key] else { return false }
                        if key == "picture_mode", let e = Int(expected), let a = Int(actual[key] ?? ""),
                           RegisterMap.pictureModeGroups[e]?.contains(a) == true { return false }
                        if key == PicturePresetPlan.soundOutputKey,
                           let normalized = PicturePresetPlan.normalizeSoundOutput(expected) {
                            return actual[key] != String(normalized)
                        }
                        return actual[key] != expected
                    }
                    if !mismatches.isEmpty {
                        finalReport.applied.removeAll { $0 == label }
                        finalReport.failed.append("\(label): 回读与目标值不一致（\(mismatches.joined(separator: "、"))）")
                    }
                }
            } catch { finalReport.failed.append("应用后回读失败：\(error.localizedDescription)") }
            return finalReport
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
        ("动态清晰度", ["picture_dynamic_definition"]),
        ("运动补偿", [MemcSupport.primarySettingKey]),
        ("灰阶响应时间", ["picture_response_time"]),
        ("色域", ["tv_picture_advanced_video_color_space"]),
        ("声音输出", [PicturePresetPlan.soundOutputKey]),
    ]
}
