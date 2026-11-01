import Cocoa
import Carbon.HIToolbox
import ApplicationServices

/// NX_SYSDEFINED 事件类型。放在**文件作用域**而不是类型里：
/// `CGEvent.tapCreate` 的回调是 C 函数指针，闭包不能捕获任何上下文 ——
/// 连 `Self.systemDefinedEventType`（动态自身类型）都会报
/// "a C function pointer cannot be formed from a closure that captures dynamic Self type"。
private let nxSystemDefinedEventType: UInt32 = 14

// MARK: - 快捷键配置模型

struct HotkeyConfig: Codable, Equatable {
    var modifier: String = "无"
    var key: String = "无"
    var isEnabled: Bool { modifier != "无" && key != "无" }
}

struct AdjustHotkeyConfig: Codable, Equatable {
    var param: String = "backlight"
    var direction: String = "increase"
    var step: Int = 5
    var modifier: String = "无"
    var key: String = "无"
    var isEnabled: Bool { modifier != "无" && key != "无" }
}

enum HotkeyLists {
    /// 下拉里能选到的修饰键组合。名称必须和 `HotkeyMap.modifierName(for:)`
    /// 生成的一致，否则录入的快捷键在下拉里显示不出来。
    static let modifiers = [
        "无", "Cmd", "Cmd + Shift", "Cmd + Option", "Cmd + Ctrl",
        "Ctrl", "Ctrl + Alt", "Ctrl + Shift",
        "Alt", "Alt + Shift", "Shift",
    ]
}

/// 热键可绑定的全局动作（对应原版 tools 页 actions_list）
let hotkeyActions: [(id: String, label: String)] = [
    ("picture_mode_cycle", "画面模式 循环切换"),
    ("local_dimming_cycle", "精密控光 循环切换"),
    ("local_dimming_toggle_off", "精密控光 开关切换"),
    ("color_space_cycle", "色域 循环切换"),
    ("color_temp_cycle", "色温 循环切换"),
    ("response_time_cycle", "响应时间 循环切换"),
    ("memc_cycle", "运动补偿 循环切换"),
    ("freesync_toggle", "FreeSync 开关切换"),
    ("input_source_hdmi1", "信号源 切至 HDMI 1"),
    ("input_source_hdmi2", "信号源 切至 HDMI 2"),
    ("input_source_dp", "信号源 切至 DP"),
    ("input_source_usbc", "信号源 切至 USB-C"),
    ("sound_output_cycle", "声音输出 循环切换"),
    ("volume_mute_toggle", "静音 开关切换"),
]

/// 可调参数（对应 core.py ADJUSTABLE_HOTKEY_PARAMS）
let adjustableParams: [(id: String, label: String, defaultStep: Int)] = [
    ("backlight", "背光", 5),
    ("black_level", "黑色级别", 5),
    ("contrast", "对比度", 5),
    ("saturation", "饱和度", 5),
    ("hue", "色调", 5),
    ("sharpness", "锐度", 1),
    ("red_gain", "红色增益", 10),
    ("green_gain", "绿色增益", 10),
    ("blue_gain", "蓝色增益", 10),
    ("atmosphere_illumination", "屏幕灯亮度", 1),
    ("volume", "音量", 5),
]

// MARK: - 按键 / 修饰键映射

enum HotkeyMap {
    static func modifiers(for name: String) -> CGEventFlags {
        var flags: CGEventFlags = []
        if name.contains("Cmd") || name.contains("Win") { flags.insert(.maskCommand) }
        if name.contains("Ctrl") { flags.insert(.maskControl) }
        if name.contains("Option") || name.contains("Alt") { flags.insert(.maskAlternate) }
        if name.contains("Shift") { flags.insert(.maskShift) }
        return flags
    }

