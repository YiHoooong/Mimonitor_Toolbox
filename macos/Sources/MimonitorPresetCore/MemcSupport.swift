import Foundation

/// Evaluation outcome when attempting to enable MEMC (level > 0).
public enum MemcConditionOutcome: Equatable {
    /// Picture mode itself forbids MEMC (e.g. Standard / PC presets); block write.
    case disallowedMode
    /// Mode supports MEMC, but at least one software-detectable runtime condition is unmet; write & warn.
    case unmetRuntimeConditions
    /// Mode and all software-detectable conditions are satisfied; write without blocking popup.
    case ready
}

/// Single row in the MEMC condition check dialog.
public struct MemcConditionItem: Equatable {
    public enum Status: Equatable {
        case satisfied
        case unsatisfied
        case info
    }

    public let title: String
    public let detail: String
    public let status: Status

    public init(title: String, detail: String, status: Status) {
        self.title = title
        self.detail = detail
        self.status = status
    }

    public var symbol: String {
        switch status {
        case .satisfied: return "✓"
        case .unsatisfied: return "✗"
        case .info: return "ℹ"
        }
    }

    public var formattedLine: String {
        "\(symbol) \(title)：\(detail)"
    }
}

/// Result of evaluating MEMC runtime enablement conditions.
public struct MemcConditionReport: Equatable {
    public let pictureMode: Int?
    public let modeName: String
    public let outcome: MemcConditionOutcome
    public let items: [MemcConditionItem]

    public var modeSupported: Bool { outcome != .disallowedMode }
    public var softwareConditionsSatisfied: Bool { outcome == .ready }
    public var checklistText: String { items.map(\.formattedLine).joined(separator: "\n") }
}

/// UI prompt payload when MEMC enablement is blocked (Case A) or saved with unmet conditions (Case B).
public struct MemcPromptState: Equatable {
    public let title: String
    public let message: String
    public let canSwitchToMovie: Bool
    public let pendingLevel: Int

    public init?(report: MemcConditionReport, level: Int) {
        let levelText = MemcSupport.levelLabel(level)
        switch report.outcome {
        case .disallowedMode:
            self.title = "当前画面模式不支持开启运动补偿"
            self.message = """
            当前处于「\(report.modeName)」，系统在该模式下禁用运动补偿（MEMC）。请先切换至「电影模式」或「游戏模式」后再开启。

            条件检测清单：
            \(report.checklistText)
            """
            self.canSwitchToMovie = true
            self.pendingLevel = level
        case .unmetRuntimeConditions:
            self.title = "已保存「\(levelText)」档位，部分条件未满足"
            self.message = """
            运动补偿档位已保存，但以下条件未满足，当前暂不生效；关闭冲突项并确保输入信号 ≤ 120Hz 后将自动生效：

            条件检测清单：
            \(report.checklistText)
            """
            self.canSwitchToMovie = false
            self.pendingLevel = level
        case .ready:
            return nil
        }
    }
}

/// Shared constants, write sequences, and runtime condition checks for MEMC (运动补偿).
public enum MemcSupport {
    public static let primarySettingKey = "tv_picture_advance_video_mjc_effect"
    public static let gameSettingKey = "tv_picture_game_mode_with_memc_switch"
    public static let gamePrefSettingKey = "tv_picture_advanced_settings_game_mode_with_memc"
    public static let saveMemcSettingKey = "com.xiaomi.mitv.settings.SAVE_MEMC_VALUE"

    public static let normalJniKey = "g_video__vid_mjc_effect"
    public static let gameJniKey = "g_video__vid_game_mode_with_memc_switch"

    public static let levelNames: [Int: String] = [
        0: "关",
        1: "低",
        2: "中",
        3: "高",
        4: "自定义",
    ]

    public static func levelLabel(_ value: Int) -> String {
        levelNames[value] ?? "\(value)"
    }

    /// Picture modes where OSD/middleware disables MEMC (MTK + Xiaomi enums).
    public static let disallowedPictureModes: Set<Int> = [
        2, 14, 18, 22,
        30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
        69, 70, 71, 72, 73,
    ]

