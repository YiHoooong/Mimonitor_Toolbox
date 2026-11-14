import Foundation

/// 菜单栏里可以放的一个快捷项。
///
/// 两种形态：
///   - `options`：一组互斥取值，菜单里以打勾列表呈现（精密控光、色域、画面模式…）
///   - `stepper`：连续数值，菜单里给「增大 / 减小」两项（背光、对比度…）
/// 菜单栏空间有限，所以不把 1~100 这种全量取值铺成列表。
struct MenuBarEntry: Identifiable, Hashable {
    enum Kind: Hashable {
        case options([Option])
        case stepper(min: Int, max: Int, step: Int)
    }

    let id: String
    let label: String
    let kind: Kind

    static func == (l: MenuBarEntry, r: MenuBarEntry) -> Bool { l.id == r.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

/// 可加入菜单栏的条目清单。顺序即为菜单栏里的显示顺序。
enum MenuBarCatalog {
    static let all: [MenuBarEntry] = [
        MenuBarEntry(id: "picture_mode", label: "画面模式", kind: .options([
            Option(label: "标准", value: 14),
            Option(label: "游戏", value: 10),
            Option(label: "电影", value: 9),
        ])),
        MenuBarEntry(id: "local_dimming", label: "精密控光", kind: .options(
            [0, 1, 2, 3].map { Option(label: RegisterMap.localDimmingNames[$0] ?? "\($0)", value: $0) }
        )),
        MenuBarEntry(id: "gamut", label: "色域", kind: .options(
            [0, 3, 6, 4, 5, 7].map { Option(label: RegisterMap.gamutNames[$0] ?? "\($0)", value: $0) }
        )),
        MenuBarEntry(id: "color_temp", label: "色温", kind: .options([
            Option(label: "冷色", value: 0),
            Option(label: "标准", value: 1),
            Option(label: "暖色", value: 2),
            Option(label: "原色", value: 8),
            Option(label: "自定义", value: 3),
        ])),
        MenuBarEntry(id: "response_time", label: "响应时间", kind: .options(
            [1, 2, 3].map { Option(label: RegisterMap.responseTimeNames[$0] ?? "\($0)", value: $0) }
        )),
        MenuBarEntry(id: "dynamic_definition", label: "动态清晰度", kind: .options(
            [0, 1, 2, 3].map { Option(label: RegisterMap.dynamicDefinitionNames[$0] ?? "\($0)", value: $0) }
        )),
        MenuBarEntry(id: "memc", label: "运动补偿", kind: .options(OptionLists.memc)),
        MenuBarEntry(id: "hdr_tone_mapping", label: "HDR 色调映射", kind: .options(
            [0, 1, 2, 3].map { Option(label: RegisterMap.hdrToneNames[$0] ?? "\($0)", value: $0) }
        )),
        MenuBarEntry(id: "source", label: "信号源", kind: .options([
            Option(label: "HDMI 1", value: 23),
            Option(label: "HDMI 2", value: 24),
            Option(label: "DP", value: 29),
            Option(label: "USB-C", value: 30),
        ])),
        MenuBarEntry(id: "sound_output", label: "声音输出", kind: .options(OptionLists.soundOutput)),
        MenuBarEntry(id: "volume", label: "音量", kind: .stepper(min: 0, max: 100, step: 5)),
        MenuBarEntry(id: "freesync", label: "FreeSync", kind: .options([
            Option(label: "关", value: 0),
            Option(label: "开", value: 1),
        ])),
        MenuBarEntry(id: "light_sensor", label: "自动调整亮度", kind: .options([
            Option(label: "关", value: 0),
            Option(label: "开", value: 1),
        ])),
        MenuBarEntry(id: "backlight", label: "背光", kind: .stepper(min: 1, max: 100, step: 5)),
        MenuBarEntry(id: "black_level", label: "黑色级别", kind: .stepper(min: 0, max: 100, step: 5)),
        MenuBarEntry(id: "contrast", label: "对比度", kind: .stepper(min: 0, max: 100, step: 5)),
        MenuBarEntry(id: "saturation", label: "饱和度", kind: .stepper(min: 0, max: 100, step: 5)),
        MenuBarEntry(id: "hue", label: "色调", kind: .stepper(min: 0, max: 100, step: 5)),
        MenuBarEntry(id: "sharpness", label: "锐度", kind: .stepper(min: 0, max: 100, step: 1)),
        MenuBarEntry(id: "light_mode", label: "屏幕灯模式", kind: .options([
            Option(label: "关闭", value: 4),
            Option(label: "照明", value: 0),
            Option(label: "纯色", value: 2),
            Option(label: "屏幕同色", value: 1),
            Option(label: "七彩梦境", value: 3),
        ])),
    ]

    static func entry(_ id: String) -> MenuBarEntry? {
        all.first { $0.id == id }
    }

    /// 默认放哪几项（用户没配置过时）
    static let defaultEnabled = ["picture_mode", "local_dimming", "backlight"]
}