    /// ANSI 按键码**不是按字母/数字顺序排列的** —— 字母按 QWERTY 物理位置排
    /// （A=0x00, D=0x02, C=0x08, B=0x0B…），数字也乱序
    /// （1=0x12, 2=0x13, 4=0x15, 6=0x16, 5=0x17, 9=0x19, 7=0x1A, 8=0x1C, 0=0x1D）。
    /// 早先这里用 `kVK_ANSI_A + 偏移` 硬算，导致所有字母/数字快捷键都匹配不上，
    /// 必须逐个列出来。
    private static let ansiTables: [String: Int] = [
        "A": kVK_ANSI_A, "B": kVK_ANSI_B, "C": kVK_ANSI_C, "D": kVK_ANSI_D,
        "E": kVK_ANSI_E, "F": kVK_ANSI_F, "G": kVK_ANSI_G, "H": kVK_ANSI_H,
        "I": kVK_ANSI_I, "J": kVK_ANSI_J, "K": kVK_ANSI_K, "L": kVK_ANSI_L,
        "M": kVK_ANSI_M, "N": kVK_ANSI_N, "O": kVK_ANSI_O, "P": kVK_ANSI_P,
        "Q": kVK_ANSI_Q, "R": kVK_ANSI_R, "S": kVK_ANSI_S, "T": kVK_ANSI_T,
        "U": kVK_ANSI_U, "V": kVK_ANSI_V, "W": kVK_ANSI_W, "X": kVK_ANSI_X,
        "Y": kVK_ANSI_Y, "Z": kVK_ANSI_Z,
        "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3,
        "4": kVK_ANSI_4, "5": kVK_ANSI_5, "6": kVK_ANSI_6, "7": kVK_ANSI_7,
        "8": kVK_ANSI_8, "9": kVK_ANSI_9,
    ]

    /// keyCode -> 显示名（`keyCode(for:)` 的反向）
    private static let reverseTable: [Int: String] = {
        var m: [Int: String] = [:]
        for (name, code) in ansiTables { m[code] = name }
        m[kVK_ANSI_Equal] = "+"
        m[kVK_ANSI_Minus] = "-"
        m[kVK_PageUp] = "PageUp"
        m[kVK_PageDown] = "PageDown"
        m[kVK_UpArrow] = "↑"
        m[kVK_DownArrow] = "↓"
        m[kVK_LeftArrow] = "←"
        m[kVK_RightArrow] = "→"
        for n in 1...12 { m[Int(kVK_F1) + n - 1] = "F\(n)" }
        return m
    }()

    /// 由按键事件反查显示名。录制快捷键时用。
    static func keyName(for code: CGKeyCode) -> String? {
        reverseTable[Int(code)]
    }

    /// 由修饰键标志生成名称。**顺序必须和 HotkeyLists.modifiers 里的写法一致**，
    /// 否则录制出来的值在下拉里匹配不上。
    static func modifierName(for flags: NSEvent.ModifierFlags) -> String {
        var parts: [String] = []
        if flags.contains(.command) { parts.append("Cmd") }
        if flags.contains(.control) { parts.append("Ctrl") }
        if flags.contains(.option) { parts.append("Alt") }
        if flags.contains(.shift) { parts.append("Shift") }
        return parts.isEmpty ? "无" : parts.joined(separator: " + ")
    }

    static func keyCode(for name: String) -> CGKeyCode? {
        if name == "无" || name.isEmpty { return nil }
        if let code = ansiTables[name.uppercased()] { return CGKeyCode(code) }
        if name == "+" { return CGKeyCode(kVK_ANSI_Equal) }
        if name == "-" { return CGKeyCode(kVK_ANSI_Minus) }
        if name.hasPrefix("F"), let n = Int(name.dropFirst()), n >= 1, n <= 12 {
            return CGKeyCode(kVK_F1) + UInt16(n - 1)
        }
        switch name {
        case "PageUp": return CGKeyCode(kVK_PageUp)
        case "PageDown": return CGKeyCode(kVK_PageDown)
        case "↑": return CGKeyCode(kVK_UpArrow)
        case "↓": return CGKeyCode(kVK_DownArrow)
        case "←": return CGKeyCode(kVK_LeftArrow)
        case "→": return CGKeyCode(kVK_RightArrow)
        default: return nil
        }
    }
}

// MARK: - 全局快捷键监听（CGEventTap）

final class HotkeyManager {
    struct Binding {
        let id: String
        let modifiers: CGEventFlags
        let keyCode: CGKeyCode
        /// 「功能键当标准功能键」时的媒体键兜底绑定。按住 fn 时这类绑定必须让路，
        /// 否则 fn+F10 会被当成静音键、fn+F1 会被当成调暗键。
        let isMediaFallback: Bool

        init(id: String, modifiers: CGEventFlags, keyCode: CGKeyCode,
             isMediaFallback: Bool = false) {
            self.id = id
            self.modifiers = modifiers
            self.keyCode = keyCode
            self.isMediaFallback = isMediaFallback
        }
    }

    /// 亮度调节键绑定。这类键不在普通 keyDown 的常见键码表里，单独存一份，
    /// 按 NX_KEYTYPE（调亮 2 / 调暗 3）匹配。
    private struct MediaBinding {
        let id: String
        let keyType: Int32
    }

