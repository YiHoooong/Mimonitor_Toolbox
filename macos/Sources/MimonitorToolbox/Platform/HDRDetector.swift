import AppKit
import CoreGraphics

/// HDR 状态检测。
///
/// macOS 没有 Windows DXGI 那样的「系统 HDR 开关」查询接口，所以这里用两条互补的信号：
///
/// 1. **主机侧**：macOS 给外接屏打开「高动态范围」后，该屏的 *potential* EDR 峰值会从
///    1.0 跳到远大于 1（实测 Mi Monitor：1.000 → 10.152）。注意必须用
///    `maximumPotentialExtendedDynamicRangeColorComponentValue` 而不是
///    `maximumExtendedDynamicRangeColorComponentValue` —— 后者在没播放 HDR 内容时
///    仍然是 1.0，实测开/关都一样。
///
/// 2. **显示器侧**：收到 HDR 信号时显示器会自动切到 HDR 画面模式
///    （实测 macOS 开启 HDR 后 picture_mode 从 14「标准」跳到 18「Dolby Vision IQ」）。
///
/// 两者取或：只用主机侧会在显示器切换到别的信号源（游戏机等）时失效；
/// 只用显示器侧则可能被手动选的 HDR 模式误触发。
enum HDRDetector {
    /// 主机侧：任意外接显示器处于 HDR 模式。
    ///
    /// 必须排除内建屏 —— 它的 potential EDR 恒为 2.0，不排除的话永远判成 HDR。
    static func hostSideHDR() -> Bool {
        for screen in NSScreen.screens {
            guard let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            else { continue }
            let displayID = CGDirectDisplayID(num.uint32Value)
            if CGDisplayIsBuiltin(displayID) != 0 { continue }
            if screen.maximumPotentialExtendedDynamicRangeColorComponentValue > 1.0 { return true }
        }
        return false
    }

    /// 显示器侧：当前画面模式属于 HDR 模式集合。
    static func monitorSideHDR(pictureMode: String?) -> Bool {
        RegisterMap.isHDRPictureMode(pictureMode)
    }

    /// 综合判定。
    static func isHDR(pictureMode: String?) -> Bool {
        hostSideHDR() || monitorSideHDR(pictureMode: pictureMode)
    }

    /// 主机侧是否具备 HDR 能力（潜在 EDR > 1），与「当前是否开启」无关。
    /// 用于在界面上说明检测原理。
    static func hostSupportsHDR() -> Bool {
        for screen in NSScreen.screens {
            guard let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            else { continue }
            let displayID = CGDirectDisplayID(num.uint32Value)
            if CGDisplayIsBuiltin(displayID) != 0 { continue }
            if screen.maximumPotentialExtendedDynamicRangeColorComponentValue > 1.0 { return true }
        }
        return false
    }

    /// 主机侧：尝试读取单个外接显示器的系统输出刷新率（Hz）。
    /// 多外接屏或无法读取有效刷新率时返回 nil，退回手动确认提示。
    static func externalDisplayRefreshRateHz() -> Int? {
        var rates: [Int] = []
        for screen in NSScreen.screens {
            guard let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            else { continue }
            let displayID = CGDirectDisplayID(num.uint32Value)
            if CGDisplayIsBuiltin(displayID) != 0 { continue }
            var hz = CGDisplayCopyDisplayMode(displayID)?.refreshRate ?? 0
            if hz <= 1, #available(macOS 12.0, *) {
                hz = Double(screen.maximumFramesPerSecond)
            }
            let rounded = Int(hz.rounded())
            if rounded > 1 {
                rates.append(rounded)
            }
        }
        return rates.count == 1 ? rates[0] : nil
    }

    /// 诊断用：列出各屏的 EDR 数值。
    static func debugDescription() -> String {
        NSScreen.screens.map { screen in
            let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            let builtin = num.map { CGDisplayIsBuiltin(CGDirectDisplayID($0.uint32Value)) != 0 } ?? true
            return String(format: "%@%@ 当前%.2f/潜在%.2f",
                          screen.localizedName,
                          builtin ? "(内建)" : "",
                          screen.maximumExtendedDynamicRangeColorComponentValue,
                          screen.maximumPotentialExtendedDynamicRangeColorComponentValue)
        }.joined(separator: "  ")
    }
}
