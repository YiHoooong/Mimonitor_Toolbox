import SwiftUI
import AppKit
import MimonitorPresetCore

/// 应用主题（对应工具页的「应用主题」选项）。
///
/// 用 `NSApp.appearance` 而不是 SwiftUI 的 `.preferredColorScheme`：
/// 前者作用于整个进程，AppKit 部分（悬浮窗那个 NSPanel、弹出菜单等）也跟着变；
/// 后者只影响被修饰的那棵 SwiftUI 视图树，面板会漏掉。
enum AppTheme {
    static let key = "theme"

    static var current: String {
        UserDefaults.standard.string(forKey: key) ?? "auto"
    }

    static func apply(_ name: String) {
        switch name {
        case "dark":  NSApp.appearance = NSAppearance(named: .darkAqua)
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        default:      NSApp.appearance = nil      // nil = 跟随系统
        }
    }

    static func applyCurrent() { apply(current) }
}

/// Dock 图标的显隐。
///
/// Info.plist 里**不要**写 `LSUIElement=true`。那个键把 app 注册成「后台型」，
/// 启动台、Dock、Cmd+Tab、强制退出列表全都看不到它 —— 用户从 DMG 拖进
/// Applications 之后在启动台里根本找不到入口。（踩过：一开始按「不在 Dock
/// 常驻」把 LSUIElement 设成了 true，等于理解成了「永远不进 Dock」。）
///
/// 正确做法是保持普通 app 身份，再按「有没有可见窗口」在运行时切显示策略。
/// WPS 那类「Dock 里也有、菜单栏也有」的软件就是这么做的：
///
///   有窗口 → .regular    Dock 出现图标，Cmd+Tab 能切
///   没窗口 → .accessory  Dock 图标消失，只留菜单栏图标继续常驻
///
/// 于是「关掉窗口就退回菜单栏」的行为保住了，同时又能在启动台里找到。
enum DockVisibility {
    private static var observers: [NSObjectProtocol] = []

    static func startMonitoring() {
        // 悬浮提示窗（HUDWindow）和菜单栏下拉面板都是 NSPanel，属于辅助窗口。
        // 它们每次弹出 / 收起都会发这些通知 —— 不排除掉的话，按一下快捷键
        // Dock 图标就会跟着闪一下。
        let names: [Notification.Name] = [
            NSWindow.didBecomeKeyNotification,
            NSWindow.didMiniaturizeNotification,
            NSWindow.didDeminiaturizeNotification,
            NSWindow.willCloseNotification,
        ]
        observers = names.map { name in
            NotificationCenter.default.addObserver(
                forName: name, object: nil, queue: .main
            ) { note in
                guard !(note.object is NSPanel) else { return }
                // 必须延到下一轮 runloop 再算：willClose 是在窗口**真正隐藏之前**
                // 发出的，此刻遍历 NSApp.windows 它还是 isVisible == true，
                // 于是判定成「还有可见窗口」不切换 —— 而关窗后再没有别的通知进来，
                // 结果就是关掉窗口 Dock 图标永远不消失（实测踩到过）。
                DispatchQueue.main.async { refresh() }
            }
        }
        refresh()
    }

    /// 主窗口即将显示前先切回 .regular。等 didBecomeKey 再切的话，
    /// 窗口已经画出来但 Dock 图标还慢半拍。
    static func showDockIcon() {
        guard NSApp.activationPolicy() != .regular else { return }
        NSApp.setActivationPolicy(.regular)
    }

    /// 判定「有没有真正的主窗口」。
    ///
    /// 不能只写 `isVisible && !(is NSPanel)`：**菜单栏图标自己也是一个可见窗口**
    /// （NSStatusBarWindow），它不是 NSPanel，于是会被算进来 —— 结果永远是
    /// 「还有窗口」，Dock 图标关掉窗口也不消失（实测踩到过，日志里能看到
    /// `wins=[AppKitWindow:hid NSStatusBarWindow:vis]`）。
    ///
    /// 改成认标题栏：主窗口有 `.titled`，菜单栏图标窗口和悬浮提示窗都没有。
    /// 全是公开 API，不依赖 NSStatusBarWindow 这种私有类名。
    private static func hasMainWindow() -> Bool {
        NSApp.windows.contains {
            $0.isVisible && $0.styleMask.contains(.titled) && !($0 is NSPanel)
        }
    }

    private static func refresh() {
        let hasVisibleWindow = hasMainWindow()
        let want: NSApplication.ActivationPolicy = hasVisibleWindow ? .regular : .accessory
        guard NSApp.activationPolicy() != want else { return }
        NSApp.setActivationPolicy(want)
    }
}