    // MARK: 亮度调节键常量
    //
    // 同一个「调亮 / 调暗」键在 macOS 上有多种到达形式，全部要覆盖：
    //   1. NX_SYSDEFINED（事件类型 14）：subtype = 8，data1 高 16 位是键类型
    //      （调亮 2 / 调暗 3）、bit 8~15 是按下/抬起。值是 IOKit ev_keymap.h 的定义。
    //   2. 普通 keyDown keyCode 144 / 145（调亮 / 调暗）：Apple 内置键盘常见。
    //   3. 普通 keyDown 的 F2 / F1：用户在系统设置里开了「功能键当标准功能键」时。
    // 1、2 会在回调里返回 nil 吞掉事件；3 不吞（避免劫持真实的 F1/F2）。
    private static let auxControlButtonSubtype: Int16 = 8
    private static let mediaKeyStateDown: Int = 0xA
    static let keyTypeBrightnessUp: Int32 = 2      // NX_KEYTYPE_BRIGHTNESS_UP
    static let keyTypeBrightnessDown: Int32 = 3    // NX_KEYTYPE_BRIGHTNESS_DOWN
    static let keyTypeVolumeUp: Int32 = 0          // NX_KEYTYPE_SOUND_UP
    static let keyTypeVolumeDown: Int32 = 1        // NX_KEYTYPE_SOUND_DOWN
    static let keyTypeMute: Int32 = 7              // NX_KEYTYPE_MUTE
    static let mediaBrightnessUpID = "media_brightness_up"
    static let mediaBrightnessDownID = "media_brightness_down"
    static let mediaVolumeUpID = "media_volume_up"
    static let mediaVolumeDownID = "media_volume_down"
    static let mediaMuteID = "media_volume_mute"
    /// 媒体键以普通 keyDown 到达时的键码（亮度 144/145、音量 72/73/74）。
    private static let mediaKeyDownBrightnessUp: Int64 = 144
    private static let mediaKeyDownBrightnessDown: Int64 = 145
    private static let mediaKeyDownVolumeUp: Int64 = 72
    private static let mediaKeyDownVolumeDown: Int64 = 73
    private static let mediaKeyDownMute: Int64 = 74
    /// 功能键当标准功能键时的兜底键码
    /// （调亮 F2 / 调暗 F1、音量加 F12 / 减 F11 / 静音 F10）。
    static let keyCodeBrightnessUp = CGKeyCode(kVK_F2)
    static let keyCodeBrightnessDown = CGKeyCode(kVK_F1)
    static let keyCodeVolumeUp = CGKeyCode(kVK_F12)
    static let keyCodeVolumeDown = CGKeyCode(kVK_F11)
    static let keyCodeMute = CGKeyCode(kVK_F10)

    /// 是否是亮度键的 NX_KEYTYPE
    static func isBrightnessKeyType(_ keyType: Int32) -> Bool {
        keyType == keyTypeBrightnessUp || keyType == keyTypeBrightnessDown
    }

    /// 是否是音量键的 NX_KEYTYPE（含静音）
    static func isVolumeKeyType(_ keyType: Int32) -> Bool {
        keyType == keyTypeVolumeUp || keyType == keyTypeVolumeDown || keyType == keyTypeMute
    }

    private var bindings: [Binding] = []
    private var mediaBindings: [MediaBinding] = []
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?

    /// 物理 fn 键是否按下。
    ///
    /// **不能用事件自带的 `.maskSecondaryFn` 判断**：macOS 会给所有功能键的
    /// keyDown 都带上这一位，即使没按 fn（实测 F10 的 flags 是 0x20800000 / 0x800100，
    /// 两种都带 fn）。只有 `CGEventSource.flagsState` 才反映真实的修饰键状态，
    /// 不会带这个“幽灵”位。
    var isFunctionKeyDown: Bool {
        CGEventSource.flagsState(.combinedSessionState).contains(.maskSecondaryFn)
    }

    /// 触发回调：参数为绑定 id（cycle 用 action 名，adjust 用 "adjust:<index>"）。
    var onTrigger: ((String) -> Void)?

    /// 诊断用：按键码命中某条绑定但修饰键不匹配时回调。
    /// 用来区分「事件根本没到」和「到了但组合不对」。
    var onKeyCodeMatch: ((CGKeyCode, CGEventFlags, CGEventFlags) -> Void)?

    /// 诊断用：收到任意按键事件时回调
    var onKeyEvent: ((CGEvent) -> Void)?

    /// 诊断用：收到系统媒体键（亮度/音量…）时回调，参数为键类型与是否按下
    var onMediaKeyEvent: ((Int32, Bool) -> Void)?

