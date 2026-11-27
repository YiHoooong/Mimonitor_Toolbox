import Foundation
import MimonitorPresetCore

// MARK: - 寄存器映射（移植自 core.py）

enum RegisterMap {
    /// 小米色温 UI 值 -> MTK JNI 值
    static let colorTempToMtk = PicturePresetPlan.colorTempToMtk
    /// MTK 值 -> 小米 settings 枚举值（反查表，用于「JNI 覆盖 settings」）
    static let mtkToColorTemp: [Int: Int] = [1: 0, 2: 1, 3: 2, 0: 3, 4: 4, 5: 5, 6: 8]

    /// HDR 色调映射 UI 值 -> MTK 值
    static let hdrToneMappingUIToMtk = PicturePresetPlan.hdrToneMappingToMtk

    /// MTK 值 -> UI 值（反查表）
    static let hdrToneMappingMtkToUI: [Int: Int] = [5: 0, 0: 1, 2: 2, 1: 3]

    /// 只有这些画面模式下才存在 HDR 色调映射这个选项（移植自 core.py MTK 枚举）。
    /// 标准/电影/游戏等 SDR 模式下该项无意义，原版会直接隐藏控件。
    static let hdrToneMappingPictureModes: Set<Int> = PicturePresetPlan.hdrModes

    static func isHdrToneMappingPictureMode(_ value: String?) -> Bool {
        guard let value,
              let n = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return hdrToneMappingPictureModes.contains(n)
    }

    /// 语义别名：上面那个集合实际上就是「HDR 画面模式」的判定依据
    /// （标准/电影/游戏等 SDR 模式都不在其中）。用于 HDR/SDR 记忆分桶。
    static func isHDRPictureMode(_ value: String?) -> Bool {
        isHdrToneMappingPictureMode(value)
    }
    /// 画面模式：主模式 id -> 名称
    static let modeNames: [Int: String] = [14: "标准", 10: "游戏", 9: "电影"]
    /// 输入源 id -> 名称
    static let sourceNames: [Int: String] = [23: "HDMI 1", 24: "HDMI 2", 29: "DP", 30: "USB-C"]
    /// 声音输出设备 id -> 名称（3 为历史 ARC 值，统一按 eARC 展示）
    static let soundOutputNames: [Int: String] = [0: "本机扬声器", 8: "HDMI(eARC)", 5: "耳机孔", 3: "HDMI(eARC)"]
    static let soundOutputKey = PicturePresetPlan.soundOutputKey
    static func normalizeSoundOutput(_ value: Int) -> Int? {
        PicturePresetPlan.normalizeSoundOutput(value)
    }
    static func normalizeSoundOutput(_ raw: String) -> Int? {
        PicturePresetPlan.normalizeSoundOutput(raw)
    }
    /// 游戏画面模式集合（用于判断是否处于游戏模式）
    /// 模式分组（移植自 core.py PICTURE_MODE_GROUPS，对齐固件 MTK 枚举）：同一组内的模式在 UI 上算同一类。
    static let pictureModeGroups: [Int: Set<Int>] = [
        14: [14, 69, 70, 71, 72, 73, 18, 30, 31, 32, 33, 34],
        10: [10, 64, 65, 66, 67, 68, 16, 25, 26, 27, 28, 29, 13],
        9: [9, 17],
    ]

    static let sdrPrimaryPictureModes: [Int: Int] = [14: 14, 10: 10, 9: 9]
    static let hdrPrimaryPictureModes: [Int: Int] = [14: 18, 10: 16, 9: 17]

    /// 归并到 UI 三个主按钮键（14 标准 / 10 游戏 / 9 电影），不匹配返回 nil
    static func primaryPictureMode(for mode: Int) -> Int? {
        for primary in [14, 10, 9] {
            if pictureModeGroups[primary]?.contains(mode) ?? false { return primary }
        }
        return nil
    }

    /// 将 UI 主按钮（14/10/9）解析为当前 SDR/HDR 信号池对应的 MTK 模式值
    static func resolveTargetPictureMode(_ primary: Int, isHDR: Bool) -> Int {
        let table = isHDR ? hdrPrimaryPictureModes : sdrPrimaryPictureModes
        return table[primary] ?? primary
    }

    /// 是否处于「仅关闭画面 / 音响模式」（MTK g_audio__audio only / power_picture_off == 0，亮屏为 1）
    static func isScreenOffOnline(_ values: [String: String]) -> Bool {
        values["power_picture_off"]?.trimmingCharacters(in: .whitespacesAndNewlines) == "0"
    }