/// 窗口关闭行为（对应工具页里的「窗口关闭行为」选项，与原版一致）：
///   - tray：关掉窗口只是隐藏，应用继续驻留在菜单栏
///   - exit：关掉窗口直接退出
/// 「隐藏窗口」不会顺带退出 —— 见 DockVisibility：没窗口时应用退回 .accessory
/// 策略，菜单栏图标还在，所以进程继续活着。
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // 启动时先把主题应用上，否则会先闪一下系统外观再切换
        AppTheme.applyCurrent()
        DockVisibility.startMonitoring()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        UserDefaults.standard.string(forKey: "close_behavior") == "exit"
    }
}

@main
struct MimonitorToolboxApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var state = AppState()

    init() {
        // 默认值为 true 的设置必须在这里注册，不能放 AppState.init()：
        // AppState 的属性初始化器先于它自己的 init 执行，那时注册已经太晚。
        // 注册后 bool(forKey:) 在任何位置都安全（未写入时返回这里的值）。
        // 默认值为 false 的设置不需要注册——UserDefaults 对 Bool 的内置回退就是 false。
        UserDefaults.standard.register(defaults: [
            "crosshair_game_mode_only": true,
        ])
    }

    var body: some Scene {
        // 用 Window（单例）而不是 WindowGroup：这是单窗口工具，
        // 而且菜单栏的「显示主窗口」需要能按 id 精确唤回同一个窗口。
        Window("红米G Pro ToolBox", id: "main") {
            ContentView()
                .environmentObject(state)
                // 主页连接那一行是全局最宽的内容：
                // 「显示器 IP:」标签 + 输入框 + 四个按钮 + 间距 + 内边距，再加 220 的侧边栏，
                // 实测要 ~935pt。原来给 900 会把「开始连接」和「扫描内网」挤到一起。
                .frame(minWidth: 1020, minHeight: 660)
        }
        .windowResizability(.contentMinSize)
        .commands {
            // 去掉「显示 → 隐藏边栏」（以及对应的快捷键），
            // 折叠入口一并砍掉：折叠/展开时侧边栏会抽一下（SwiftUI NavigationSplitView
            // 在动画收尾时还会再重排一次），本工具也不需要折叠，索性不支持。
            CommandGroup(replacing: .sidebar) { }
        }

        // 菜单栏图标（顶栏状态栏）。Dock 图标的显隐见 DockVisibility ——
        // 有窗口时在 Dock，关掉窗口只剩这里。
        // 用 .window 样式而不是默认的 .menu：菜单里放不了滑块，
        // 而数值型条目（背光、对比度…）按步长加加减减太难用。
        MenuBarExtra {
            MenuBarPanel()
                .environmentObject(state)
        } label: {
            // 用 display 符号而不是应用图标：菜单栏图标惯例是单色符号，
            // 彩色图标混在 Wi-Fi / 电池那排里会很突兀。
            Image(systemName: "display")
        }
        .menuBarExtraStyle(.window)
    }
}

/// 菜单栏下方面板。比系统菜单自由得多 —— 能放滑块、分组、任意布局。
private struct MenuBarPanel: View {
    @EnvironmentObject var state: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // 状态行
            HStack(spacing: 8) {
                Circle()
                    .fill(state.statusColor)
                    .frame(width: 8, height: 8)
                Text(state.statusText)
                    .font(.callout)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)

            Divider()

            if state.menuBarItems.isEmpty && state.presetConfiguration.menuBarPresets.isEmpty {
                Text("还没有添加快捷项。\n去「菜单栏」或「预设模式」页添加。")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 24)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if !state.presetConfiguration.menuBarPresets.isEmpty {
                            MenuBarPresetPicker()
                        }
                        ForEach(state.menuBarItems, id: \.self) { id in
                            if let entry = MenuBarCatalog.entry(id) {
                                MenuBarPanelRow(entry: entry)
                            }
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                }
                .frame(maxHeight: 420)
            }

            Divider()

            HStack(spacing: 8) {
                Button("显示主窗口") {
                    // 先切回 .regular 再开窗：此时应用可能正处于 .accessory
                    // （Dock 里没有图标），不先切的话窗口能开出来但 Dock 图标要等一拍
                    DockVisibility.showDockIcon()
                    openWindow(id: "main")
                    NSApp.activate(ignoringOtherApps: true)
                }
                if state.isConnected {
                    Button("断开") { state.disconnectAdb() }
                } else {
                    Button("重连") { state.connect() }
                }
                Spacer()
                Button("退出") { NSApp.terminate(nil) }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .frame(width: 320)
    }
}

