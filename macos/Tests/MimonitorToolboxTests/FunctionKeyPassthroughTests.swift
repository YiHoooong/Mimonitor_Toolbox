#if canImport(Cocoa)
import XCTest
import Cocoa
@testable import MimonitorToolbox

/// fn+F1/F2、fn+F10~F12 应该作为标准功能键透传给前台应用，
/// 而不是继续调显示器背光/音量。
///
/// 关键点：macOS 给**所有**功能键的 keyDown 都带上 `.maskSecondaryFn`
/// （即使没按 fn），所以判断物理 fn 只能用 flagsState / flagsChanged，
/// 不能用事件自带的 flags。这里把两条媒体键路径都锁住。
final class FunctionKeyPassthroughTests: XCTestCase {

    private func keyDown(_ keyCode: CGKeyCode, flags: CGEventFlags = []) -> CGEvent {
        let event = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: true)!
        event.flags = flags
        return event
    }

    private func systemDefinedEvent(keyType: Int32, down: Bool) -> CGEvent {
        let data1 = (Int(keyType) << 16) | (down ? 0x0A00 : 0x0B00)
        let nsEvent = NSEvent.otherEvent(
            with: .systemDefined,
            location: .zero,
            modifierFlags: [],
            timestamp: 0,
            windowNumber: 0,
            context: nil,
            subtype: 8,
            data1: data1,
            data2: -1
        )!
        return nsEvent.cgEvent!
    }

    // MARK: 媒体键兜底绑定（F1/F2、F10~F12）

    func testRawMediaBindingDoesNotTriggerWhenFunctionKeyIsDown() {
        let manager = HotkeyManager()
        manager.registerRaw(id: HotkeyManager.mediaVolumeUpID,
                            keyCode: HotkeyManager.keyCodeVolumeUp)
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        manager.handle(keyDown(HotkeyManager.keyCodeVolumeUp), functionKeyDown: true)
        XCTAssertTrue(triggered.isEmpty)

        manager.handle(keyDown(HotkeyManager.keyCodeVolumeUp), functionKeyDown: false)
        XCTAssertEqual(triggered, [HotkeyManager.mediaVolumeUpID])
    }

    func testRawBrightnessBindingDoesNotTriggerWhenFunctionKeyIsDown() {
        let manager = HotkeyManager()
        manager.registerRaw(id: HotkeyManager.mediaBrightnessDownID,
                            keyCode: HotkeyManager.keyCodeBrightnessDown)
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        manager.handle(keyDown(HotkeyManager.keyCodeBrightnessDown), functionKeyDown: true)
        XCTAssertTrue(triggered.isEmpty)

        manager.handle(keyDown(HotkeyManager.keyCodeBrightnessDown), functionKeyDown: false)
        XCTAssertEqual(triggered, [HotkeyManager.mediaBrightnessDownID])
    }

    /// 用户自己录的快捷键不受影响：按住 fn 时仍然照常匹配。
    func testUserBindingStillTriggersWhenFunctionKeyIsDown() {
        let manager = HotkeyManager()
        manager.register(id: "user_cmd_a", modifierName: "Cmd", keyName: "A")
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        let keyCode = HotkeyMap.keyCode(for: "A")!
        manager.handle(keyDown(keyCode, flags: [.maskCommand]), functionKeyDown: true)
        XCTAssertEqual(triggered, ["user_cmd_a"])
    }

    /// macOS 会给功能键 keyDown 带上 `.maskSecondaryFn`，即使物理 fn 没按。
    /// 不能因为事件 flags 里有这一位就误判成 fn 组合键。
    func testPhantomFunctionFlagOnKeyDownDoesNotDisableMediaFallback() {
        let manager = HotkeyManager()
        manager.registerRaw(id: HotkeyManager.mediaVolumeUpID,
                            keyCode: HotkeyManager.keyCodeVolumeUp)
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        manager.handle(keyDown(HotkeyManager.keyCodeVolumeUp, flags: [.maskSecondaryFn]),
                       functionKeyDown: false)
        XCTAssertEqual(triggered, [HotkeyManager.mediaVolumeUpID])
    }

    // MARK: 以普通 keyDown 到达的媒体键（72/73/74、144/145）

    func testMediaKeyDownIsPassedThroughWhenFunctionKeyIsDown() {
        let manager = HotkeyManager()
        manager.registerMedia(id: HotkeyManager.mediaVolumeUpID,
                              keyType: HotkeyManager.keyTypeVolumeUp)
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        XCTAssertFalse(manager.handleMediaKeyDown(
            keyDown(CGKeyCode(72)), functionKeyDown: true))
        XCTAssertTrue(triggered.isEmpty)

        XCTAssertTrue(manager.handleMediaKeyDown(
            keyDown(CGKeyCode(72)), functionKeyDown: false))
        XCTAssertEqual(triggered, [HotkeyManager.mediaVolumeUpID])
    }

    // MARK: NX_SYSDEFINED

    func testSystemDefinedMediaKeyIsPassedThroughWhenFunctionKeyIsDown() {
        let manager = HotkeyManager()
        manager.registerMedia(id: HotkeyManager.mediaVolumeUpID,
                              keyType: HotkeyManager.keyTypeVolumeUp)
        var triggered: [String] = []
        manager.onTrigger = { triggered.append($0) }

        let event = systemDefinedEvent(keyType: HotkeyManager.keyTypeVolumeUp, down: true)
        XCTAssertFalse(manager.handleSystemDefined(event, functionKeyDown: true))
        XCTAssertTrue(triggered.isEmpty)

        XCTAssertTrue(manager.handleSystemDefined(event, functionKeyDown: false))
        XCTAssertEqual(triggered, [HotkeyManager.mediaVolumeUpID])
    }
}
#endif
