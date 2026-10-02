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

    /// 只有这些画面模式下才存在 HDR 色调映射这个选项（移植自 core.py）。
    /// 标准/电影/游戏等 SDR 模式下该项无意义，原版会直接隐藏控件。
    static let hdrToneMappingPictureModes: Set<Int> = [
        11, 12, 13, 15, 16, 17, 18, 19,
        22, 23,
        29, 30, 31, 32, 33,
        39, 40, 41, 42, 43, 44,
    ]

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
    /// 游戏画面模式集合（用于判断是否处于游戏模式）
    /// 模式分组（移植自 core.py PICTURE_MODE_GROUPS）：同一组内的模式在 UI 上算同一类。
    static let pictureModeGroups: [Int: Set<Int>] = [
        14: [14, 64, 65, 66, 67, 68],
        10: [10, 25, 26, 27, 28, 29],
        9: [9],
    ]

    /// 组名（移植自 _picture_mode_group_name）
    static func pictureModeGroupName(_ mode: Int) -> String? {
        for (primary, name) in [(14, "标准"), (10, "游戏"), (9, "电影")] {
            if pictureModeGroups[primary]?.contains(mode) ?? false { return name }
        }
        return nil
    }

    /// GAME_PICTURE_MODES = 组10 ∪ {4, 15, 19}
    static let gamePictureModes: Set<Int> = [10, 25, 26, 27, 28, 29, 4, 15, 19]

    /// 移植自 is_game_picture_mode：容忍 nil / 空串 / 非法值
    static func isGamePictureMode(_ value: String?) -> Bool {
        guard let value,
              let n = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return gamePictureModes.contains(n)
    }

    /// 完整场景名称表（移植自 core.py PICTURE_SCENE_NAMES）
    static let sceneNames: [Int: String] = [
        9: "电影", 10: "游戏", 11: "Dolby Vision 明亮", 12: "Dolby Vision 暗场",
        13: "Dolby Vision 自定义", 14: "标准", 15: "HDR 游戏", 16: "HDR 图片",
        17: "HDR 电影", 18: "Dolby Vision IQ", 19: "Dolby Vision 游戏",
        21: "Filmmaker", 22: "HDR 显示器", 23: "HDR Filmmaker", 24: "SDR 游戏 FPS",
        25: "SDR 游戏 RPG", 26: "SDR 游戏 RTS", 27: "SDR 游戏 MOBA", 28: "SDR 游戏 SPT",
        29: "HDR 游戏 FPS", 30: "HDR 游戏 RPG", 31: "HDR 游戏 RTS", 32: "HDR 游戏 MOBA",
        33: "HDR 游戏 SPT", 34: "SDR PC AdobeRGB", 35: "SDR PC DCI-P3", 36: "SDR PC CG",
        37: "SDR PC 暗房", 38: "SDR PC sRGB", 39: "HDR PC AdobeRGB", 40: "HDR PC DCI-P3",
        41: "HDR PC CG", 42: "HDR PC 暗房", 43: "HDR PC sRGB", 44: "HDR Vivid",
        64: "标准预设", 65: "标准预设", 66: "标准预设", 67: "标准预设", 68: "标准预设",
    ]

    static let lightModeNames: [Int: String] = [4: "关闭", 0: "照明", 2: "纯色", 1: "屏幕同色", 3: "七彩梦境"]

    /// OpenSound 之类的外部枚举统一用这几个现成的表
    static let gamutNames: [Int: String] = [0: "自动", 3: "sRGB", 4: "Adobe RGB",
                                            5: "BT2020", 6: "DCI-P3", 7: "BT709"]
    static let responseTimeNames: [Int: String] = [1: "普通", 2: "快速", 3: "高速"]
    static let dynamicDefinitionNames: [Int: String] = [0: "关", 1: "低", 2: "中", 3: "高"]
    static let localDimmingNames: [Int: String] = [0: "关", 1: "低", 2: "中", 3: "高"]
    static let hdrToneNames: [Int: String] = [0: "HGiG", 1: "层次", 2: "动态", 3: "明亮"]
    static let lightColorTempNames: [Int: String] = [0: "2700K", 1: "4000K", 2: "6500K"]
    static let lightColorNames: [Int: String] = [0: "冰蓝", 1: "流金", 2: "天青", 3: "草地", 4: "日落"]
}

// MARK: - 页面数据 key（移植自 device_features.py _page_data_keys）

enum PageDataKeys {
    static let pictureSettings = [
        "picture_mode", "picture_backlight", "xiaomi_picture_backlight",
        "picture_preset_scenario", "picture_brightness", "picture_contrast",
        "picture_saturation", "picture_hue", "picture_sharpness",
        "picture_color_temperature", "picture_red_gain", "picture_green_gain",
        "picture_blue_gain", "picture_local_dimming",
        "tv_picture_video_local_dimming", "picture_hdr_tone_mapping",
        "settings_display_hdr_color_tone", "picture_dynamic_definition",
        "picture_response_time", "tv_picture_advanced_video_color_space",
        "tv_picture_video_color_space", "tv_picture_light_sensor",
    ]
    static let pictureJni = [
        "g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode",
        "g_video__clr_temp", "g_video__vid_local_dimming",
        "g_video__vid_hdr_tone_mapping_mode",
        // 原版通过 query_setting_or_jni 单独读它；放进同一批里一起拿更省一次往返
        "g_video__vid_od_response_time",
        // 自动调整亮度（光感）：菜单读的是 MTK 侧，回读后覆盖 settings 值
        "g_video__light_sensor_switch",
    ]
    static let gameSettings = [
        "picture_mode", "picture_preset_scenario", "front_sight_index",
        "mt_game_dynamic_ft", "mt_game_scope", "mt_game_scope_night",
        "monitor_menu_fps_counter", "monitor_menu_stopwatch", "monitor_menu_timer",
        "mitv.tvplayer.hdmi.last.source",
    ]
    static let sourceSettings = ["mitv.tvplayer.hdmi.last.source"]
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
        case .source: return "信号源切换"
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