/// Native segmented selection, matching the close-behavior control in ToolsView.
private struct MenuBarPresetPicker: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("预设模式").font(.callout).foregroundColor(.secondary)
                Spacer()
                Text(state.presetConfiguration.applicationIncomplete ? "应用未完成" : state.activePresetName)
                    .font(.caption).foregroundColor(.secondary).lineLimit(1)
            }
            ScrollView(.horizontal, showsIndicators: true) {
                PresetSegmentedControl(
                    presets: state.presetConfiguration.menuBarPresets,
                    selectedID: state.isConnected && !state.presetConfiguration.applicationIncomplete
                        ? (state.activePresetID ?? PicturePreset.baselineID) : nil,
                    isEnabled: state.isConnected && !state.isPresetOperationInFlight,
                    baselineEnabled: state.activePresetID == nil || state.canRestoreBaseline,
                    onSelect: { id in
                        guard id != (state.activePresetID ?? PicturePreset.baselineID)
                            || state.presetConfiguration.applicationIncomplete else { return }
                        if id == PicturePreset.baselineID { state.restoreBaseline() }
                        else { state.applyPreset(id: id) }
                    }
                )
                .accessibilityLabel("预设模式")
                .fixedSize(horizontal: true, vertical: false)
                .frame(minWidth: 292)
            }
            .frame(height: 32)
            .disabled(!state.isConnected || state.isPresetOperationInFlight)
        }
    }
}

/// AppKit permits selectedSegment == -1: hidden presets and disconnected displays need no selection.
private struct PresetSegmentedControl: NSViewRepresentable {
    let presets: [PicturePreset]
    let selectedID: String?
    let isEnabled: Bool
    let baselineEnabled: Bool
    let onSelect: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSSegmentedControl {
        let control = NSSegmentedControl()
        control.trackingMode = .selectOne
        control.segmentStyle = .automatic
        control.target = context.coordinator
        control.action = #selector(Coordinator.selectSegment(_:))
        return control
    }

    func updateNSView(_ control: NSSegmentedControl, context: Context) {
        context.coordinator.ids = presets.map(\.id)
        context.coordinator.onSelect = onSelect
        control.segmentCount = presets.count
        for (index, preset) in presets.enumerated() {
            control.setLabel(preset.name, forSegment: index)
            control.setWidth(0, forSegment: index)
            control.setEnabled(isEnabled && (preset.id != PicturePreset.baselineID || baselineEnabled), forSegment: index)
        }
        control.selectedSegment = presets.firstIndex { $0.id == selectedID } ?? -1
        control.isEnabled = isEnabled
        control.invalidateIntrinsicContentSize()
    }

    final class Coordinator: NSObject {
        var ids: [String] = []
        var onSelect: ((String) -> Void)?
        @objc func selectSegment(_ control: NSSegmentedControl) {
            guard control.isEnabled, ids.indices.contains(control.selectedSegment),
                  control.isEnabled(forSegment: control.selectedSegment) else { return }
            onSelect?(ids[control.selectedSegment])
        }
    }
}

/// 面板里的单个快捷项。
private struct MenuBarPanelRow: View {
    @EnvironmentObject var state: AppState
    let entry: MenuBarEntry

    /// 滑块拖动中的本地值：松手才下发，避免拖动过程刷出一串 ADB 命令
    @State private var dragging: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch entry.kind {
            case .options(let options):
                Text(entry.label).font(.callout).foregroundColor(.secondary)
                Picker("", selection: Binding(
                    get: { state.menuBarValue(for: entry.id) ?? -1 },
                    set: { state.applyMenuBarOption(entry.id, value: $0) }
                )) {
                    ForEach(options) { opt in
                        Text(opt.label).tag(opt.value)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .frame(maxWidth: .infinity, alignment: .leading)

            case .stepper(let lo, let hi, _):
                let current = state.menuBarValue(for: entry.id) ?? lo
                HStack {
                    Text(entry.label).font(.callout).foregroundColor(.secondary)
                    Spacer()
                    Text("\(dragging ?? current)")
                        .font(.callout.monospacedDigit())
                        .foregroundColor(.primary)
                }
                // 用自绘的 FastSlider 而不是系统 Slider：
                // 后者底层是 NSSlider，每次创建都要向 CoreUI 解析主题 rendition，
                // 正是之前画面页切页卡 1.6 秒的根因（见 README 性能那节）。
                FastSlider(
                    value: Binding(
                        get: { Double(dragging ?? current) },
                        set: { dragging = Int($0.rounded()) }
                    ),
                    range: Double(lo)...Double(hi),
                    step: 1
                ) { v in
                    // 松手才提交
                    state.setMenuBarItem(entry.id, to: Int(v))
                    dragging = nil
                }
            }
        }
        .disabled(!state.isConnected)
        .opacity(state.isConnected ? 1 : 0.5)
    }
}

// 注：曾经的菜单式渲染（MenuBarMenu / MenuBarEntryMenu）已删除 ——
// 改成 .window 样式的面板后它就没有调用方了。