    /// 组名（移植自 _picture_mode_group_name）
    static func pictureModeGroupName(_ mode: Int) -> String? {
        guard let primary = primaryPictureMode(for: mode) else { return nil }
        return modeNames[primary]
    }

    /// GAME_PICTURE_MODES = 组10（含 SDR/HDR 游戏子模式与 Dolby Vision 游戏）
    static let gamePictureModes: Set<Int> = pictureModeGroups[10]!

    /// 移植自 is_game_picture_mode：容忍 nil / 空串 / 非法值
    static func isGamePictureMode(_ value: String?) -> Bool {
        guard let value,
              let n = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return gamePictureModes.contains(n)
    }

    /// 完整 MTK 场景名称表（移植自 core.py PICTURE_SCENE_NAMES，对齐固件 PictureSettingsManagerImpl）
    static let sceneNames: [Int: String] = [
        0: "自定义", 2: "体育", 3: "图片", 4: "Dolby Vision Filmmaker",
        5: "Dolby Vision IQ", 6: "Dolby Vision 暗场", 9: "电影", 10: "游戏",
        13: "Dolby Vision 游戏", 14: "标准", 15: "HDR", 16: "HDR 游戏",
        17: "HDR 电影", 18: "HDR 标准", 19: "HDR 图片", 22: "IMAX",
        25: "HDR 游戏 FPS", 26: "HDR 游戏 RPG", 27: "HDR 游戏 RTS",
        28: "HDR 游戏 MOBA", 29: "HDR 游戏 SPT",
        30: "HDR PC AdobeRGB", 31: "HDR PC DCI-P3", 32: "HDR PC CG",
        33: "HDR PC 暗房", 34: "HDR PC sRGB", 35: "HDR10+",
        55: "HLG", 59: "Dolby Vision 明亮", 61: "Dolby Vision 自定义",
        64: "SDR 游戏 FPS", 65: "SDR 游戏 RPG", 66: "SDR 游戏 RTS",
        67: "SDR 游戏 MOBA", 68: "SDR 游戏 SPT",
        69: "SDR PC AdobeRGB", 70: "SDR PC DCI-P3", 71: "SDR PC CG",
        72: "SDR PC 暗房", 73: "SDR PC sRGB",
        75: "Filmmaker", 76: "HDR Filmmaker",
    ]

    static let lightModeNames: [Int: String] = [4: "关闭", 0: "照明", 2: "纯色", 1: "屏幕同色", 3: "七彩梦境"]

    /// OpenSound 之类的外部枚举统一用这几个现成的表
    static let gamutNames: [Int: String] = [0: "自动", 3: "sRGB", 4: "Adobe RGB",
                                            5: "BT2020", 6: "DCI-P3", 7: "BT709"]
    static let responseTimeNames: [Int: String] = [1: "普通", 2: "快速", 3: "高速"]
    static let dynamicDefinitionNames: [Int: String] = [0: "关", 1: "低", 2: "中", 3: "高"]
    static let memcNames: [Int: String] = MemcSupport.levelNames
    static let localDimmingNames: [Int: String] = [0: "关", 1: "低", 2: "中", 3: "高"]
    static let hdrToneNames: [Int: String] = [0: "HGiG", 1: "层次", 2: "动态", 3: "明亮"]
    static let lightColorTempNames: [Int: String] = [0: "2700K", 1: "4000K", 2: "6500K"]
    static let lightColorNames: [Int: String] = [0: "冰蓝", 1: "流金", 2: "天青", 3: "草地", 4: "日落"]
}

// MARK: - 页面数据 key（移植自 device_features.py _page_data_keys）