    /// 媒体键是否该由本应用处理（参数为 NX_KEYTYPE）。返回 false 时事件原样放行给系统
    /// —— 亮度键看焦点是否在目标显示器上，音量键看当前音频输出是不是显示器。
    var shouldHandleMediaKey: ((Int32) -> Bool)?

    /// 监听被系统禁用时回调
    var onTapDisabled: (() -> Void)?

    private static let combinedMask: CGEventFlags = [.maskCommand, .maskControl, .maskAlternate, .maskShift]

    func register(id: String, modifierName: String, keyName: String) {
        bindings.removeAll { $0.id == id }
        guard modifierName != "无", keyName != "无",
              let keyCode = HotkeyMap.keyCode(for: keyName) else { return }
        bindings.append(Binding(id: id,
                                modifiers: HotkeyMap.modifiers(for: modifierName),
                                keyCode: keyCode))
    }

    /// 注册一个系统媒体键（目前用于硬件亮度键）。
    func registerMedia(id: String, keyType: Int32) {
        mediaBindings.removeAll { $0.id == id }
        mediaBindings.append(MediaBinding(id: id, keyType: keyType))
    }

    /// 注册一个不带修饰键的普通按键。给硬件亮度/音量键做兼容：
    /// 系统若把 F1/F2、F10~F12 当标准功能键，就只会发 keyDown，不会发 NX_SYSDEFINED。
    func registerRaw(id: String, keyCode: CGKeyCode) {
        bindings.removeAll { $0.id == id }
        bindings.append(Binding(id: id, modifiers: [], keyCode: keyCode,
                                isMediaFallback: true))
    }

    func clear() {
        bindings.removeAll()
        mediaBindings.removeAll()
    }

