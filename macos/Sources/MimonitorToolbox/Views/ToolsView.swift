import SwiftUI
import UniformTypeIdentifiers

struct ToolsView: View {
    @EnvironmentObject var state: AppState
    @AppStorage("theme") private var theme: String = "dark"
    @AppStorage("close_behavior") private var closeBehavior: String = "tray"
    @State private var autostart = Autostart.isEnabled()
    @State private var pending4K: Bool = false
    @State private var show4KConfirm = false
    @State private var showApkImporter = false
    @State private var showLogExporter = false
    @State private var showHotkeySheet = false

    private var configuredHotkeyCount: Int {
        state.hotkeys.values.filter { $0.isEnabled }.count
            + state.adjustHotkeys.filter { $0.isEnabled }.count
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("工具与设置").font(.title2.bold())

                // 软件设置
                SectionCard(title: "软件设置") {
                    HStack(spacing: 16) {
                        Text("窗口关闭行为:").frame(width: 120, alignment: .leading)
                        // 对应 AppDelegate.applicationShouldTerminateAfterLastWindowClosed。
                        // macOS 上没有 Windows 那种"托盘"，等价物是菜单栏图标。
                        Picker("", selection: $closeBehavior) {
                            Text("隐藏窗口（继续在菜单栏运行）").tag("tray")
                            Text("退出应用").tag("exit")
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 260)
                        Spacer()
                    }
                    HStack(spacing: 16) {
                        Text("应用主题:").frame(width: 120, alignment: .leading)
                        Picker("", selection: $theme) {
                            Text("跟随系统").tag("auto")
                            Text("深色模式").tag("dark")
                            Text("浅色模式").tag("light")
                        }
                        .frame(width: 220)
                        .onChange(of: theme) { AppTheme.apply($0) }
                        Spacer()
                    }
                    Toggle("开机自动启动并隐藏窗口（仅菜单栏）", isOn: $autostart)
                        .onChange(of: autostart) { v in state.setAutostart(v) }

                    Divider()

                    // 快捷键生效前的等待。开着时按一下会先走一段倒计时再下发，
                    // 连按只落最后一个值；关掉就每次立即下发（更跟手，但连按会把命令堆起来）。
                    Toggle("快捷键松手后生效（倒计时）", isOn: Binding(
                        get: { state.hotkeyCountdownEnabled },
                        set: { state.hotkeyCountdownEnabled = $0 }
                    ))

                    HStack(spacing: 12) {
                        Text("倒计时时长")
                            .frame(width: 120, alignment: .leading)
                        FastSlider(
                            value: Binding(
                                get: { state.hotkeyCountdownSeconds },
                                set: { state.hotkeyCountdownSeconds = $0 }
                            ),
                            range: 0.2...3.0,
                            step: 0.1
                        ) { _ in }
                        .disabled(!state.hotkeyCountdownEnabled)
                        Text(String(format: "%.1f 秒", state.hotkeyCountdownSeconds))
                            .font(.callout.monospacedDigit())
                            .foregroundColor(state.hotkeyCountdownEnabled ? .primary : .secondary)
                            .frame(width: 60, alignment: .trailing)
                        Spacer()
                    }
                    .opacity(state.hotkeyCountdownEnabled ? 1 : 0.4)

                    Text(state.hotkeyCountdownEnabled
                         ? "按一下快捷键后，进度条走完才真正下发；连按只保留最后一个值。"
                         : "已关闭：每次按键立即下发。连按会把 ADB 命令堆起来，可能变慢。")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                // 4K UI
                SectionCard(title: "4K UI 模式") {
                    Text("将显示器 UI 分辨率提升至 3840×2160，DPI 设为 640。开启或关闭后显示器将自动重启。")
                        .foregroundColor(.secondary)
                    Text("当前状态：\(state.is4KUI ? "已开启" : "未开启（1080p）")")
                        .font(.callout)
                        .foregroundColor(state.is4KUI ? .green : .secondary)
                    HStack(spacing: 10) {
                        // 开关反映的是显示器实测状态；点击只发起切换请求，不直接改本地值
                        Toggle("启用 4K UI", isOn: Binding(
                            get: { state.is4KUI },
                            set: { newValue in
                                pending4K = newValue
                                show4KConfirm = true
                            }
                        ))
                        Button("重新检测") { state.check4KState() }
                        Spacer()
                    }
                }

                // ADB 保活守护
                SectionCard(title: "ADB 保活守护") {
                    Text("部署电视端 AdbGuardian，重启、待机或唤醒后自动恢复无线 ADB，并保持 5555 端口可用。")
                        .foregroundColor(.secondary)
                    Text(state.guardianStatus).foregroundColor(.secondary)
                    HStack(spacing: 8) {
                        Button("检测状态") { state.checkGuardian() }
                        Button("部署/修复") { state.deployGuardian() }.buttonStyle(.borderedProminent)
                        Button("启动保活") { state.startGuardian() }
                    }
                }

                // HDR / SDR 分区控光记忆
                SectionCard(title: "HDR/SDR 分区控光记忆") {
                    Toggle("按信号分别记忆精密控光（基于 macOS EDR 判断 HDR）", isOn: Binding(
                        get: { state.effectiveHdrMemoryEnabled },
                        set: { state.toggleHdrMemory($0) }
                    ))
                    .disabled(state.memoriesSuspendedByPreset)
                    Text(state.hdrMemoryStatusText).font(.callout).foregroundColor(.secondary)
                    if state.memoriesSuspendedByPreset {
                        Label("使用预设期间已锁定，返回无预设后恢复原设置。", systemImage: "lock.fill")
                            .font(.caption).foregroundColor(.secondary)
                    }
                }

                // FreeSync Pro 模式记忆
                SectionCard(title: "FreeSync Pro 模式记忆") {
                    Toggle("开启 FreeSync 时记住画面模式，关闭后自动恢复", isOn: Binding(
                        get: { state.effectiveFreesyncMemoryEnabled },
                        set: { state.toggleFreesyncMemory($0) }
                    ))
                    .disabled(state.memoriesSuspendedByPreset)
                    Text(state.freesyncMemoryStatusText).font(.callout).foregroundColor(.secondary)
                    if state.memoriesSuspendedByPreset {
                        Label("使用预设期间已锁定，返回无预设后恢复原设置。", systemImage: "lock.fill")
                            .font(.caption).foregroundColor(.secondary)
                    }
                }

                // 准星模式联动
                SectionCard(title: "准星模式联动") {
                    Toggle("准星仅在游戏模式下生效", isOn: Binding(
                        get: { state.crosshairGameModeOnly },
                        set: { state.toggleCrosshairGameModeOnly($0) }
                    ))
                    Text(state.crosshairModeStatusText).font(.callout).foregroundColor(.secondary)
                }

                // APK 安装
                SectionCard(title: "APK 安装") {
                    Text("选择本地 .apk 文件，通过 adb install -r -d 安装到显示器。")
                        .foregroundColor(.secondary)
                    HStack(spacing: 8) {
                        Button("选择 APK 并安装") { showApkImporter = true }
                        if state.isConnected {
                            Text("已连接").font(.callout).foregroundColor(.green)
                        }
                    }
                }

                // ADB 命令行
                SectionCard(title: "ADB 命令行") {
                    Text("在终端打开独立 adb server（端口 5038）的 shell，便于手动调试。")
                        .foregroundColor(.secondary)
                    HStack(spacing: 8) {
                        Button("打开 ADB CMD") { state.openAdbCmd() }
                        Button("进入 ADB Shell") { state.openAdbShell() }
                    }
                }

                // 全局快捷键
                SectionCard(title: "自定义全局快捷键") {
                    Text("全局快捷键基于 CGEventTap 实现，需要辅助功能权限。")
                        .foregroundColor(.secondary)
                    if state.accessibilityTrusted {
                        Label("辅助功能权限已授予", systemImage: "checkmark.seal.fill")
                            .font(.callout)
                            .foregroundColor(.green)
                    } else {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("尚未授予辅助功能权限，全局快捷键不会生效", systemImage: "exclamationmark.triangle.fill")
                                .font(.callout)
                                .foregroundColor(.orange)
                            HStack(spacing: 10) {
                                Button("前往授权") { state.requestAccessibilityPermission() }
                                    .buttonStyle(.borderedProminent)
                                Button("重新检测") { state.refreshAccessibilityStatus() }
                                // TCC 的授权结果要重启进程才会生效，光切回窗口不够
                                // （实测：切回前台刷新仍是未授权，重启后立刻生效）
                                Text("授权后请重启本应用")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                    // 配置面板放进独立 sheet，而不是内联折叠。
                    // 内联展开会改变整页高度 → 工具页上十个卡片全部重新布局，
                    // 实测要 850ms（采样显示是 SwiftUI 依赖图全量重算）。
                    // 放进 sheet 后只影响那个小面板，展开是瞬时的。
                    HStack(spacing: 10) {
                        Button("配置快捷键…") { showHotkeySheet = true }
                            .buttonStyle(.borderedProminent)
                        Text("已配置 \(configuredHotkeyCount) 个")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                    }
                }

                Link("仓库地址：https://github.com/YiHoooong/Mimonitor_Toolbox",
                     destination: URL(string: "https://github.com/YiHoooong/Mimonitor_Toolbox")!)
                    .font(.callout)
                    .foregroundColor(Color(red: 0.45, green: 0.31, blue: 1.0))
                    .frame(maxWidth: .infinity)
            }
            .padding(30)
        }
        .onAppear { state.check4KState() }
        .sheet(isPresented: $showHotkeySheet) {
            HotkeyConfigSheet()
                .environmentObject(state)
        }
        .alert("切换 4K UI 会重启显示器，继续？", isPresented: $show4KConfirm) {
            Button("取消", role: .cancel) { }
            Button("确认") { state.toggle4K(pending4K) }
        } message: {
            Text(pending4K
                 ? "将设置 wm size 3840x2160 / density 640 并重启显示器。"
                 : "将恢复 wm size 1920x1080 / density 320 并重启显示器。")
        }
        .fileImporter(isPresented: $showApkImporter,
                      allowedContentTypes: [UTType(filenameExtension: "apk") ?? .item]) { result in
            if case .success(let url) = result {
                state.installApk(url)
            }
        }
    }

    private func hotkeyBinding(_ action: String) -> Binding<HotkeyConfig> {
        Binding(
            get: { state.hotkeys[action] ?? HotkeyConfig() },
            set: { state.hotkeys[action] = $0 }
        )
    }

    private func adjustBinding(_ index: Int) -> Binding<AdjustHotkeyConfig> {
        Binding(
            get: { state.adjustHotkeys[index] },
            set: { state.adjustHotkeys[index] = $0 }
        )
    }
}

// MARK: - 快捷键配置面板

/// 独立的配置 sheet。放在 sheet 里而不是内联折叠，是因为内联展开会改变整页高度、
/// 触发工具页所有卡片的重新布局（实测 850ms）。
struct HotkeyConfigSheet: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("自定义全局快捷键").font(.headline)
                Spacer()
            }
            .padding(16)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(hotkeyActions, id: \.id) { action in
                        HotkeyRow(label: action.label, config: hotkeyBinding(action.id))
                    }

                    Divider().padding(.vertical, 4)

                    ForEach(state.adjustHotkeys.indices, id: \.self) { i in
                        AdjustHotkeyRow(config: adjustBinding(i)) {
                            state.adjustHotkeys.remove(at: i)
                        }
                    }
                    Button("新建可调快捷键") {
                        state.adjustHotkeys.append(AdjustHotkeyConfig())
                    }
                }
                .padding(16)
            }

            Divider()

            HStack(spacing: 12) {
                Text("点「未设置」后直接按组合键即可录入")
                    .font(.caption)
                    .foregroundColor(.secondary)

                // 排查"配了没反应"时才有用；平时开着手打键盘会把日志刷满
                Toggle("诊断日志", isOn: Binding(
                    get: { state.hotkeyDebugEnabled },
                    set: { state.hotkeyDebugEnabled = $0 }
                ))
                .toggleStyle(.checkbox)
                .font(.caption)
                .help("记录「按键码命中但修饰键不符」这类信息，用于排查快捷键不生效")

                Spacer()
                Button("完成") {
                    state.saveHotkeys()
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(16)
        }
        .frame(width: 640, height: 500)
    }

    private func hotkeyBinding(_ action: String) -> Binding<HotkeyConfig> {
        Binding(
            get: { state.hotkeys[action] ?? HotkeyConfig() },
            set: { state.hotkeys[action] = $0 }
        )
    }

    private func adjustBinding(_ index: Int) -> Binding<AdjustHotkeyConfig> {
        Binding(
            get: { state.adjustHotkeys[index] },
            set: { state.adjustHotkeys[index] = $0 }
        )
    }
}