enum PageDataKeys {
    static let pictureSettings = [
        "picture_mode", "picture_backlight", "xiaomi_picture_backlight",
        "picture_preset_scenario", "power_picture_off",
        "picture_brightness", "picture_contrast",
        "picture_saturation", "picture_hue", "picture_sharpness",
        "picture_color_temperature", "picture_red_gain", "picture_green_gain",
        "picture_blue_gain", "picture_local_dimming",
        "tv_picture_video_local_dimming", "picture_hdr_tone_mapping",
        "settings_display_hdr_color_tone", "picture_dynamic_definition",
        MemcSupport.primarySettingKey, MemcSupport.gameSettingKey,
        MemcSupport.gamePrefSettingKey, MemcSupport.saveMemcSettingKey,
        "picture_response_time", "tv_picture_advanced_video_color_space",
        "tv_picture_video_color_space", "tv_picture_light_sensor",
        "mitv.tvplayer.hdmi.last.source", "picture_content_adaptive",
    ]
    static let pictureJni = [
        "g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode",
        "g_video__clr_temp", "g_video__vid_local_dimming",
        "g_video__vid_hdr_tone_mapping_mode",
        // 原版通过 query_setting_or_jni 单独读它；放进同一批里一起拿更省一次往返
        "g_video__vid_od_response_time",
        // 自动调整亮度（光感）：菜单读的是 MTK 侧，回读后覆盖 settings 值
        "g_video__light_sensor_switch",
        MemcSupport.normalJniKey, MemcSupport.gameJniKey,
        "g_video__picture_mode", "g_video__freesync_switch", "g_video__dp_adaptive_sync",
        "g_fusion_picture__hdmi_edid_version", "g_fusion_picture__dp_edid_version",
        "g_video__dlg_vrr_switch", "g_video__vid_content_adaptive",
    ]
    static let gameSettings = [
        "picture_mode", "picture_preset_scenario", "power_picture_off", "front_sight_index",
        "mt_game_dynamic_ft", "mt_game_scope", "mt_game_scope_night",
        "monitor_menu_fps_counter", "monitor_menu_stopwatch", "monitor_menu_timer",
        "mitv.tvplayer.hdmi.last.source",
    ]
    static let sourceSettings = ["mitv.tvplayer.hdmi.last.source", RegisterMap.soundOutputKey]
    static let lightSettings = [
        "atmosphere_light_switcher_pm2", "atmosphere_light_illumination",
        "atmosphere_light_color_temp", "atmosphere_light_color_value",
    ]
}

// MARK: - 通用选项

struct Option: Identifiable, Hashable {
    let label: String
    let value: Int
    var id: Int { value }
}

enum OptionLists {
    static let colorTemp: [Option] = [
        .init(label: "冷色", value: 0), .init(label: "标准", value: 1),
        .init(label: "暖色", value: 2), .init(label: "原色", value: 8),
        .init(label: "自定义", value: 3),
    ]
    static let localDimming: [Option] = [
        .init(label: "关", value: 0), .init(label: "低", value: 1),
        .init(label: "中", value: 2), .init(label: "高", value: 3),
    ]
    static let hdrToneMapping: [Option] = [
        .init(label: "HGiG", value: 0), .init(label: "层次", value: 1),
        .init(label: "动态", value: 2), .init(label: "明亮", value: 3),
    ]
    static let dynamicDefinition: [Option] = [
        .init(label: "关", value: 0), .init(label: "低", value: 1),
        .init(label: "中", value: 2), .init(label: "高", value: 3),
    ]
    static let memc: [Option] = [0, 1, 2, 3].map {
        .init(label: MemcSupport.levelLabel($0), value: $0)
    }
    static let responseTime: [Option] = [
        .init(label: "普通", value: 1), .init(label: "快速", value: 2), .init(label: "高速", value: 3),
    ]
    static let gamut: [Option] = [
        .init(label: "自动", value: 0), .init(label: "sRGB", value: 3), .init(label: "DCI-P3", value: 6),
        .init(label: "AdobeRGB", value: 4), .init(label: "BT2020", value: 5), .init(label: "BT709", value: 7),
    ]
    static let crosshair: [Option] = [
        .init(label: "关", value: 0), .init(label: "1", value: 1), .init(label: "2", value: 2),
        .init(label: "3", value: 3), .init(label: "4", value: 4), .init(label: "5", value: 5),
    ]
    static let dynamicCrosshair: [Option] = [.init(label: "关", value: 0), .init(label: "开", value: 1)]
    static let scope: [Option] = [
        .init(label: "关", value: 0), .init(label: "1.1x", value: 1), .init(label: "1.3x", value: 3),
        .init(label: "1.5x", value: 5), .init(label: "1.7x", value: 7), .init(label: "2.0x", value: 10),
    ]
    static let scopeNight: [Option] = [.init(label: "关", value: 0), .init(label: "开", value: 1)]
    static let mode320: [Option] = [.init(label: "关", value: 0), .init(label: "开", value: 1)]
    static let freesync: [Option] = [.init(label: "关", value: 0), .init(label: "开", value: 1)]
    static let fpsCounter: [Option] = [
        .init(label: "关", value: 0), .init(label: "刷新率", value: 1), .init(label: "柱状图", value: 2),
    ]
    static let stopwatch: [Option] = [.init(label: "关", value: 0), .init(label: "开", value: 1)]
    static let timer: [Option] = [
        .init(label: "关", value: 0), .init(label: "1分钟", value: 60), .init(label: "5分钟", value: 300),
        .init(label: "30分钟", value: 1800), .init(label: "60分钟", value: 3600),
    ]
    static let source: [Option] = [
        .init(label: "HDMI 1", value: 23), .init(label: "HDMI 2", value: 24),
        .init(label: "DP", value: 29), .init(label: "USB-C", value: 30),
    ]
    static let soundOutput: [Option] = [
        .init(label: "本机扬声器", value: 0),
        .init(label: "HDMI(eARC)", value: 8),
        .init(label: "耳机孔", value: 5),
    ]
    static let lightMode: [Option] = [
        .init(label: "关闭", value: 4), .init(label: "照明", value: 0), .init(label: "纯色", value: 2),
        .init(label: "屏幕同色", value: 1), .init(label: "七彩梦境", value: 3),
    ]
    static let lightColorTemp: [Option] = [
        .init(label: "2700K", value: 0), .init(label: "4000K", value: 1), .init(label: "6500K", value: 2),
    ]
    static let lightColor: [Option] = [
        .init(label: "冰蓝", value: 0), .init(label: "流金", value: 1), .init(label: "天青", value: 2),
        .init(label: "草地", value: 3), .init(label: "日落", value: 4),
    ]
}