    /// Picture modes routed through the game-MEMC JNI/settings path (includes MTK 64..68, 13, 16).
    public static let gamePictureModes: Set<Int> = [
        4, 10, 13, 15, 16, 19,
        25, 26, 27, 28, 29,
        64, 65, 66, 67, 68,
    ]

    public static let modeNames: [Int: String] = [
        2: "标准",
        4: "游戏",
        5: "Dolby Vision IQ",
        6: "Dolby Vision 柔和",
        9: "电影",
        10: "游戏",
        13: "Dolby Vision 游戏",
        14: "标准",
        15: "HDR 游戏",
        16: "HDR 游戏",
        17: "HDR 电影",
        18: "HDR 标准",
        19: "Dolby Vision 游戏",
        22: "HDR 标准",
        25: "SDR 游戏 RPG",
        26: "SDR 游戏 RTS",
        27: "SDR 游戏 MOBA",
        28: "SDR 游戏 SPT",
        29: "HDR 游戏 FPS",
        30: "HDR PC 预设",
        31: "HDR PC 预设",
        32: "HDR PC 预设",
        33: "HDR PC 预设",
        34: "HDR PC 预设",
        35: "SDR PC DCI-P3",
        36: "SDR PC CG",
        37: "SDR PC 暗房",
        38: "SDR PC sRGB",
        39: "HDR PC AdobeRGB",
        40: "HDR PC DCI-P3",
        41: "HDR PC CG",
        42: "HDR PC 暗房",
        43: "HDR PC sRGB",
        59: "Dolby Vision 明亮",
        64: "游戏预设",
        65: "游戏预设",
        66: "游戏预设",
        67: "游戏预设",
        68: "游戏预设",
        69: "SDR PC sRGB",
        70: "SDR PC AdobeRGB",
        71: "SDR PC DCI-P3",
        72: "SDR PC CG",
        73: "SDR PC 暗房",
    ]

    public static func isGameMode(_ mode: Int?) -> Bool {
        guard let mode else { return false }
        return gamePictureModes.contains(mode)
    }

    public static func isDisallowedMode(_ mode: Int?) -> Bool {
        guard let mode else { return false }
        return disallowedPictureModes.contains(mode)
    }

    public static func jniKey(forPictureMode mode: Int?) -> String {
        isGameMode(mode) ? gameJniKey : normalJniKey
    }

    public static func modeDisplayName(_ mode: Int?) -> String {
        guard let mode else { return "未知模式" }
        return modeNames[mode] ?? "模式 \(mode)"
    }

    public static func writeCommands(value: Int, pictureMode mode: Int?) -> [PresetCommand] {
        let text = String(value)
        if isGameMode(mode) {
            return [
                .jni(gameJniKey, text, 3),
                .setting(gameSettingKey, text),
                .setting(gamePrefSettingKey, text),
                .setting(saveMemcSettingKey, text),
                .refresh,
            ]
        }
        return [
            .jni(normalJniKey, text, 3),
            .setting(primarySettingKey, text),
            .setting(saveMemcSettingKey, text),
            .refresh,
        ]
    }