// MARK: - 按键录入

/// 快捷键录入按钮：点一下开始录制，然后直接按想用的组合键。
/// **修饰键和主键一起录** —— 按 Cmd+Option+Z 就同时记下两者。
///
/// 之前是「修饰键下拉 + 55 项按键下拉」两个 Picker。展开配置面板时，
/// 9 行就是 9 个 NSPopUpButton（每个 11 项）+ 55 项的长列表，
/// 实测要 1 秒才画完。改成单个录入按钮后一个 Picker 都不剩。
/// Esc 取消；不带修饰键的普通字母/数字会被拒绝（否则会劫持全局打字）。
struct KeyRecorderButton: View {
    @Binding var modifier: String
    @Binding var key: String
    var width: CGFloat = 150

    @State private var recording = false
    @State private var monitor: Any?
    @State private var hint: String?

    var body: some View {
        HStack(spacing: 6) {
            Button {
                recording ? stopRecording() : startRecording()
            } label: {
                Text(recording ? "请按键…" : displayText)
                    .frame(width: width)
            }
            .buttonStyle(.bordered)
            .tint(recording ? .accentColor : nil)

            if let hint {
                Text(hint).font(.caption).foregroundStyle(.orange)
            }
        }
        .onDisappear { stopRecording() }
    }