    /// 返回是否成功建立了事件监听。
    /// `tapCreate` 在缺权限时会直接返回 nil —— 这种情况必须让调用方知道，
    /// 否则表现就是"快捷键配了但完全没反应"。
    @discardableResult
    func start() -> Bool {
        if eventTap != nil { return true }
        // 普通按键是 keyDown；硬件亮度键是 NX_SYSDEFINED（类型 14），两种都要听。
        let mask = (CGEventMask(1) << CGEventType.keyDown.rawValue)
            | (CGEventMask(1) << nxSystemDefinedEventType)
        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: { _, type, event, refcon in
                guard let refcon else { return Unmanaged.passUnretained(event) }
                let manager = Unmanaged<HotkeyManager>.fromOpaque(refcon).takeUnretainedValue()

                // 回调超时会被系统临时禁用。不重新启用的话快捷键就永久失效了，
                // 而且表面上完全看不出来（tap 还在，只是收不到事件）。
                if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
                    if let tap = manager.eventTap { CGEvent.tapEnable(tap: tap, enable: true) }
                    manager.onTapDisabled?()
                    return Unmanaged.passUnretained(event)
                }

                if type == .keyDown {
                    manager.receivedKeyEvents += 1
                    manager.onKeyEvent?(event)
                    // fn 组合键是标准功能键（fn+F10 就是 F10），不能按媒体键处理。
                    let functionKeyDown = manager.isFunctionKeyDown
                    // 媒体键若以普通 keyDown 到达（keyCode 72/73/74、144/145），先拦下并吞掉，
                    // 否则内置屏/系统音量会跟着变。
                    if manager.handleMediaKeyDown(event, functionKeyDown: functionKeyDown) { return nil }
                    manager.handle(event, functionKeyDown: functionKeyDown)
                } else if type.rawValue == nxSystemDefinedEventType {
                    // 命中我们注册的媒体键就吞掉（返回 nil）；按住 fn 时原样放行。
                    if manager.handleSystemDefined(event, functionKeyDown: manager.isFunctionKeyDown) { return nil }
                }
                return Unmanaged.passUnretained(event)
            },
            userInfo: selfPtr
        ) else { return false }

        eventTap = tap
        runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        if let runLoopSource {
            CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        }
        CGEvent.tapEnable(tap: tap, enable: true)
        return true
    }

    /// 已注册的绑定数量（用于诊断）
    var bindingCount: Int { bindings.count }

    /// 已注册的系统媒体键数量（用于诊断）
    var mediaBindingCount: Int { mediaBindings.count }

    func stop() {
        if let eventTap { CGEvent.tapEnable(tap: eventTap, enable: false) }
        if let runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
            self.runLoopSource = nil
        }
        eventTap = nil
    }

    /// `functionKeyDown` 必须来自 `isFunctionKeyDown`（flagsState），不能看事件 flags：
    /// 功能键 keyDown 自带 `.maskSecondaryFn`，直接看会误判。
    func handle(_ event: CGEvent, functionKeyDown: Bool) {
        let keyCode = CGKeyCode(event.getIntegerValueField(.keyboardEventKeycode))
        let flags = event.flags.intersection(Self.combinedMask)
        for binding in bindings where binding.keyCode == keyCode {
            // 按住 fn 时 F1/F2、F10~F12 是标准功能键，不能触发媒体键兜底。
            if functionKeyDown && binding.isMediaFallback { continue }
            let expected = binding.modifiers.intersection(Self.combinedMask)
            if expected == flags {
                onTrigger?(binding.id)
                break
            } else {
                // 只在真的不符时报告，便于排查"配了没反应"
                onKeyCodeMatch?(keyCode, flags, expected)
            }
        }
    }

    /// 处理「媒体键被当作普通 keyDown」的情况（keyCode 72/73/74、144/145）。
    /// 命中已注册的媒体键就触发并返回 true，让回调吞掉事件。
    /// 按住 fn 时不处理：此时系统发的是标准功能键，应该透传给前台应用。
    func handleMediaKeyDown(_ event: CGEvent, functionKeyDown: Bool) -> Bool {
        guard !mediaBindings.isEmpty, !functionKeyDown else { return false }
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        guard let keyType = Self.mediaKeyType(forKeyCode: keyCode) else { return false }
        guard shouldHandleMediaKey?(keyType) ?? true else { return false }
        for binding in mediaBindings where binding.keyType == keyType {
            onTrigger?(binding.id)
            return true
        }
        return false
    }

    /// 把「以普通 keyDown 到达」的媒体键键码映射到 NX_KEYTYPE。抽出来方便单测。
    static func mediaKeyType(forKeyCode keyCode: Int64) -> Int32? {
        switch keyCode {
        case mediaKeyDownBrightnessUp: return keyTypeBrightnessUp
        case mediaKeyDownBrightnessDown: return keyTypeBrightnessDown
        case mediaKeyDownVolumeUp: return keyTypeVolumeUp
        case mediaKeyDownVolumeDown: return keyTypeVolumeDown
        case mediaKeyDownMute: return keyTypeMute
        default: return nil
        }
    }

    /// 处理 NX_SYSDEFINED 事件。返回 true 表示这是已注册的媒体键，
    /// 调用方应返回 nil 把事件吞掉。按住 fn 时原样放行。
    func handleSystemDefined(_ event: CGEvent, functionKeyDown: Bool) -> Bool {
        guard !functionKeyDown else { return false }
        guard let nsEvent = NSEvent(cgEvent: event) else { return false }
        guard nsEvent.subtype.rawValue == Self.auxControlButtonSubtype else { return false }
        let (keyType, isDown) = Self.parseMediaKey(data1: nsEvent.data1)
        onMediaKeyEvent?(keyType, isDown)
        // 只看按下，抬起事件原样放行
        guard isDown else { return false }
        receivedMediaKeyEvents += 1
        guard shouldHandleMediaKey?(keyType) ?? true else { return false }
        for binding in mediaBindings where binding.keyType == keyType {
            onTrigger?(binding.id)
            return true
        }
        return false
    }

    /// 解析 NX_SYSDEFINED 的 `data1`：高 16 位是媒体键类型，
    /// bit 8~15 是状态（0xA 按下 / 0xB 抬起）。抽出来方便单测。
    static func parseMediaKey(data1: Int) -> (keyType: Int32, isDown: Bool) {
        let keyType = Int32(truncatingIfNeeded: (data1 & 0xFFFF0000) >> 16)
        let isDown = (data1 & 0xFF00) >> 8 == mediaKeyStateDown
        return (keyType, isDown)
    }

    /// 事件是否已到达监听（用于诊断）
    private(set) var receivedKeyEvents = 0

    /// 系统媒体键事件计数（用于诊断）
    private(set) var receivedMediaKeyEvents = 0

    // MARK: 权限

    static var isTrusted: Bool { AXIsProcessTrusted() }

    /// 弹出系统授权引导。
    ///
    /// 系统会弹一个「「App」想要控制此电脑」的框，带「打开系统设置」按钮。
    /// **每个 app 只会弹一次**，之后这个调用就静默无效了 —— 所以用户拒绝过之后
    /// 必须靠 `openAccessibilitySettings()` 引导。
    @discardableResult
    static func promptForAccessibility() -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    /// 直接打开「系统设置 → 隐私与安全性 → 辅助功能」
    static func openAccessibilitySettings() {
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!
        NSWorkspace.shared.open(url)
    }

    /// 兼容旧调用点
    static func requestAccessibility() {
        _ = promptForAccessibility()
    }
}