    public static func parseInt(_ raw: String?) -> Int? {
        guard let raw else { return nil }
        return Int(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Evaluates MEMC enablement conditions from fresh device JNI + settings maps.
    public static func evaluate(
        settings: [String: String],
        jni: [String: String],
        hostRefreshHz: Int? = nil
    ) -> MemcConditionReport {
        let mode = parseInt(jni["g_video__picture_mode"])
            ?? parseInt(settings["picture_mode"])
            ?? parseInt(settings["picture_preset_scenario"])
        let modeName = modeDisplayName(mode)
        let modeSupported = mode != nil && !isDisallowedMode(mode)

        let source = (settings["mitv.tvplayer.hdmi.last.source"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let isDP = source == "29" || source == "30"

        let hdmiEdid = parseInt(jni["g_fusion_picture__hdmi_edid_version"])
        let dpEdid = parseInt(jni["g_fusion_picture__dp_edid_version"])

        // Condition 2: FreeSync off (only read the source-appropriate register)
        let freesyncActive: Bool
        let mode320Active: Bool
        if isDP {
            let dpFs = parseInt(jni["g_video__dp_adaptive_sync"])
            freesyncActive = dpFs.map { $0 == 1 } ?? (parseInt(settings["freesync"]) == 1)
            mode320Active = dpEdid.map { $0 == 3 } ?? (parseInt(settings["mode_320"]) == 1)
        } else {
            let hdmiFs = parseInt(jni["g_video__freesync_switch"])
            let fsFlag = hdmiFs.map { $0 == 3 } ?? (parseInt(settings["freesync"]) == 1)
            let hdmiEdidFreesync = hdmiEdid.map { $0 == 3 || $0 == 5 } ?? false
            freesyncActive = fsFlag || hdmiEdidFreesync
            mode320Active = hdmiEdid.map { $0 == 5 || $0 == 6 } ?? (parseInt(settings["mode_320"]) == 1)
        }

        // Condition 3: 320Hz / DLG off
        let dlgActive = parseInt(jni["g_video__dlg_vrr_switch"]) == 1
        let highRefreshConflict = mode320Active || dlgActive

        let highRefreshDetail: String
        if mode320Active && dlgActive {
            highRefreshDetail = "320Hz 模式与 DLG 高帧率已开启，需关闭"
        } else if mode320Active {
            highRefreshDetail = "320Hz 模式已开启，需关闭"
        } else if dlgActive {
            highRefreshDetail = "DLG 高帧率已开启，需关闭"
        } else {
            highRefreshDetail = "已关闭"
        }

        let refreshItem: MemcConditionItem
        let highRefreshRateActive: Bool
        if let hz = hostRefreshHz, hz > 0 {
            if hz > 120 {
                highRefreshRateActive = true
                refreshItem = MemcConditionItem(
                    title: "输入信号刷新率 ≤ 120Hz",
                    detail: "系统读取当前约 \(hz)Hz（> 120Hz；获取值仅供参考，多信号源或 VRR 下可能不准，请调至 ≤ 120Hz）",
                    status: .unsatisfied
                )
            } else {
                highRefreshRateActive = false
                refreshItem = MemcConditionItem(
                    title: "输入信号刷新率 ≤ 120Hz",
                    detail: "系统读取当前约 \(hz)Hz（获取值仅供参考，多信号源或 VRR 下实际以显示器信号为准）",
                    status: .satisfied
                )
            }
        } else {
            highRefreshRateActive = false
            refreshItem = MemcConditionItem(
                title: "输入信号刷新率 ≤ 120Hz",
                detail: "未能读取当前显示器刷新率，请确认输入信号为 120Hz 及以下",
                status: .info
            )
        }

        var items: [MemcConditionItem] = [
            MemcConditionItem(
                title: "画面模式支持",
                detail: modeSupported ? "当前为「\(modeName)」" : "当前为「\(modeName)」（需电影 / 游戏等非标准模式）",
                status: modeSupported ? .satisfied : .unsatisfied
            ),
            MemcConditionItem(
                title: "FreeSync 已关闭",
                detail: freesyncActive ? "当前已开启，需关闭" : "已关闭",
                status: freesyncActive ? .unsatisfied : .satisfied
            ),
            MemcConditionItem(
                title: "320Hz / DLG 模式已关闭",
                detail: highRefreshDetail,
                status: highRefreshConflict ? .unsatisfied : .satisfied
            ),
            refreshItem,
        ]

        // Condition 5: Content adaptive off (only applicable to mode 5 / DV IQ)
        var contentAdaptiveActive = false
        if mode == 5 {
            let ca = parseInt(jni["g_video__vid_content_adaptive"]) ?? parseInt(settings["picture_content_adaptive"])
            contentAdaptiveActive = (ca == 1)
            items.append(MemcConditionItem(
                title: "内容自适应已关闭",
                detail: contentAdaptiveActive ? "当前已开启，需关闭" : "已关闭",
                status: contentAdaptiveActive ? .unsatisfied : .satisfied
            ))
        }

        let outcome: MemcConditionOutcome
        if !modeSupported {
            outcome = .disallowedMode
        } else if freesyncActive || highRefreshConflict || highRefreshRateActive || contentAdaptiveActive {
            outcome = .unmetRuntimeConditions
        } else {
            outcome = .ready
        }

        return MemcConditionReport(pictureMode: mode, modeName: modeName, outcome: outcome, items: items)
    }
}