    private var displayText: String {
        guard modifier != "无", key != "无" else { return "未设置" }
        return "\(modifier) + \(key)"
    }

    private func startRecording() {
        recording = true
        hint = nil
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            // Esc 取消
            if event.keyCode == 53 { stopRecording(); return nil }
            guard let name = HotkeyMap.keyName(for: event.keyCode) else {
                hint = "不支持的按键"
                return nil
            }
            let modName = HotkeyMap.modifierName(for: event.modifierFlags)
            // 没修饰键的字母/数字会被全局劫持，导致正常打字失灵，必须挡住
            let isFunctionKey = name.hasPrefix("F") && Int(name.dropFirst()) != nil
            if modName == "无" && !isFunctionKey {
                hint = "需要配合修饰键"
                return nil
            }
            modifier = modName
            key = name
            stopRecording()
            return nil   // 录制期间吞掉全部按键，避免误触发
        }
    }

    private func stopRecording() {
        recording = false
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }
}

// MARK: - 快捷键行

struct HotkeyRow: View {
    let label: String
    @Binding var config: HotkeyConfig

    var body: some View {
        HStack(spacing: 10) {
            Text(label).frame(width: 150, alignment: .leading)
            KeyRecorderButton(modifier: $config.modifier, key: $config.key)
            Spacer()
        }
    }
}

struct AdjustHotkeyRow: View {
    @Binding var config: AdjustHotkeyConfig
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Picker("", selection: $config.param) {
                ForEach(adjustableParams, id: \.id) { Text($0.label).tag($0.id) }
            }
            .frame(width: 100)
            Picker("", selection: $config.direction) {
                Text("增大").tag("increase")
                Text("减小").tag("decrease")
            }
            .frame(width: 70)
            Stepper("步长 \(config.step)", value: $config.step, in: 1...100)
                .frame(width: 140)
            KeyRecorderButton(modifier: $config.modifier, key: $config.key, width: 130)
            Button(action: onDelete) {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
        }
    }
}