// MARK: - 侧边栏页面

enum Page: String, CaseIterable, Identifiable, Hashable {
    case home, picture, game, source, light, menuBar, presets, autoTasks, tools, remote

    var id: String { rawValue }

    var title: String {
        switch self {
        case .home: return "主页 & 连接"
        case .picture: return "画面设置"
        case .presets: return "预设模式"
        case .autoTasks: return "自动任务"
        case .game: return "游戏模式"
        case .source: return "信号源与音频"
        case .light: return "屏幕灯"
        // macOS 独有的一页：Windows 原版没有菜单栏这个概念（那边是托盘图标）
        case .menuBar: return "菜单栏"
        case .tools: return "工具与设置"
        case .remote: return "遥控器"
        }
    }

    var icon: String {
        switch self {
        case .home: return "house"
        case .picture: return "paintpalette"
        case .presets: return "square.stack.3d.up"
        case .autoTasks: return "calendar.badge.clock"
        case .game: return "gamecontroller"
        case .source: return "arrow.triangle.2.circlepath"
        case .light: return "lightbulb"
        case .menuBar: return "menubar.rectangle"
        case .tools: return "wrench.and.screwdriver"
        case .remote: return "tv"
        }
    }
}

// MARK: - 连接模式（混合加速 Daemon / 标准 ADB）

enum ConnectionMode: String, CaseIterable, Identifiable, Equatable {
    case adb
    case daemon

    static let defaultMode: ConnectionMode = .adb
    static let userDefaultsKey = "connection_mode"
    static let mtkDaemonPort = 27891

    var id: String { rawValue }

    var label: String {
        switch self {
        case .adb: return "标准 ADB（兼容模式）"
        case .daemon: return "混合加速（Daemon 常驻）"
        }
    }

    var shortLabel: String {
        switch self {
        case .adb: return "兼容模式"
        case .daemon: return "混合加速"
        }
    }

    var descriptionText: String {
        switch self {
        case .adb:
            return "每次读写通过标准 adb shell 执行，无需在显示器后台驻留进程，兼容性最好。"
        case .daemon:
            return "在显示器上常驻轻量级 MtkDirectTool 守护进程并通过 adb forward 本机端口通信，批量读写与页面刷新可显著提速；不可用时自动回退到标准 ADB。"
        }
    }

    static func normalize(_ raw: String?) -> ConnectionMode {
        let trimmed = (raw ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        switch trimmed {
        case "daemon", "hybrid", "fast":
            return .daemon
        default:
            return .adb
        }
    }

    static func load(from defaults: UserDefaults = .standard) -> ConnectionMode {
        normalize(defaults.string(forKey: userDefaultsKey))
    }

    func save(to defaults: UserDefaults = .standard) {
        defaults.set(rawValue, forKey: Self.userDefaultsKey)
    }
}
