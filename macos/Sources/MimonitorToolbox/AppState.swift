import Foundation
import SwiftUI
import AppKit
import MimonitorPresetCore

enum ConnectionStatus: Equatable {
    case disconnected
    case connecting
    case connected(String)
    case scanning
}

// MARK: - ADB 保活守护（AdbGuardian）

fileprivate enum Guardian {
    static let package = "com.example.adbguardian"
    static let mainActivity = "\(package)/.MainActivity"
    static let accessibility = "\(package)/\(package).AdbGuardianAccessibilityService"
}

fileprivate struct GuardianStatus {
    let installed: Bool
    let pid: String
    let accessibility: Bool
    let adbEnabled: Bool
    let adbWifiEnabled: Bool
    let servicePort: String
    let persistPort: String
    let adbd: String
    let mask: String
    let stopped: Bool

    var ok: Bool {
        installed && !pid.isEmpty && adbEnabled && adbWifiEnabled
            && servicePort == "5555" && persistPort == "5555"
            && adbd == "running" && accessibility && mask == "-134250497" && !stopped
    }

    var summary: String {
        if ok { return "状态：✅ 正常（已安装 · 保活运行中 · 端口 5555 · 无障碍已授权）" }
        var parts: [String] = []
        if !installed { parts.append("未安装") }
        else if pid.isEmpty { parts.append("未运行") }
        if !adbEnabled { parts.append("adb_enabled≠1") }
        if !adbWifiEnabled { parts.append("adb_wifi≠1") }
        if servicePort != "5555" || persistPort != "5555" { parts.append("端口≠5555") }
        if adbd != "running" { parts.append("adbd 未运行") }
        if !accessibility { parts.append("无障碍未授权") }
        if stopped { parts.append("被停止") }
        if parts.isEmpty { return "状态：已安装（部分检查未通过）" }
        return "状态：⚠️ \(parts.joined(separator: " / "))"
    }
}

/// 应用状态：连接状态、当前读取值、日志，以及所有显示器控制动作。
/// 对应原版 App 类的职责（不含 UI 控件，UI 由各 View 负责）。
final class AppState: ObservableObject {
    let adb = AdbClient()
    let hotkeyManager = HotkeyManager()
    var connectionIntent = ConnectionIntent()
    private var pageRefreshIntent = PageRefreshIntent()
    private let connectionQueue = DispatchQueue(label: "com.mimonitor.toolbox.connection")
    let deviceQueue = DispatchQueue(label: "com.mimonitor.toolbox.device", qos: .userInitiated)
    let presetEngine = PresetEngine(store: PresetStore())
    @Published var presetConfiguration = PresetConfiguration()
    @Published var isPresetBusy = false
    @Published var isPresetOperationInFlight = false
    @Published var presetOperationText = ""
    @Published var presetError: String?
    @Published var automaticTaskStatus = ""
    @Published var requestedPage: Page?
    var presetTimer: Timer?
    var presetWakeObserver: NSObjectProtocol?
    var pendingPresetSave: DispatchWorkItem?
    var pendingPresetSaveID: String?
    var pendingPresetSaveDevice: String?
    var pictureEditRevision = 0
    /// Only accessed on deviceQueue; native mode/reset commands may settle after ADB returns.
    var pictureSettleUntil = Date.distantPast
    var lastAutomaticTaskError = ""

    @Published var connectionStatus: ConnectionStatus = .disconnected
    @Published var currentValues: [String: String] = [:]
    @Published var logLines: [String] = []
    /// 每写一行日志就自增。界面靠它触发自动滚动 ——
    /// 不能用 logLines.count：日志满 500 行后会 removeFirst，count 恒定不变，
    /// 于是滚动就再也不触发了。
    @Published var logSeq: Int = 0
    @Published var scannedDevices: [String] = []
    @Published var selectedDevice: String = ""
    @Published var activeSource: String = "未知"
    @Published var loadedPages: Set<String> = []
    @Published var isBusy: Bool = false
    /// 正在刷新的页面，用于显示 loading 遮罩
    @Published var loadingPages: Set<String> = []

    @Published var ipInput: String = UserDefaults.standard.string(forKey: "saved_ip") ?? ""

    // 工具页状态
    @Published var logToFileEnabled = false
    @Published var guardianStatus = "状态：未检测"
    @Published var hdrMemoryEnabled = UserDefaults.standard.bool(forKey: "hdr_sdr_local_dimming_enabled")
    /// 显示器当前是否处于 4K UI 模式（由 check4KState 检测得到）
    @Published var is4KUI = false
    @Published var freesyncMemoryEnabled = UserDefaults.standard.bool(forKey: "freesync_mode_memory_enabled")
    @Published var hdrMemoryStatusText = ""
    @Published var freesyncMemoryStatusText = ""
    /// 准星是否只在游戏模式下生效。默认值 true 在 App.init() 里用
    /// register(defaults:) 注册，所以这里可以直接用 bool(forKey:)。
    @Published var crosshairGameModeOnly = UserDefaults.standard.bool(forKey: "crosshair_game_mode_only")
    @Published var crosshairModeStatusText = ""
    /// 准星联动写入进行中，挡住重入的 reconcile
    private var crosshairReconcileBusy = false
    /// 上一次对账时的画面模式，用于区分「进入游戏模式」与「一直在游戏模式」
    private var crosshairLastMode: String?
    /// 本 app 是否已获辅助功能权限（和终端是两回事，各算各的）
    @Published var accessibilityTrusted = HotkeyManager.isTrusted

    /// 快捷键诊断日志。默认关闭：正常打字时每按一个不匹配的键都会记一行，很快把日志刷满。
    @Published var hotkeyDebugEnabled = UserDefaults.standard.bool(forKey: "hotkey_debug") {
        didSet { UserDefaults.standard.set(hotkeyDebugEnabled, forKey: "hotkey_debug") }
    }
    @Published var hotkeys: [String: HotkeyConfig] = AppState.loadHotkeys()
    @Published var adjustHotkeys: [AdjustHotkeyConfig] = AppState.loadAdjustHotkeys()

    private var hdrLastState: Bool?
    private var logFileHandle: FileHandle?
    private var logFileURL: URL?
    private var sourcePollTimer: Timer?
    private var sourcePollArmed = false
    private var monitorChecking = false
    private var monitorTick = 0
    private var lastMonitorFailState = ""
    /// 切页时刻，用于统计刷新耗时
    private var pageAppearTimes: [String: Date] = [:]

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    private static let fileTimeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd_HHmmss"
        return f
    }()

    init() {
        startPresetFeatures()
        log("系统就绪，等待连接...")
        hotkeyManager.onTrigger = { [weak self] id in self?.handleHotkeyTrigger(id) }
        // 诊断默认关闭：每按一个不匹配的键就写一行日志，正常打字会把日志刷满。
        // 在「自定义全局快捷键」面板里按需打开。
        hotkeyManager.onKeyCodeMatch = { [weak self] code, flags, expected in
            guard let self, self.hotkeyDebugEnabled else { return }
            self.log("快捷键诊断: 按键码 \(code) 命中，但修饰键不符（实收 \(flags.rawValue) / 期望 \(expected.rawValue)）")
        }
        hotkeyManager.onTapDisabled = { [weak self] in
            self?.log("快捷键诊断: 事件监听被系统禁用，已重新启用")
        }
        applyHotkeys()
        hotkeyManager.start()
        updateHdrMemoryStatus()
        updateFreesyncMemoryStatus()
        updateCrosshairModeStatus()
        Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.pollHdrState()
        }
        // 配了快捷键却没授权：启动时引导一次。
        // 系统授权框每个 app 只会弹一次，之后再调用是静默的，所以只在有快捷键配置时尝试。
        if !accessibilityTrusted && !hotkeys.isEmpty {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                _ = HotkeyManager.promptForAccessibility()
            }
        }

        // 先把 adb server 预热起来，省得自动连接那一步还要等它冷启动
        runConnectionTask { self.adb.warmUpServer() }

        // 启动后自动连接上次设备（原版延迟 900ms，等窗口先出来）
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { [weak self] in
            self?.autoConnectOnStartup()
        }
        // adb 链路监控（对应原版 _monitor_adb_server）。
        // 每 6 秒一跳：未连接时每跳都重试（故障恢复要快），已连接时每 5 跳体检一次。
        Timer.scheduledTimer(withTimeInterval: 6.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.monitorTick &+= 1
            if self.isConnected && self.monitorTick % 5 != 0 { return }
            self.monitorAdbServer()
        }
    }

    deinit {
        presetTimer?.invalidate()
        pendingPresetSave?.cancel()
        if let observer = presetWakeObserver { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
    }

    /// 探活并自愈。
    ///
    /// 两种失效都会表现为连不上：server 长时间运行后失效（报 "No route to host"），
    /// 以及 adb connect 后卡在 offline 不翻转。两者都靠 kill-server + start-server 治，
    /// 但设备侧清掉旧 transport 需要时间，所以要反复重试而不是只试一次。
    private func monitorAdbServer() {
        guard !monitorChecking else { return }
        guard connectionIntent.allowsAutomaticRecovery else { return }
        // 正在连接/扫描时不要插手：监控会重启 adb server，而重启会把
        // 进行中的连接打断，于是「越监控越连不上」。这是之前冷启动很慢的元凶。
        switch connectionStatus {
        case .connecting, .scanning: return
        case .disconnected, .connected: break
        }

        // 未连接时也要能重试，所以目标从 adb.ip 兜底到上次保存的 IP
        let target = adb.ip.isEmpty
            ? (UserDefaults.standard.string(forKey: "saved_ip") ?? "").trimmingCharacters(in: .whitespaces)
            : adb.ip
        guard !target.isEmpty else { return }

        let wasConnected = isConnected    // 在主线程序上读，避免后台 sync 主线程
        let request = connectionIntent.generation
        if adb.ip.isEmpty { adb.ip = target }
        monitorChecking = true
        runConnectionTask {
            defer { DispatchQueue.main.async { self.monitorChecking = false } }

            if wasConnected && self.adb.deviceState() == "device" { return }   // 一切正常

            if !self.adb.isServerAlive() { self.adb.restartServer() }
            let (ok, state) = self.adb.ensureConnected()
            let model = ok ? self.adb.getModel() : ""

            DispatchQueue.main.async {
                guard self.connectionIntent.isCurrent(request),
                      self.connectionIntent.allowsAutomaticRecovery else { return }
                if ok {
                    let ip = target
                    self.connectionStatus = .connected(model.isEmpty ? ip : "\(model) · \(ip)")
                    if !wasConnected {
                        // 关键：恢复后必须把连接状态也置回已连接，
                        // 否则界面一直以为"未连接"，点任何页面都会被弹回主页。
                        self.log("已连接: \(ip)")
                        self.loadedPages = []
                        self.refreshPage("picture")
                        self.refreshPage("game")
                        self.pollHdrState(force: true)
                        self.checkAutomaticTasks()
                    } else {
                        self.log("ADB 链路已恢复")
                    }
                    self.lastMonitorFailState = ""
                } else if self.isConnected {
                    self.connectionStatus = .disconnected
                    self.log("ADB 链路恢复失败: \(state)")
                } else if self.lastMonitorFailState != state {
                    // 未连接状态下只在失败原因变化时记一次，避免每 6 秒刷屏
                    self.lastMonitorFailState = state
                    self.log("自动重连失败: \(state)")
                }
            }
        }
    }

    // MARK: - 通用工具

    var isConnected: Bool {
        if case .connected = connectionStatus { return true }
        return false
    }

    var statusText: String {
        switch connectionStatus {
        case .disconnected: return "未连接"
        case .connecting: return "连接中"
        case .connected(let detail): return "已连接 (\(detail))"
        case .scanning: return "正在扫描内网..."
        }
    }

    var statusColor: Color {
        switch connectionStatus {
        case .disconnected: return Color(red: 0.85, green: 0.23, blue: 0.0)
        case .connecting, .scanning: return Color(red: 0.72, green: 0.36, blue: 0.0)
        case .connected: return Color(red: 0.06, green: 0.49, blue: 0.25)
        }
    }

    var pictureModeHint: String {
        let raw = currentValues["picture_mode"]?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let raw, let mode = Int(raw) else {
            return "当前场景：未知（\(raw ?? "无")）"
        }
        if let group = RegisterMap.pictureModeGroupName(mode) {
            return "当前场景：\(group)（\(mode)）"
        }
        let scene = RegisterMap.sceneNames[mode] ?? "未知场景"
        return "当前场景：\(scene)（\(mode)），不匹配上方模式按钮"
    }

    /// 游戏模式提示的三态（移植自 _update_game_mode_hint）
    enum GameModeState { case unknown, active, inactive }

    var gameModeState: GameModeState {
        // 两个键任一有有效值就算"已知"；原版还会容忍 "null"/"N/A"
        let keys = ["picture_mode", "picture_preset_scenario"]
        let known = keys.contains { key in
            guard let v = currentValues[key]?.trimmingCharacters(in: .whitespacesAndNewlines) else { return false }
            return !v.isEmpty && v != "null" && v != "N/A"
        }
        guard known else { return .unknown }
        let isGame = keys.contains { RegisterMap.isGamePictureMode(currentValues[$0]) }
        return isGame ? .active : .inactive
    }

    var gameModeHintText: String {
        switch gameModeState {
        case .unknown: return "当前画面模式未知；高亮值尚未确认是否生效。"
        case .active: return "当前为游戏模式；下方高亮为当前生效值。"
        case .inactive: return "当前不是游戏模式；下方高亮为记忆值，功能当前未生效。"
        }
    }

    /// 未知 / 非游戏模式用琥珀色警示，游戏模式用常规色
    var gameModeHintIsWarning: Bool { gameModeState != .active }

    var isCustomColorTemp: Bool { intValue("picture_color_temperature", default: 1) == 3 }

    func intValue(_ key: String, default fallback: Int) -> Int {
        guard let s = currentValues[key],
              let v = Int(s.trimmingCharacters(in: .whitespacesAndNewlines)) else { return fallback }
        return v
    }

    private func clamp(_ v: Int, _ lo: Int, _ hi: Int) -> Int { max(lo, min(hi, v)) }

    func log(_ message: String) {
        let line = "[\(Self.timeFormatter.string(from: Date()))] \(message)"
        DispatchQueue.main.async {
            self.logLines.append(line)
            if self.logLines.count > 500 {
                self.logLines.removeFirst(self.logLines.count - 500)
            }
            self.logSeq &+= 1
            if self.logToFileEnabled, let handle = self.logFileHandle {
                try? handle.write(contentsOf: (line + "\n").data(using: .utf8)!)
            }
        }
    }

    private func runBackground(_ work: @escaping () -> Void) {
        deviceQueue.async(execute: work)
    }

    private func runConnectionTask(_ work: @escaping () -> Void) {
        connectionQueue.async(execute: work)
    }

    private func setValue(_ key: String, _ value: String) {
        DispatchQueue.main.async { self.currentValues[key] = value }
    }

    // MARK: - 连接 / 扫描

    /// 启动时自动连接上次设备；没记录就扫描内网（对应原版 _auto_connect_on_startup）。
    func autoConnectOnStartup() {
        guard connectionIntent.allowsStartupAttempt else { return }
        guard !isConnected else { return }
        let saved = (UserDefaults.standard.string(forKey: "saved_ip") ?? "")
            .trimmingCharacters(in: .whitespaces)
        if saved.isEmpty {
            log("启动自动连接: 未找到上次设备，开始扫描内网")
            scanNet()
            return
        }
        ipInput = saved
        log("启动自动连接: 尝试连接上次设备 \(saved)")
        connect(isAuto: true)
    }

    func connect(isAuto: Bool = false) {
        let ip = ipInput.trimmingCharacters(in: .whitespaces)
        guard !ip.isEmpty else { log("请输入显示器 IP 地址"); return }
        let request = connectionIntent.beginConnection()
        UserDefaults.standard.set(ip, forKey: "saved_ip")
        adb.ip = ip
        connectionStatus = .connecting
        if !isAuto { log("正在连接 \(ip)...") }

        runConnectionTask {
            let (ok, state) = self.adb.ensureConnected()

            if ok {
                // 连接后先确保 JNI 依赖的 jar 已部署到设备（原版 check_and_heal_jar）
                self.adb.ensureJars()
                let model = self.adb.getModel()
                let detail = model.isEmpty ? ip : "\(model) · \(ip)"
                DispatchQueue.main.async {
                    guard self.connectionIntent.isCurrent(request) else { return }
                    self.connectionStatus = .connected(detail)
                    self.log(isAuto ? "启动自动连接成功: \(detail)" : "已连接: \(detail)")
                    self.loadedPages = []
                    self.loadingPages = []
                    self.activeSource = "未知"
                    self.hdrLastState = nil
                    // scannedDevices 只存在内存里、不持久化：重启后列表是空的，
                    // 虽然自动连上了保存的 IP，下拉框却还显示占位符。
                    // 这里把当前 IP 补进列表，保证下拉框显示的就是实际连的设备。
                    if !ip.isEmpty {
                        if let idx = self.scannedDevices.firstIndex(of: ip) {
                            self.scannedDevices.remove(at: idx)
                        }
                        self.scannedDevices.insert(ip, at: 0)
                        self.selectedDevice = ip
                    }
                    // 与原版一致：连接后只预刷画面和游戏两页，其余切过去时懒加载
                    self.refreshPage("picture")
                    self.refreshPage("game")
                    self.pollHdrState(force: true)
                    self.checkAutomaticTasks()
                    self.check4KState()
                    // 原版连接后还会检测保活守护状态（QTimer 1800ms）
                    self.checkGuardian()
                }
            } else {
                DispatchQueue.main.async {
                    guard self.connectionIntent.isCurrent(request) else { return }
                    self.connectionStatus = .disconnected
                    if isAuto {
                        self.log("启动自动连接失败: \(state)，开始扫描内网")
                        self.scanNet()
                    } else {
                        self.log("连接失败: \(state)")
                    }
                }
            }
        }
    }

    /// 进入页面时触发：懒加载该页数据 + 控制信号源轮询。
    func onPageAppear(_ page: Page) {
        if page == .source {
            startSourcePolling()
        } else {
            stopSourcePolling()
        }
        // 记下切页时刻，用于衡量「切换 → 数据上屏」的耗时
        pageAppearTimes[page.rawValue] = Date()
        refreshPage(page.rawValue)
    }

    /// 该页是否必须先连接显示器（对应原版 _PAGES_NEED_CONNECTION）。
    func needsConnection(_ page: Page) -> Bool {
        switch page {
        case .picture, .game, .source, .light, .remote: return true
        // 菜单栏配置页只改本地配置，没连显示器也能用
        case .home, .tools, .menuBar, .presets, .autoTasks: return false
        }
    }

    func disconnectAdb() {
        pendingPresetSave?.cancel()
        pendingPresetSave = nil
        // Keep dirty identity; reconnecting this same display can still save the edit safely.
        let ip = adb.ip
        connectionIntent.disconnect()
        adb.ip = ""
        connectionStatus = .disconnected
        currentValues = [:]
        loadedPages = []
        loadingPages = []
        activeSource = "未知"
        hdrLastState = nil
        stopSourcePolling()
        if !ip.isEmpty {
            runConnectionTask { _ = self.adb.disconnect(ip: ip) }
        }
        log("已断开连接")
        updateAutomaticTaskStatus()
    }

    func scanNet() {
        switch connectionStatus {
        case .disconnected: break
        case .connecting: log("正在连接显示器，请等待连接完成或先断开"); return
        case .connected: log("已连接显示器，请先断开后扫描"); return
        case .scanning: log("正在扫描内网，请等待扫描完成"); return
        }
        let request = connectionIntent.beginScan()
        connectionStatus = .scanning
        log("开始扫描内网...")
        runBackground {
            let subnets = NetworkScan.localIPv4Subnets()
            DispatchQueue.main.async {
                self.log("本机网段: \(subnets.isEmpty ? "未识别" : subnets.joined(separator: ", "))")
            }
            let found = NetworkScan.scan()
            DispatchQueue.main.async {
                guard self.connectionIntent.isCurrent(request) else { return }
                self.scannedDevices = found
                self.connectionStatus = .disconnected
                self.log("扫描完成，发现 \(found.count) 台设备")
                if let first = found.first {
                    self.selectedDevice = first
                    self.ipInput = first
                }
            }
        }
    }

    // MARK: - 改动后的合并刷新

    private var pendingRefreshWork: DispatchWorkItem?

    /// 合并「设置改动后回读真实值」的刷新。
    ///
    /// 不合并的话，连按快捷键每按一次就排一次全文刷新（21 个 settings + 6 个 JNI 键，
    /// 还要重绘整页），十几次堆起来会把 ADB 通道占满 —— 表现就是"按多了就卡"。
    /// 原版用 `_picture_mode_switch_seq` 做同样的事。
    private func scheduleCoalescedRefresh(delay: TimeInterval, pages: [String]) {
        pendingRefreshWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.pendingRefreshWork = nil
            for page in pages { self.loadedPages.remove(page) }
            for page in pages { self.refreshPage(page) }
        }
        pendingRefreshWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    // MARK: - 页面数据加载

    /// JNI 才是硬件真实状态，settings 会漂移（比如用户用显示器自带的 OSD 改过设置，
    /// 或上一次写入只成功了一半）。下面几项一律以 JNI 回读值为准，
    /// 否则界面会显示与硬件实际不符的值。移植自 device_features.py:1381-1418。
    private func applyJniOverrides(_ values: inout [String: String]) {
        // 色域：MTK 值与 settings 值同域，直接覆盖主键和旧 OSD 键
        if let raw = values["g_video__vid_gamut_mapping_mode"], let v = Int(raw) {
            values["tv_picture_advanced_video_color_space"] = String(v)
            values["tv_picture_video_color_space"] = String(v)
        }
        // 色温：MTK 枚举与小米枚举不同，需要反查
        if let raw = values["g_video__clr_temp"], let v = Int(raw),
           let ui = RegisterMap.mtkToColorTemp[v] {
            values["picture_color_temperature"] = String(ui)
        }
        // 精密控光：覆盖官方主键和旧 OSD 键
        if let raw = values["g_video__vid_local_dimming"], let v = Int(raw) {
            values["picture_local_dimming"] = String(v)
            values["tv_picture_video_local_dimming"] = String(v)
        }
        // HDR 色调映射：OSD 索引与 MTK 底层枚举不同，需要反查
        if let raw = values["g_video__vid_hdr_tone_mapping_mode"], let v = Int(raw) {
            values["picture_hdr_tone_mapping"] = String(v)
            if let ui = RegisterMap.hdrToneMappingMtkToUI[v] {
                values["settings_display_hdr_color_tone"] = String(ui)
            }
        }
        // 响应时间同样以 JNI 为准（原版 query_setting_or_jni）
        if let raw = values["g_video__vid_od_response_time"], let v = Int(raw) {
            values["picture_response_time"] = String(v)
        }
        // 自动调整亮度（光感）：菜单读的是 MTK 侧，覆盖 settings 值
        if let raw = values["g_video__light_sensor_switch"], let v = Int(raw) {
            values["tv_picture_light_sensor"] = String(v)
        }
        // 背光滑条同样以 JNI 为准
        if let raw = values["g_disp__disp_back_light"], let v = Int(raw) {
            values["picture_backlight"] = String(v)
            values["xiaomi_picture_backlight"] = String(v)
        }
    }

    /// 游戏页的 320Hz / FreeSync 开关没有直接的 settings 键，要由 EDID / 自适应同步
    /// 的 JNI 值推导（移植自 device_features.py:1420-1452）。DP 和 HDMI 走不同的键。
    private func applyGameJniMode(_ values: inout [String: String]) {
        let src = Int(values["mitv.tvplayer.hdmi.last.source"] ?? "") ?? -1
        if src == 29 || src == 30 {
            let m = adb.jniBatchGet(keys: ["g_fusion_picture__dp_edid_version",
                                           "g_video__dp_adaptive_sync"])
            values["mode_320"] = (Int(m["g_fusion_picture__dp_edid_version"] ?? "") == 3) ? "1" : "0"
            values["freesync"] = (Int(m["g_video__dp_adaptive_sync"] ?? "") == 1) ? "1" : "0"
        } else {
            let m = adb.jniBatchGet(keys: ["g_fusion_picture__hdmi_edid_version",
                                           "g_video__freesync_switch"])
            values["mode_320"] = (Int(m["g_fusion_picture__hdmi_edid_version"] ?? "") == 6) ? "1" : "0"
            values["freesync"] = (Int(m["g_video__freesync_switch"] ?? "") == 3) ? "1" : "0"
        }
    }

    /// HDR 色调映射只在部分画面模式下存在（原版会据此隐藏控件）
    var showHdrToneMapping: Bool {
        RegisterMap.isHdrToneMappingPictureMode(currentValues["picture_mode"])
    }

    /// 有数据可刷的页面（对应原版 _page_data_keys）。主页/工具页没有可读寄存器。
    static let refreshablePages: Set<String> = ["picture", "game", "source", "light"]

    func refreshPage(_ page: String) {
        guard isConnected else { return }
        guard !isPresetBusy else { return }
        guard Self.refreshablePages.contains(page) else { return }
        guard !loadedPages.contains(page) else { return }
        let connectionRequest = connectionIntent.generation
        let pageRequest = pageRefreshIntent.begin(page)
        loadedPages.insert(page)
        loadingPages.insert(page)

        let settingsKeys: [String]
        let jniKeys: [String]
        switch page {
        case "picture": settingsKeys = PageDataKeys.pictureSettings; jniKeys = PageDataKeys.pictureJni
        case "game": settingsKeys = PageDataKeys.gameSettings; jniKeys = []
        case "source": settingsKeys = PageDataKeys.sourceSettings; jniKeys = []
        case "light": settingsKeys = PageDataKeys.lightSettings; jniKeys = []
        default: settingsKeys = []; jniKeys = []
        }

        runBackground {
            // ── 第一阶段：settings（约 0.1s）──────────────────────────
            var settingsVals: [String: String] = [:]
            if !settingsKeys.isEmpty {
                settingsVals = self.adb.settingsGetBatch(settingsKeys)
            }
            if !settingsVals.isEmpty {
                DispatchQueue.main.async {
                    guard self.isConnected,
                          self.connectionIntent.isCurrent(connectionRequest),
                          self.pageRefreshIntent.isCurrent(page, pageRequest) else { return }
                    self.mergeValues(settingsVals)
                    // 首屏数据到手就撤遮罩，不必等慢一档的 JNI
                    self.loadingPages.remove(page)
                    if let t0 = self.pageAppearTimes[page] {
                        let ms = Int(Date().timeIntervalSince(t0) * 1000)
                        if ms > 50 { self.log("⏱ \(page) 首屏数据 \(ms)ms") }
                    }
                }
            }

            // ── 第二阶段：JNI 回读（约 0.9s，瓶颈在设备端启动 app_process）──
            // 慢，所以单独一步：先把上面拿到的值显示出来，这一批到了再静默修正。
            var jniVals: [String: String] = [:]
            if !jniKeys.isEmpty {
                jniVals = self.adb.jniBatchGet(keys: jniKeys)
            }

            var merged = settingsVals
            for (k, v) in jniVals { merged[k] = v }
            self.applyJniOverrides(&merged)
            if page == "game" {
                self.applyGameJniMode(&merged)
            }

            // 只把「与 settings 不同」的键挑出来作为修正量
            var corrections: [String: String] = [:]
            for (k, v) in merged where settingsVals[k] != v {
                corrections[k] = v
            }

            DispatchQueue.main.async {
                guard self.isConnected,
                      self.connectionIntent.isCurrent(connectionRequest),
                      self.pageRefreshIntent.isCurrent(page, pageRequest) else { return }
                if !corrections.isEmpty { self.mergeValues(corrections) }

                // 拿到新鲜的 picture_mode + front_sight_index 后按模式纠正准星
                if page == "picture" || page == "game" {
                    self.reconcileCrosshairModeState()
                }

                if page == "source",
                   let s = settingsVals["mitv.tvplayer.hdmi.last.source"],
                   let n = Int(s.trimmingCharacters(in: .whitespacesAndNewlines)) {
                    self.activeSource = RegisterMap.sourceNames[n] ?? s
                }
                self.loadingPages.remove(page)
                if let t0 = self.pageAppearTimes[page] {
                    let ms = Int(Date().timeIntervalSince(t0) * 1000)
                    self.pageAppearTimes[page] = nil
                    self.log("已刷新 \(page) 页面数据（合计 \(ms)ms）")
                } else {
                    self.log("已刷新 \(page) 页面数据")
                }
            }
        }
    }

    /// 合并写入 currentValues。必须整体赋值：逐个键赋值会让 @Published 触发 N 次重绘。
    private func mergeValues(_ values: [String: String]) {
        guard !values.isEmpty else { return }
        var merged = currentValues
        for (k, v) in values { merged[k] = v }
        currentValues = merged
    }

    func forceRefreshPage(_ page: String) {
        loadedPages.remove(page)
        refreshPage(page)
    }

    var hasPendingDeviceControls: Bool { !cyclePending.isEmpty || !adjustPendingWork.isEmpty }

    func invalidatePictureRefreshesForPreset() {
        for page in ["picture", "game"] {
            _ = pageRefreshIntent.begin(page)
            loadedPages.remove(page)
            loadingPages.remove(page)
        }
        pendingRefreshWork?.cancel()
        pendingRefreshWork = nil
        // A countdown armed for the previous preset must never land in the new one.
        for item in cyclePending.values { item.work.cancel() }
        cyclePending.removeAll()
        for item in adjustPendingWork.values { item.cancel() }
        adjustPendingWork.removeAll()
        adjustPendingValues.removeAll()
        HUDWindow.shared.endCountdown()
    }

    // MARK: - 画面

    func setMode(_ value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        let name = RegisterMap.modeNames[value] ?? "\(value)"
        currentValues["picture_mode"] = String(value)
        log("模式: \(name)")
        reconcileCrosshairModeState()
        runPictureChange(delay: 1.5) {
            self.adb.settingsPut("picture_mode", String(value))
            DispatchQueue.main.async {
                self.scheduleCoalescedRefresh(delay: 1.2, pages: ["picture"])
            }
        }
    }

    func resetCurrentMode() {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        let mode = intValue("picture_mode", default: -1)
        guard let name = RegisterMap.modeNames[mode] else { log("无法获取当前模式"); return }
        log("恢复 \(name) 模式默认设置...")
        runPictureChange(delay: 3.2) {
            self.adb.jniSet(key: "g_fusion_picture__pic_reset_def_bypicmode", value: "0")
            self.adb.refreshPq()
            DispatchQueue.main.async {
                self.scheduleCoalescedRefresh(delay: 3, pages: ["picture"])
            }
        }
    }

    /// 通用滑条：写 JNI + settings，然后 refresh_pq。
    func setPictureSlider(title: String, value: Int, jniKey: String?, settingsKeys: [String]) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        log("\(title): \(value)")
        runPictureChange {
            if let jniKey {
                self.adb.jniSet(key: jniKey, value: String(value))
                self.adb.refreshPq()
            }
            for k in settingsKeys { self.adb.settingsPut(k, String(value)) }
        }
    }

    func setColorTemp(_ sv: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        let jv = RegisterMap.colorTempToMtk[sv] ?? 0
        let names: [Int: String] = [0: "冷色", 1: "标准", 2: "暖色", 8: "原色", 3: "自定义"]
        currentValues["picture_color_temperature"] = String(sv)
        log("色温: \(names[sv] ?? "\(sv)")")
        runPictureChange {
            self.adb.jniSet(key: "g_video__clr_temp", value: String(jv))
            self.adb.settingsPut("picture_color_temperature", String(sv))
            self.adb.refreshPq()
        }
    }

    func setColorGain(title: String, settingsKey: String, jniKey: String, value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        func gain(_ key: String) -> Int {
            let v = (settingsKey == key) ? value : intValue(key, default: 1024)
            return max(524, min(1524, v))
        }
        let red = gain("picture_red_gain")
        let green = gain("picture_green_gain")
        let blue = gain("picture_blue_gain")

        currentValues["picture_color_temperature"] = "3"
        currentValues["picture_red_gain"] = String(red)
        currentValues["picture_green_gain"] = String(green)
        currentValues["picture_blue_gain"] = String(blue)
        log("\(title): \(value)")

        runPictureChange {
            self.adb.jniSet(key: "g_video__clr_temp", value: String(RegisterMap.colorTempToMtk[3] ?? 0))
            self.adb.settingsPut("picture_color_temperature", "3")
            self.adb.setColorGains(red: String(red), green: String(green), blue: String(blue))
            self.adb.settingsPut("picture_red_gain", String(red))
            self.adb.settingsPut("picture_green_gain", String(green))
            self.adb.settingsPut("picture_blue_gain", String(blue))
            self.adb.refreshPq()
        }
    }

    func setLocalDimming(_ value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        currentValues["picture_local_dimming"] = String(value)
        log("精密控光: \(["关", "低", "中", "高"][value])")
        if hdrMemoryEnabled, !memoriesSuspendedByPreset, let state = hdrLastState {
            var m = localDimmingMemory()
            m[state ? "hdr" : "sdr"] = value
            saveLocalDimmingMemory(m)
        }
        runPictureChange {
            self.adb.jniSet(key: "g_video__vid_local_dimming", value: String(value))
            self.adb.settingsPut("picture_local_dimming", String(value))
            self.adb.settingsPut("tv_picture_video_local_dimming", String(value))
            self.adb.refreshPq()
        }
    }

    /// 自动调整亮度（光感）开关。
    ///
    /// 这个开关有**两份状态**，必须都写：`tv_picture_light_sensor` 是
    /// MiBackLightManager 真正监听的（功能立即生效），MTK 的
    /// `g_video__light_sensor_switch` 才是设置菜单显示的值。只写前者功能会生效，
    /// 但菜单显示不同步。
    ///
    /// 注意这只是**结果状态等价**，不是过程等价：照抄的是某一次拨动的写入快照，
    /// 没有走 App 层的 setLightSensor() 判断，也不触发菜单的埋点上报。
    ///
    /// 菜单手拨「开」还会顺带写三个 DBC（动态背光）的值；本机未启用那套功能
    /// （没有 mitv.settings.backlight.dbc 功能位），写入效果**未经验证**，故不写。
    ///
    /// 两处偏离本模块其他 JNI 写入的惯例，都是有意的：`upd` 传 1（其余处用默认的
    /// 3），这是实测手拨菜单抓到的值；不跟 `refreshPq()`，光感由 ContentObserver
    /// 立即生效，菜单路径里没有这一步。
    func setLightSensor(_ on: Bool) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        let value = on ? 1 : 0
        currentValues["tv_picture_light_sensor"] = String(value)
        log("自动调整亮度: \(on ? "开" : "关")")
        runPictureChange {
            // settings put 必须由 adb shell 执行（shell 持有 WRITE_SECURE_SETTINGS）；
            // 塞进 service call TvService 会被 tvservice 的 uid 静默拒绝 —— 所以这两条
            // 是两次独立调用，不要"优化"成一条 TvService 命令。
            self.adb.jniSet(key: "g_video__light_sensor_switch", value: String(value), upd: 1)
            self.adb.settingsPut("tv_picture_light_sensor", String(value))
        }
    }

    func setHdrToneMapping(_ uiValue: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        guard let mtk = RegisterMap.hdrToneMappingUIToMtk[uiValue] else { return }
        let names: [Int: String] = [0: "HGiG", 1: "层次", 2: "动态", 3: "明亮"]
        currentValues["settings_display_hdr_color_tone"] = String(uiValue)
        log("HDR 色调映射: \(names[uiValue] ?? "\(uiValue)")")
        runPictureChange {
            self.adb.hdrToneMapping(String(mtk))
            self.adb.settingsPut("picture_hdr_tone_mapping", String(mtk))
            self.adb.settingsPut("settings_display_hdr_color_tone", String(uiValue))
            self.adb.refreshPq()
        }
    }

    func setDynamicDefinition(_ value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        currentValues["picture_dynamic_definition"] = String(value)
        log("动态清晰度: \(["关", "低", "中", "高"][value])")
        runPictureChange {
            self.adb.jniSet(key: "g_video__vid_insert_black", value: String(value))
            self.adb.settingsPut("picture_dynamic_definition", String(value))
            self.adb.refreshPq()
        }
    }

    func setResponseTime(_ value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        currentValues["picture_response_time"] = String(value)
        log("响应时间: \(["", "普通", "快速", "高速"][value])")
        runPictureChange {
            self.adb.jniSet(key: "g_video__vid_od_response_time", value: String(value))
            self.adb.settingsPut("picture_response_time", String(value))
            self.adb.refreshPq()
        }
    }

    func setGamut(_ value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        let names: [Int: String] = [0: "自动", 3: "sRGB", 6: "DCI-P3", 4: "Adobe RGB", 5: "BT2020", 7: "BT709"]
        currentValues["tv_picture_advanced_video_color_space"] = String(value)
        log("色域: \(names[value] ?? "\(value)")")
        runPictureChange {
            self.adb.jniSet(key: "g_video__vid_gamut_mapping_mode", value: String(value))
            self.adb.settingsPut("tv_picture_advanced_video_color_space", String(value))
            self.adb.settingsPut("tv_picture_video_color_space", String(value))
            self.adb.refreshPq()
        }
    }

    // MARK: - 游戏

    func setGameFeature(key: String, value: Int, message: String) {
        guard isConnected else { return log("未连接") }
        currentValues[key] = String(value)
        log(message)
        runBackground {
            self.adb.settingsPut(key, String(value))
            self.adb.refreshPq()
        }
    }

    func setCrosshair(_ value: Int) {
        guard isConnected else { return log("未连接") }
        // 用户在游戏模式下的主动选择同步进记忆：关掉即清记忆（避免下次回到
        // 游戏模式又被自动还原回来），选了样式则更新记忆值。
        if value == 0 {
            clearCrosshairMemory()
        } else {
            saveCrosshairMemory(value)
        }
        currentValues["front_sight_index"] = String(value)
        log("准星: \(value == 0 ? "关" : "\(value)")")
        runBackground {
            self.adb.settingsPut("front_sight_index", String(value))
            // 游戏模式下重触发让准星生效（移植自 _fs -> _set_game_feature(retrigger=True)）
            let mode = self.adb.settingsGet("picture_mode")
            if RegisterMap.isGamePictureMode(mode) {
                self.adb.settingsPut("picture_mode", "14")
                Thread.sleep(forTimeInterval: 0.5)
                self.adb.settingsPut("picture_mode", "10")
            } else {
                self.adb.refreshPq()
            }
        }
    }

    func setMode320(_ on: Bool) {
        guard isConnected else { return log("未连接") }
        currentValues["mode_320"] = on ? "1" : "0"
        log("320Hz: \(on ? "开" : "关")")
        runBackground {
            let src = self.adb.settingsGet("mitv.tvplayer.hdmi.last.source")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if src == "29" || src == "30" {
                self.adb.jniSet(key: "g_fusion_picture__dp_edid_version", value: on ? "3" : "2")
            } else {
                self.adb.jniSet(key: "g_fusion_picture__hdmi_edid_version", value: on ? "6" : "1")
            }
            self.adb.refreshPq()
            self.refreshAfterEdidChange()
        }
    }

    /// 切换 FreeSync。当前状态从设备现读 —— 不能用缓存的 currentValues["freesync"]，
    /// 那个值来自 JNI 回读，读取偶发失败会把它留在 0，于是每次都算出"要开启"，
    /// 表现就是按了没反应。
    func toggleFreesync() {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        runBackground {
            let src = self.adb.settingsGet("mitv.tvplayer.hdmi.last.source")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let isDP = (src == "29" || src == "30")
            let key = isDP ? "g_video__dp_adaptive_sync" : "g_video__freesync_switch"
            let raw = Int(self.adb.jniBatchGet(keys: [key])[key] ?? "") ?? -1
            let isOn = isDP ? (raw == 1) : (raw == 3)
            DispatchQueue.main.async { self.setFreesync(!isOn) }
        }
    }

    func setFreesync(_ on: Bool) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }
        currentValues["freesync"] = on ? "1" : "0"
        log("FreeSync: \(on ? "开" : "关")")

        let useMemory = freesyncMemoryEnabled && !memoriesSuspendedByPreset
        runPictureChange(delay: 1.8) {
            // ── 先读设备真实状态，再决定要不要记录 / 还原 ──
            // 不能用缓存里的 currentValues["freesync"]：它来自 JNI 回读，
            // 而 JNI 读取偶发失败会把缓存留在 0。于是「从关到开才记录」会误判成
            // 「本来就是关的」→ 记录下已被上一次 FreeSync 改过的模式；
            // 「从开到关才还原」也会误判成「本来就是关的」→ 直接跳过还原。
            let src = self.adb.settingsGet("mitv.tvplayer.hdmi.last.source")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let isDP = (src == "29" || src == "30")
            let key = isDP ? "g_video__dp_adaptive_sync" : "g_video__freesync_switch"
            let onRaw = isDP ? "1" : "3"
            let before = Int(self.adb.jniBatchGet(keys: [key])[key] ?? "") ?? -1
            let wasOn = isDP ? (before == 1) : (before == 3)

            var restoreMode: Int? = nil
            if useMemory {
                if on && !wasOn {
                    // 画面模式同样现读，避免缓存过期把游戏模式记成"开启前"
                    let mode = Int(self.adb.settingsGet("picture_mode")
                        .trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
                    // 只记「标准 / 游戏 / 电影」这三个标准模式。
                    // Dolby Vision、游戏子模式（SDR 游戏 RPG 之类）这些不是用户手动选的档位，
                    // 记下来再还原会很突兀 —— 而且它们在 FreeSync 开关时本来就会被自动切换。
                    if RegisterMap.modeNames[mode] != nil {
                        UserDefaults.standard.set(mode, forKey: "freesync_previous_mode")
                        DispatchQueue.main.async {
                            self.log("FreeSync 模式记忆: 记录开启前模式 \(RegisterMap.modeNames[mode] ?? "\(mode)")")
                        }
                    } else {
                        UserDefaults.standard.removeObject(forKey: "freesync_previous_mode")
                        DispatchQueue.main.async {
                            self.log("FreeSync 模式记忆: 当前是 \(RegisterMap.sceneNames[mode] ?? "未知模式")，非标准模式不记录")
                        }
                    }
                } else if !on && wasOn {
                    let saved = UserDefaults.standard.integer(forKey: "freesync_previous_mode")
                    restoreMode = RegisterMap.modeNames[saved] != nil ? saved : nil
                }
            }

            self.adb.jniSet(key: key, value: on ? onRaw : "0")
            // 关闭时先切回原先的画面模式，再 refresh_pq（顺序与原版一致）
            if !on, let restoreMode {
                self.adb.settingsPut("picture_mode", String(restoreMode))
            }
            self.adb.refreshPq()

            DispatchQueue.main.async {
                if !on, let restoreMode {
                    self.currentValues["picture_mode"] = String(restoreMode)
                    self.log("FreeSync 模式记忆: 已切回 \(RegisterMap.sceneNames[restoreMode] ?? "\(restoreMode)")")
                }
            }
            self.refreshAfterEdidChange()
        }
    }

    private func refreshAfterEdidChange() {
        DispatchQueue.main.async {
            self.scheduleCoalescedRefresh(delay: 1.5, pages: ["picture", "game"])
        }
    }

    // MARK: - 信号源

    func setSource(_ value: Int) {
        guard isConnected else { return log("未连接") }
        let name = RegisterMap.sourceNames[value] ?? "未知"
        currentValues["mitv.tvplayer.hdmi.last.source"] = String(value)
        activeSource = name
        log("信号源: \(name)")
        runBackground {
            self.adb.shell("am force-stop com.xiaomi.mitv.tvplayer")
            self.adb.shell("am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input \(value) -f 0x10000000")
            self.adb.refreshPq()
        }
    }

    // MARK: - 屏幕灯

    func setLightMode(_ value: Int) {
        currentValues["atmosphere_light_switcher_pm2"] = String(value)
        commitScreenLight("屏幕灯模式: \(RegisterMap.lightModeNames[value] ?? "\(value)")")
    }

    func setLightIllumination(_ uiValue: Int) {
        let raw = max(0, min(14, uiValue - 1))
        currentValues["atmosphere_light_illumination"] = String(raw)
        commitScreenLight("屏幕灯亮度挡位: \(uiValue)")
    }

    func setLightColorTemp(_ value: Int) {
        currentValues["atmosphere_light_switcher_pm2"] = "0"
        currentValues["atmosphere_light_color_temp"] = String(value)
        commitScreenLight("屏幕灯色温: \(RegisterMap.lightColorTempNames[value] ?? "\(value)")")
    }

    func setLightColor(_ value: Int) {
        currentValues["atmosphere_light_switcher_pm2"] = "2"
        currentValues["atmosphere_light_color_value"] = String(value)
        commitScreenLight("屏幕灯颜色: \(RegisterMap.lightColorNames[value] ?? "\(value)")")
    }

    private func commitScreenLight(_ message: String) {
        guard isConnected else { return log("未连接") }
        let mode = intValue("atmosphere_light_switcher_pm2", default: 4)
        let illumination = intValue("atmosphere_light_illumination", default: 9)
        let colorTemp = intValue("atmosphere_light_color_temp", default: 1)
        let colorValue = intValue("atmosphere_light_color_value", default: 0)
        log(message)
        runBackground {
            self.adb.settingsPut("atmosphere_light_switcher_pm2", String(mode))
            self.adb.settingsPut("atmosphere_light_illumination", String(illumination))
            self.adb.settingsPut("atmosphere_light_color_temp", String(colorTemp))
            self.adb.settingsPut("atmosphere_light_color_value", String(colorValue))
            switch mode {
            case 0: self.adb.colorfulLed(action: "lighting", args: [String(illumination), String(colorTemp)])
            case 1: self.adb.colorfulLed(action: "ambient")
            case 2: self.adb.colorfulLed(action: "solid", args: [String(illumination), String(colorValue)])
            case 3: self.adb.colorfulLed(action: "cycle")
            default: self.adb.colorfulLed(action: "off")
            }
        }
    }

    // MARK: - 遥控器

    func key(_ code: String) {
        guard isConnected else { return log("未连接") }
        log("按键: \(code)")
        runBackground { self.adb.keyevent(code) }
    }

    // MARK: - 日志落盘 / 导出

    func toggleLogFile(_ enabled: Bool) {
        logToFileEnabled = enabled
        if enabled {
            let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("MimonitorToolbox/logs")
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let url = dir.appendingPathComponent("log_\(Self.fileTimeFormatter.string(from: Date())).txt")
            logFileURL = url
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            logFileHandle = try? FileHandle(forWritingTo: url)
            log("本地日志记录: 开启")
        } else {
            try? logFileHandle?.close()
            logFileHandle = nil
            log("本地日志记录: 关闭")
        }
    }

    func exportLog(to url: URL) {
        if let logFileURL, FileManager.default.fileExists(atPath: logFileURL.path) {
            try? FileManager.default.copyItem(at: logFileURL, to: url)
        } else {
            try? logLines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
        }
        log("日志已导出: \(url.path)")
    }

    func openLogDir() {
        if let logFileURL {
            NSWorkspace.shared.activateFileViewerSelecting([logFileURL])
        } else {
            let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("MimonitorToolbox/logs")
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            NSWorkspace.shared.open(dir)
        }
    }

    // MARK: - 信号源轮询

    /// 信号源页停留期间每 2 秒轮询一次：显示器可能被遥控器/按键切走，
    /// 这个页面要反映实时状态（对应原版 _start_source_polling）。
    func startSourcePolling() {
        guard isConnected else { return }
        sourcePollArmed = true
        DispatchQueue.main.async {
            self.sourcePollTimer?.invalidate()
            self.sourcePollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
                self?.pollSourceState()
            }
        }
    }

    func stopSourcePolling() {
        sourcePollArmed = false
        _ = pageRefreshIntent.begin("sourcePoll")
        DispatchQueue.main.async {
            self.sourcePollTimer?.invalidate()
            self.sourcePollTimer = nil
        }
    }

    private func pollSourceState() {
        guard sourcePollArmed, isConnected else { stopSourcePolling(); return }
        let connectionRequest = connectionIntent.generation
        let pollRequest = pageRefreshIntent.begin("sourcePoll")
        runBackground {
            let raw = self.adb.settingsGet("mitv.tvplayer.hdmi.last.source")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard let n = Int(raw), let name = RegisterMap.sourceNames[n] else { return }
            DispatchQueue.main.async {
                guard self.sourcePollArmed, self.isConnected,
                      self.connectionIntent.isCurrent(connectionRequest),
                      self.pageRefreshIntent.isCurrent("sourcePoll", pollRequest) else { return }
                self.currentValues["mitv.tvplayer.hdmi.last.source"] = String(n)
                if self.activeSource != name {
                    self.activeSource = name
                    self.log("信号源变化: \(name)")
                }
            }
        }
    }

    // MARK: - 网络诊断

    /// 逐项检查连接链路的每一环，结果打到日志区。
    /// 用于区分「网段不对」「显示器无线调试没开」「权限被拦」这几类问题。
    func runDiagnostics() {
        log("===== 网络诊断 =====")
        runBackground {
            var lines: [String] = []
            let fm = FileManager.default

            lines.append("adb 路径: \(self.adb.adbPath)")
            lines.append("adb 可执行: \(fm.isExecutableFile(atPath: self.adb.adbPath) ? "✅" : "❌ 找不到 adb")")
            lines.append("adb server 端口: \(self.adb.serverPort)")

            let subnets = NetworkScan.localIPv4Subnets()
            lines.append("本机网段: \(subnets.isEmpty ? "❌ 未识别" : "✅ " + subnets.joined(separator: ", "))")

            let ip = self.ipInput.trimmingCharacters(in: .whitespaces)
            if ip.isEmpty {
                lines.append("⚠️ 未填写显示器 IP，跳过连通性测试")
            } else {
                let sameSubnet = subnets.contains { ip.hasPrefix($0 + ".") }
                lines.append("与目标同网段: \(sameSubnet ? "✅" : "❌ 不在同一网段")")
                lines.append("ping \(ip): \(Self.ping(ip) ? "✅ 通" : "❌ 不通")")
                let tcp = NetworkScan.isTcpOpen(host: ip, port: 5555, timeout: 2.0)
                lines.append("TCP \(ip):5555: \(tcp ? "✅ 开放" : "❌ 连不上")")
                if !tcp {
                    lines.append("  → 设备在线但端口不通，通常是显示器的「无线调试」没开")
                }
                if self.isConnected {
                    lines.append("adb 设备状态: \(self.adb.deviceState())")
                }
            }

            lines.append("提示: 三者全 ❌ 且同网段 → 多半是本 app 缺「本地网络」权限")
            lines.append("      到 系统设置 → 隐私与安全性 → 本地网络，打开本应用开关")
            lines.append("提示: 不同网段 → 显示器与 Mac 不在同一 Wi-Fi/VLAN")
            lines.append("===== 诊断结束 =====")

            DispatchQueue.main.async { lines.forEach { self.log($0) } }
        }
    }

    private static func ping(_ host: String) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/sbin/ping")
        process.arguments = ["-c", "1", "-W", "1000", host]
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        do { try process.run() } catch { return false }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    // MARK: - APK 安装 / ADB 命令行

    func installApk(_ url: URL) {
        guard isConnected else { log("请先连接显示器"); return }
        log("正在安装: \(url.lastPathComponent) ...")
        runBackground {
            let r = self.adb.installApk(url.path)
            DispatchQueue.main.async {
                if r.contains("Success") {
                    self.log("APK 安装成功")
                } else {
                    self.log("APK 安装失败: \(r.trimmingCharacters(in: .whitespacesAndNewlines))")
                }
            }
        }
    }

    /// 进入显示器上的 adb shell。
    func openAdbShell() {
        guard isConnected else { log("请先连接显示器"); return }
        openTerminal(title: "Mimonitor · Shell",
                     command: "adb -s \(adb.serial) shell")
    }

    /// 打开一个配好环境的终端：PATH 里有 adb，端口也设好了，直接敲 `adb devices` 就能用。
    ///
    /// **故意不要求已连接** —— 连不上的时候正是最需要这个终端的时候。
    /// 以前这里跑的是 `adb shell`（没带 -s），设备多于一个时报
    /// "more than one device/emulator"，而且那个终端里也没有 adb 可用。
    func openAdbCmd() {
        openTerminal(title: "Mimonitor · ADB", command: "")
    }

    private func openTerminal(title: String, command: String) {
        let adbDir = (adb.adbPath as NSString).deletingLastPathComponent
        // 把 adb 所在目录塞进 PATH，用户才能直接敲 adb
        let setup = "export PATH=\"\(adbDir):$PATH\"; export ANDROID_ADB_SERVER_PORT=\(adb.serverPort)"
        let body = command.isEmpty ? "\(setup); clear" : "\(setup); clear; \(command)"

        let script = """
        tell application "Terminal"
            -- 已经开过就切过去，不再新开一个 —— 之前点一次开一个，很快就堆一堆窗口
            repeat with w in windows
                repeat with t in tabs of w
                    if (custom title of t) is "\(title)" then
                        set selected tab of w to t
                        set index of w to 1
                        activate
                        return
                    end if
                end repeat
            end repeat
            set newTab to do script "\(Self.appleScriptEscape(body))"
            set custom title of newTab to "\(title)"
            activate
        end tell
        """

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        task.arguments = ["-e", script]
        do {
            try task.run()
            log("已在终端打开 ADB 会话（\(title)）")
        } catch {
            log("打开终端失败: \(error.localizedDescription)")
        }
    }

    /// 套进 AppleScript 字符串字面量前的转义
    private static func appleScriptEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    // MARK: - 4K UI

    /// 检测显示器当前是否处于 4K UI 模式（移植自原版 _check_4k_state）。
    /// Override size 存在且任一边大于 1920x1080 即视为已开启。
    func check4KState() {
        guard isConnected else { return }
        runBackground {
            let override = self.adb.getOverrideSize()
            let density = self.adb.getOverrideDensity()
            DispatchQueue.main.async {
                let is4k = override.map { $0.width > 1920 || $0.height > 1080 } ?? false
                let changed = (self.is4KUI != is4k)
                if changed { self.is4KUI = is4k }
                // 只在状态变化时记日志：工具页每次出现都会检测一遍，
                // 无脑记录会把日志刷满
                if changed || override == nil {
                    let sizeText = override.map { "\($0.width)×\($0.height)" } ?? "无 Override（面板原生）"
                    let densityText = density.map { "，DPI \($0)" } ?? ""
                    self.log("4K UI 检测: \(is4k ? "已开启" : "未开启")（\(sizeText)\(densityText)）")
                }
            }
        }
    }

    func toggle4K(_ enabled: Bool) {
        guard isConnected else { return log("请先连接显示器") }
        log(enabled ? "已设置 4K UI (3840×2160 / DPI 640)，显示器即将重启..." : "已恢复 1080p UI，显示器即将重启...")
        runBackground {
            if enabled {
                self.adb.shell("wm size 3840x2160")
                self.adb.shell("wm density 640")
            } else {
                self.adb.shell("wm size 1920x1080")
                self.adb.shell("wm density 320")
            }
            self.adb.shell("reboot")
        }
    }

    // MARK: - ADB 保活守护

    func checkGuardian() {
        guard isConnected else { return log("请先连接显示器") }
        guardianStatus = "状态：正在检测..."
        runBackground {
            let s = self.readGuardianStatus()
            DispatchQueue.main.async {
                self.guardianStatus = s.summary
                // 把结论一起写进日志：只写"检测完成"的话，导出日志看不到守护到底正不正常
                self.log("ADB 保活守护检测完成 —— \(s.summary)")
            }
        }
    }

    func deployGuardian() {
        guard isConnected else { return log("请先连接显示器") }
        guard let apk = AdbClient.guardianApkPath() else { log("找不到保活 APK（assets/adb_guardian/adbguardian-signed.apk）"); return }
        guardianStatus = "状态：正在部署/修复..."
        log("正在部署 ADB 保活守护...")
        runBackground {
            let r = self.adb.installApk(apk)
            if r.contains("Success") {
                self.adb.shell("pm grant \(Guardian.package) android.permission.WRITE_SECURE_SETTINGS 2>/dev/null || true")
                self.adb.shell("cmd deviceidle whitelist +\(Guardian.package) 2>/dev/null || true")
                self.enableGuardianAccessibility()
                self.startGuardianCommands()
                Thread.sleep(forTimeInterval: 3)
                _ = self.adb.reconnect()
                let s = self.readGuardianStatus()
                DispatchQueue.main.async {
                    self.guardianStatus = s.summary
                    self.log("ADB 保活守护部署完成")
                }
            } else {
                DispatchQueue.main.async {
                    self.guardianStatus = "状态：部署失败"
                    self.log("ADB 保活守护部署失败: \(r.trimmingCharacters(in: .whitespacesAndNewlines))")
                }
            }
        }
    }

    func startGuardian() {
        guard isConnected else { return log("请先连接显示器") }
        guardianStatus = "状态：正在启动保活..."
        runBackground {
            self.enableGuardianAccessibility()
            self.startGuardianCommands()
            Thread.sleep(forTimeInterval: 2)
            let s = self.readGuardianStatus()
            DispatchQueue.main.async {
                self.guardianStatus = s.summary
                self.log("ADB 保活守护已启动")
            }
        }
    }

    private func enableGuardianAccessibility() {
        let current = adb.shell("settings get secure enabled_accessibility_services 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let services: String
        if current.isEmpty || current == "null" {
            services = Guardian.accessibility
        } else if current.split(separator: ":").map(String.init).contains(Guardian.accessibility) {
            services = current
        } else {
            services = "\(current):\(Guardian.accessibility)"
        }
        adb.shell("settings put secure enabled_accessibility_services '\(services)'")
        adb.shell("settings put secure accessibility_enabled 1")
    }

    private func startGuardianCommands() {
        adb.shell("am start -n \(Guardian.mainActivity) >/dev/null")
        adb.shell("am broadcast -a \(Guardian.package).ACTION_KEEP_ALIVE -p \(Guardian.package) >/dev/null")
    }

    private func readGuardianStatus() -> GuardianStatus {
        let acc = adb.shell("settings get secure enabled_accessibility_services 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let path = adb.shell("pm path \(Guardian.package) 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let pid = adb.shell("pidof \(Guardian.package) 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let mask = adb.shell("getprop persist.appcontrol_w_mask 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let adbEnabled = adb.shell("settings get global adb_enabled 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let wifiEnabled = adb.shell("settings get global adb_wifi_enabled 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let servicePort = adb.shell("getprop service.adb.tcp.port 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let persistPort = adb.shell("getprop persist.adb.tcp.port 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let adbd = adb.shell("getprop init.svc.adbd 2>/dev/null")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let stopped = adb.shell("dumpsys package \(Guardian.package) 2>/dev/null | grep stopped=true | head -n 1").isEmpty == false
        return GuardianStatus(
            installed: !path.isEmpty && path.hasPrefix("package:"),
            pid: pid,
            accessibility: acc.contains(Guardian.accessibility),
            adbEnabled: adbEnabled == "1",
            adbWifiEnabled: wifiEnabled == "1",
            servicePort: servicePort,
            persistPort: persistPort,
            adbd: adbd,
            mask: mask,
            stopped: stopped
        )
    }

    // MARK: - HDR / SDR 分区控光记忆

    func toggleHdrMemory(_ enabled: Bool) {
        guard let enabled = presetControlPolicy.memoryPreferenceChange(requested: enabled) else { return }
        hdrMemoryEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: "hdr_sdr_local_dimming_enabled")
        hdrLastState = nil
        log("HDR/SDR 分区控光记忆: \(enabled ? "开启" : "关闭")")
        updateHdrMemoryStatus()
        if enabled { pollHdrState(force: true) }
    }

    /// 最近一次判定 HDR 用的是哪条信号，用于界面说明
    private(set) var hdrStateSource = ""

    func pollHdrState(force: Bool = false) {
        let host = HDRDetector.hostSideHDR()
        let monitor = HDRDetector.monitorSideHDR(pictureMode: currentValues["picture_mode"])
        let state = host || monitor
        hdrStateSource = host && monitor ? "主机+显示器"
            : (host ? "主机 EDR" : (monitor ? "显示器模式" : ""))
        let prev = hdrLastState
        hdrLastState = state
        updateHdrMemoryStatus()

        let changed = (prev != nil && prev != state)

        // HDR 切换后必须重读画面页：显示器侧的判据用的是缓存的 picture_mode，
        // 不重读的话它会一直停在上一次的值（实测关了 HDR 后仍显示「HDR（显示器模式）」）。
        // 同时 HDR 切换会连带换掉一整套画面参数，本来也该重新回读。
        if changed || force {
            schedulePictureRefreshAfterHdrChange()
        }

        guard hdrMemoryEnabled, !memoriesSuspendedByPreset else { return }
        if force || changed {
            applyHdrMemory(state: state)
        }
    }

    /// 移植自原版 _schedule_picture_refresh_after_hdr_change。
    /// 开了记忆时要多等一会儿 —— 精密控光的下发要 2~3 秒才落到设备上，
    /// 太早回读会读到旧值。
    private func schedulePictureRefreshAfterHdrChange() {
        let delay = hdrMemoryEnabled ? 2.5 : 0.4
        scheduleCoalescedRefresh(delay: delay, pages: ["picture"])
    }

    private func applyHdrMemory(state: Bool) {
        guard isConnected, !memoriesSuspendedByPreset else { return }
        let bucket = state ? "hdr" : "sdr"
        guard let value = localDimmingMemory()[bucket] else { return }

        runBackground {
            // 现读设备真实值。用缓存的 currentValues 会因 JNI 读取偶发失败而过期，
            // 导致该下发时不下发、或重复下发。
            let raw = self.adb.settingsGet("picture_local_dimming")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard Int(raw) != value else { return }
            DispatchQueue.main.async {
                guard self.isConnected, !self.memoriesSuspendedByPreset else { return }
                let valueName = ["关", "低", "中", "高"][value]
                self.log("\(state ? "HDR" : "SDR") 精密控光记忆: \(valueName)")
                // 原版这里也会弹悬浮提示（_apply_hdr_memory_for_current_state）
                HUDWindow.shared.show(title: "\(state ? "HDR" : "SDR") 精密控光", value: valueName)
                self.setLocalDimming(value)
            }
        }
    }

    private func localDimmingMemory() -> [String: Int] {
        guard let data = UserDefaults.standard.data(forKey: "local_dimming_memory"),
              let m = try? JSONDecoder().decode([String: Int].self, from: data) else { return [:] }
        return m
    }

    private func saveLocalDimmingMemory(_ m: [String: Int]) {
        if let data = try? JSONEncoder().encode(m) {
            UserDefaults.standard.set(data, forKey: "local_dimming_memory")
        }
        updateHdrMemoryStatus()
    }

    func updateHdrMemoryStatus() {
        let stateText = hdrLastState == nil ? "未知" : (hdrLastState! ? "HDR" : "SDR")
        let m = localDimmingMemory()
        let sdrText = m["sdr"].map { ["关", "低", "中", "高"][$0] } ?? "--"
        let hdrText = m["hdr"].map { ["关", "低", "中", "高"][$0] } ?? "--"
        let sourceText = hdrStateSource.isEmpty ? "" : "（\(hdrStateSource)）"
        let status = memoriesSuspendedByPreset ? "已锁定（预设生效或切换中）" : (hdrMemoryEnabled ? "已开启" : "已关闭")
        let text = "分区控光记忆：\(status)，当前信号：\(stateText)\(sourceText)，记忆模式：SDR=\(sdrText) / HDR=\(hdrText)"
        // 只在内容真的变了才赋值：@Published 每次赋值都会让整个界面重绘，
        // 而 HDR 状态是 3 秒轮询一次，无脑赋值等于每 3 秒全量重绘一次。
        if hdrMemoryStatusText != text { hdrMemoryStatusText = text }
    }

    // MARK: - FreeSync Pro 模式记忆

    func toggleFreesyncMemory(_ enabled: Bool) {
        guard let enabled = presetControlPolicy.memoryPreferenceChange(requested: enabled) else { return }
        freesyncMemoryEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: "freesync_mode_memory_enabled")
        log("FreeSync Pro 模式记忆: \(enabled ? "开启" : "关闭")")
        updateFreesyncMemoryStatus()
    }

    func updateFreesyncMemoryStatus() {
        let saved = UserDefaults.standard.integer(forKey: "freesync_previous_mode")
        let savedText = saved == 0 ? "--" : (RegisterMap.sceneNames[saved] ?? "\(saved)")
        let status = memoriesSuspendedByPreset ? "已锁定（预设生效或切换中）" : (freesyncMemoryEnabled ? "已开启" : "已关闭")
        let text = "模式记忆：\(status)，记录模式：\(savedText)"
        if freesyncMemoryStatusText != text { freesyncMemoryStatusText = text }
    }

    // MARK: - 准星模式联动
    //
    // front_sight_index 是设备级 settings，与 picture_mode 解耦：不主动清理的话，
    // 切到标准/电影等模式后准星依然显示。这里在离开游戏模式时记住并隐藏，
    // 回到游戏模式时还原。

    private var crosshairMemory: Int? {
        guard UserDefaults.standard.object(forKey: "crosshair_memory") != nil else { return nil }
        return UserDefaults.standard.integer(forKey: "crosshair_memory")
    }

    private func saveCrosshairMemory(_ value: Int) {
        UserDefaults.standard.set(value, forKey: "crosshair_memory")
        updateCrosshairModeStatus()
    }

    private func clearCrosshairMemory() {
        UserDefaults.standard.removeObject(forKey: "crosshair_memory")
        updateCrosshairModeStatus()
    }

    func toggleCrosshairGameModeOnly(_ enabled: Bool) {
        crosshairGameModeOnly = enabled
        UserDefaults.standard.set(enabled, forKey: "crosshair_game_mode_only")
        log("准星仅在游戏模式下生效: \(enabled ? "开启" : "关闭")")
        updateCrosshairModeStatus()
        if enabled { reconcileCrosshairModeState() }
    }

    private func updateCrosshairModeStatus() {
        let text: String
        if !crosshairGameModeOnly {
            text = "已关闭，准星在所有模式下都生效"
        } else if let memory = crosshairMemory {
            text = "已开启，离开游戏模式时自动隐藏（已记忆准星 \(memory)）"
        } else {
            text = "已开启，离开游戏模式时自动隐藏（暂无记忆）"
        }
        if crosshairModeStatusText != text { crosshairModeStatusText = text }
    }

    /// 按当前画面模式纠正准星。由页面数据刷新驱动（含模式切换后的自动刷新），
    /// 没有周期轮询——用显示器遥控器改模式时，会在下次刷新数据时纠正。
    ///
    /// 隐藏是「只要在非游戏模式看到准星就清掉」；还原只在「模式跃迁进入游戏
    /// 模式」时做一次，避免用户用显示器 OSD 主动关掉准星后又被打开。
    func reconcileCrosshairModeState() {
        guard crosshairGameModeOnly, isConnected else { return }
        guard !crosshairReconcileBusy else { return }
        guard let mode = currentValues["picture_mode"] else { return }
        guard let raw = currentValues["front_sight_index"],
              let crosshair = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return
        }

        let previousMode = crosshairLastMode
        crosshairLastMode = mode

        if RegisterMap.isGamePictureMode(mode) {
            let enteringGameMode = previousMode != nil && !RegisterMap.isGamePictureMode(previousMode)
            if enteringGameMode, crosshair == 0, let memory = crosshairMemory {
                applyCrosshairModeValue(memory, message: "进入游戏模式，已恢复记忆的准星 \(memory)")
            }
            return
        }

        if crosshair != 0 {
            saveCrosshairMemory(crosshair)
            applyCrosshairModeValue(0, message: "离开游戏模式，已隐藏准星（记忆 \(crosshair)）")
        }
    }

    private func applyCrosshairModeValue(_ value: Int, message: String) {
        crosshairReconcileBusy = true
        runBackground {
            self.adb.settingsPut("front_sight_index", String(value))
            self.adb.refreshPq()
            DispatchQueue.main.async {
                self.log(message)
                self.mergeValues(["front_sight_index": String(value)])
                self.updateCrosshairModeStatus()
                // 重入的 reconcile 由 busy 标记挡住，结束后再放开
                self.crosshairReconcileBusy = false
            }
        }
    }

    // MARK: - 开机自启动

    func setAutostart(_ enabled: Bool) {
        let exe = Bundle.main.executablePath ?? CommandLine.arguments.first ?? ""
        Autostart.setEnabled(enabled, executablePath: exe)
        log(enabled ? "已设置开机自启动（下次登录生效）" : "已取消开机自启动")
    }

    // MARK: - 菜单栏快捷项

    /// 已加入菜单栏的条目 id，顺序即菜单显示顺序。持久化到 UserDefaults。
    @Published var menuBarItems: [String] = AppState.loadMenuBarItems() {
        didSet { UserDefaults.standard.set(menuBarItems, forKey: Self.menuBarKey) }
    }

    private static let menuBarKey = "menubar_items"

    private static func loadMenuBarItems() -> [String] {
        guard let saved = UserDefaults.standard.array(forKey: menuBarKey) as? [String] else {
            return MenuBarCatalog.defaultEnabled
        }
        // 过滤掉清单里已经不存在的 id（版本升级后清理）
        return saved.filter { MenuBarCatalog.entry($0) != nil }
    }

    func toggleMenuBarItem(_ id: String) {
        if let idx = menuBarItems.firstIndex(of: id) {
            menuBarItems.remove(at: idx)
        } else {
            menuBarItems.append(id)
        }
    }

    func isMenuBarItemEnabled(_ id: String) -> Bool {
        menuBarItems.contains(id)
    }

    /// 列表拖动排序（SwiftUI List 的 onMove 直接转过来）
    func moveMenuBarItems(from: IndexSet, to: Int) {
        menuBarItems.move(fromOffsets: from, toOffset: to)
    }

    /// 该条目对应的当前值，用于在菜单里给选项打勾
    func menuBarValue(for id: String) -> Int? {
        switch id {
        case "picture_mode":        return intValue("picture_mode", default: -1)
        case "local_dimming":       return intValue("picture_local_dimming", default: -1)
        case "gamut":               return intValue("tv_picture_advanced_video_color_space", default: -1)
        case "color_temp":          return intValue("picture_color_temperature", default: -1)
        case "response_time":       return intValue("picture_response_time", default: -1)
        case "dynamic_definition":  return intValue("picture_dynamic_definition", default: -1)
        case "hdr_tone_mapping":    return intValue("settings_display_hdr_color_tone", default: -1)
        case "source":              return intValue("mitv.tvplayer.hdmi.last.source", default: -1)
        case "freesync":            return intValue("freesync", default: -1)
        case "light_sensor":        return intValue("tv_picture_light_sensor", default: -1)
        case "backlight":           return intValue("picture_backlight", default: -1)
        case "black_level":         return intValue("picture_brightness", default: -1)
        case "contrast":            return intValue("picture_contrast", default: -1)
        case "saturation":          return intValue("picture_saturation", default: -1)
        case "hue":                 return intValue("picture_hue", default: -1)
        case "sharpness":           return intValue("picture_sharpness", default: -1)
        case "light_mode":          return intValue("atmosphere_light_switcher_pm2", default: -1)
        default:                    return nil
        }
    }

    /// 从菜单栏选中一个取值
    func applyMenuBarOption(_ id: String, value: Int) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        switch id {
        case "picture_mode":        setMode(value)
        case "local_dimming":       setLocalDimming(value)
        case "gamut":               setGamut(value)
        case "color_temp":          setColorTemp(value)
        case "response_time":       setResponseTime(value)
        case "dynamic_definition":  setDynamicDefinition(value)
        case "hdr_tone_mapping":    setHdrToneMapping(value)
        case "source":              setSource(value)
        case "freesync":            setFreesync(value == 1)
        case "light_sensor":        setLightSensor(value == 1)
        case "light_mode":          setLightMode(value)
        default: break
        }
    }

    // MARK: - 辅助功能权限

    /// app 回到前台时重新查一次：用户去系统设置勾选后切回来，警告应该自动消失
    func refreshAccessibilityStatus() {
        let trusted = HotkeyManager.isTrusted
        guard accessibilityTrusted != trusted else { return }
        accessibilityTrusted = trusted
        if trusted {
            log("辅助功能权限已授权，正在重新注册全局快捷键")
            applyHotkeys()
        } else {
            log("辅助功能权限已撤销，全局快捷键将失效")
        }
    }

    /// 引导授权：先弹系统框（只能弹一次），同时把设置页打开
    func requestAccessibilityPermission() {
        _ = HotkeyManager.promptForAccessibility()
        HotkeyManager.openAccessibilitySettings()
        log("请在「系统设置 → 隐私与安全性 → 辅助功能」中勾选本应用，然后回到这里")
    }

    // MARK: - 全局快捷键

    private static func loadHotkeys() -> [String: HotkeyConfig] {
        guard let data = UserDefaults.standard.data(forKey: "hotkeys"),
              let v = try? JSONDecoder().decode([String: HotkeyConfig].self, from: data) else { return [:] }
        return v
    }

    private static func loadAdjustHotkeys() -> [AdjustHotkeyConfig] {
        guard let data = UserDefaults.standard.data(forKey: "adjust_hotkeys"),
              let v = try? JSONDecoder().decode([AdjustHotkeyConfig].self, from: data) else { return [] }
        return v
    }

    func saveHotkeys() {
        if let data = try? JSONEncoder().encode(hotkeys) {
            UserDefaults.standard.set(data, forKey: "hotkeys")
        }
        if let data = try? JSONEncoder().encode(adjustHotkeys) {
            UserDefaults.standard.set(data, forKey: "adjust_hotkeys")
        }
        applyHotkeys()
        HotkeyManager.requestAccessibility()
        log("全局快捷键已保存并重新注册（如未生效请在系统设置→隐私与安全性→辅助功能中勾选本应用）")
    }

    private func applyHotkeys() {
        hotkeyManager.clear()
        for (action, cfg) in hotkeys where cfg.isEnabled {
            hotkeyManager.register(id: action, modifierName: cfg.modifier, keyName: cfg.key)
        }
        for (idx, rule) in adjustHotkeys.enumerated() where rule.isEnabled {
            hotkeyManager.register(id: "adjust:\(idx)", modifierName: rule.modifier, keyName: rule.key)
        }
        let started = hotkeyManager.start()
        log("全局快捷键: 注册 \(hotkeyManager.bindingCount) 个，"
            + "监听\(started ? "已启动" : "启动失败")"
            + "（辅助功能权限：\(HotkeyManager.isTrusted ? "已授予" : "未授予")）")
    }

    private func handleHotkeyTrigger(_ id: String) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        if id.hasPrefix("adjust:"), let idx = Int(id.dropFirst("adjust:".count)),
           idx >= 0, idx < adjustHotkeys.count {
            let rule = adjustHotkeys[idx]
            adjustHotkey(param: rule.param, direction: rule.direction, step: rule.step)
            return
        }
        switch id {
        case "picture_mode_cycle": cyclePictureMode()
        case "local_dimming_cycle": cycleLocalDimming()
        case "local_dimming_toggle_off": toggleLocalDimming()
        case "color_space_cycle": cycleColorSpace()
        case "color_temp_cycle": cycleColorTemp()
        case "response_time_cycle": cycleResponseTime()
        case "freesync_toggle": toggleFreesync()
        case "input_source_cycle": cycleSource()
        default: break
        }
    }

    // MARK: - 循环切换（防抖 + 悬浮提示）
    //
    // 对应原版 _cycle_hotkey：连按时先算出下一个值、立刻更新 HUD 预览和界面，
    // 停手 450ms 后才真正下发。否则长按会把命令堆起来 ——
    // JNI 通道单次要 0.5~1 秒，堆几条之后要好几秒才追得上。

    private var cyclePending: [String: (value: Int, work: DispatchWorkItem)] = [:]

    /// 是否启用「松手后生效」的倒计时。
    /// 关掉之后每次按键立即下发 —— 更跟手，但连按会把 ADB 命令堆起来。
    @Published var hotkeyCountdownEnabled = UserDefaults.standard.object(forKey: "hotkey_countdown_enabled") as? Bool ?? true {
        didSet { UserDefaults.standard.set(hotkeyCountdownEnabled, forKey: "hotkey_countdown_enabled") }
    }

    /// 倒计时时长（秒）。HUD 的进度条就是这个时长，两边一起变。
    @Published var hotkeyCountdownSeconds = UserDefaults.standard.object(forKey: "hotkey_countdown_seconds") as? Double ?? 0.8 {
        didSet { UserDefaults.standard.set(hotkeyCountdownSeconds, forKey: "hotkey_countdown_seconds") }
    }

    /// 实际生效前等待多久。0 表示不等待。下限 0.1s 是为了避免设成 0 之后
    /// 退化成"每次按键都立即下发"，那和关掉倒计时没区别了。
    var effectiveHotkeyDelay: TimeInterval {
        hotkeyCountdownEnabled ? max(0.1, hotkeyCountdownSeconds) : 0
    }

    /// 循环切换的通用实现。
    ///
    /// **纯尾部防抖**：每次按键都重新开始一个完整的倒计时，走完才真正下发。
    /// 也就是说"松手后才生效"—— 连按时只有最后一次的值会落到显示器上。
    ///
    /// 曾经用过"前沿 + 尾部"（第一下立刻生效）：单按确实更跟手，但表现为
    /// "慢速按时倒计时根本不出现、连按时倒计时只剩几十毫秒一闪而过"，
    /// 反而让人以为功能坏了。现在统一成纯尾部，倒计时每次都完整走一遍。
    private func cycleStep(actionId: String,
                           label: String,
                           order: [Int],
                           valueKey: String,
                           nameFor: (Int) -> String,
                           apply: @escaping (Int) -> Void) {
        guard isConnected else { return log("未连接") }

        // 基准优先取「上一次连按的预览值」，这样快速连按能连续往前推进
        let base = cyclePending[actionId]?.value ?? intValue(valueKey, default: order[0])
        let index = order.firstIndex(of: base).map { ($0 + 1) % order.count } ?? 0
        let next = order[index]

        currentValues[valueKey] = String(next)

        let delay = effectiveHotkeyDelay
        cyclePending[actionId]?.work.cancel()
        cyclePending[actionId] = nil

        // 关掉倒计时：立即下发，HUD 也不显示进度条
        guard delay > 0 else {
            HUDWindow.shared.show(title: label, value: nameFor(next))
            apply(next)
            return
        }

        HUDWindow.shared.show(title: label, value: nameFor(next), countdown: delay)
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.cyclePending[actionId] = nil
            apply(next)
            HUDWindow.shared.endCountdown()
        }
        cyclePending[actionId] = (next, work)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func cyclePictureMode() {
        cycleStep(actionId: "picture_mode_cycle", label: "画面模式", order: [14, 10, 9],
                  valueKey: "picture_mode",
                  nameFor: { RegisterMap.sceneNames[$0] ?? "\($0)" }) { [weak self] v in
            self?.setMode(v)
        }
    }

    private func cycleLocalDimming() {
        let names = ["关", "低", "中", "高"]
        cycleStep(actionId: "local_dimming_cycle", label: "精密控光", order: [0, 1, 2, 3],
                  valueKey: "picture_local_dimming",
                  nameFor: { names[max(0, min(3, $0))] }) { [weak self] v in
            self?.setLocalDimming(v)
        }
    }

    private func toggleLocalDimming() {
        guard isConnected else { return log("未连接") }
        let names = ["关", "低", "中", "高"]
        let next = intValue("picture_local_dimming", default: 3) == 0 ? 3 : 0
        HUDWindow.shared.show(title: "精密控光", value: names[next])
        setLocalDimming(next)
    }

    private func cycleColorSpace() {
        let names: [Int: String] = [0: "自动", 3: "sRGB", 4: "Adobe RGB",
                                    5: "BT2020", 6: "DCI-P3", 7: "BT709"]
        cycleStep(actionId: "color_space_cycle", label: "色域", order: [0, 3, 6, 4, 5, 7],
                  valueKey: "tv_picture_advanced_video_color_space",
                  nameFor: { names[$0] ?? "\($0)" }) { [weak self] v in
            self?.setGamut(v)
        }
    }

    private func cycleColorTemp() {
        let names: [Int: String] = [0: "冷色", 1: "标准", 2: "暖色", 8: "原色", 3: "自定义"]
        cycleStep(actionId: "color_temp_cycle", label: "色温", order: [0, 1, 2, 8, 3],
                  valueKey: "picture_color_temperature",
                  nameFor: { names[$0] ?? "\($0)" }) { [weak self] v in
            self?.setColorTemp(v)
        }
    }

    private func cycleResponseTime() {
        let names: [Int: String] = [1: "普通", 2: "快速", 3: "高速"]
        cycleStep(actionId: "response_time_cycle", label: "响应时间", order: [1, 2, 3],
                  valueKey: "picture_response_time",
                  nameFor: { names[$0] ?? "\($0)" }) { [weak self] v in
            self?.setResponseTime(v)
        }
    }

    private func cycleSource() {
        cycleStep(actionId: "input_source_cycle", label: "信号源", order: [23, 24, 29, 30],
                  valueKey: "mitv.tvplayer.hdmi.last.source",
                  nameFor: { RegisterMap.sourceNames[$0] ?? "\($0)" }) { [weak self] v in
            self?.setSource(v)
        }
    }

    /// 各参数待下发的预览值 / 待执行任务（键都是 param）
    private var adjustPendingValues: [String: Int] = [:]
    private var adjustPendingWork: [String: DispatchWorkItem] = [:]

    private func adjustHotkey(param: String, direction: String, step: Int) {
        let delta = direction == "decrease" ? -step : step
        applyAdjustment(param: param, delta: delta, absolute: nil)
    }

    /// 菜单栏面板里的滑块松手：直接设到指定值（而不是加减一个步长）。
    func setMenuBarItem(_ id: String, to value: Int) {
        applyAdjustment(param: id, delta: 0, absolute: value)
    }

    /// 调整可调参数的统一入口。`absolute` 非空时直接采用该值，否则按 `delta` 加减。
    private func applyAdjustment(param: String, delta: Int, absolute: Int?) {
        guard !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        guard isConnected else { return log("未连接") }

        /// 暂存一次调整。与 cycleStep 一致：纯尾部，每次改动都重新开始完整倒计时，
        /// 停手后才下发。连续调整时基准取上一次的预览值，所以能连续推进而不是卡住。
        func stage(_ key: String, _ label: String, range: ClosedRange<Int>,
                   fallback: Int, apply: @escaping (Int) -> Void) {
            let base: Int
            if let absolute {
                base = absolute
            } else {
                base = (adjustPendingValues[param] ?? intValue(key, default: fallback)) + delta
            }
            let next = clamp(base, range.lowerBound, range.upperBound)
            adjustPendingValues[param] = next
            currentValues[key] = String(next)

            let delay = effectiveHotkeyDelay
            adjustPendingWork[param]?.cancel()
            adjustPendingWork[param] = nil

            guard delay > 0 else {
                HUDWindow.shared.show(title: label, value: "\(next)")
                adjustPendingValues[param] = nil
                apply(next)
                return
            }

            HUDWindow.shared.show(title: label, value: "\(next)", countdown: delay)
            let work = DispatchWorkItem { [weak self] in
                guard let self else { return }
                self.adjustPendingWork[param] = nil
                self.adjustPendingValues[param] = nil
                apply(next)
                HUDWindow.shared.endCountdown()
            }
            adjustPendingWork[param] = work
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
        }

        switch param {
        case "backlight":
            stage("picture_backlight", "背光", range: 1...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "背光", value: v, jniKey: "g_disp__disp_back_light",
                                       settingsKeys: ["picture_backlight", "xiaomi_picture_backlight"])
            }
        case "black_level":
            stage("picture_brightness", "黑色级别", range: 0...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "黑色级别", value: v, jniKey: nil,
                                       settingsKeys: ["picture_brightness"])
            }
        // 黑色级别/对比度/饱和度/色调/锐度 在原版里都只写 settings，没有 JNI 键
        case "contrast":
            stage("picture_contrast", "对比度", range: 0...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "对比度", value: v, jniKey: nil,
                                       settingsKeys: ["picture_contrast"])
            }
        case "saturation":
            stage("picture_saturation", "饱和度", range: 0...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "饱和度", value: v, jniKey: nil,
                                       settingsKeys: ["picture_saturation"])
            }
        case "hue":
            stage("picture_hue", "色调", range: 0...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "色调", value: v, jniKey: nil,
                                       settingsKeys: ["picture_hue"])
            }
        case "sharpness":
            stage("picture_sharpness", "锐度", range: 0...100, fallback: 50) { [weak self] v in
                self?.setPictureSlider(title: "锐度", value: v, jniKey: nil,
                                       settingsKeys: ["picture_sharpness"])
            }
        case "red_gain":
            stage("picture_red_gain", "红色增益", range: 524...1524, fallback: 1024) { [weak self] v in
                self?.setColorGain(title: "红色增益", settingsKey: "picture_red_gain",
                                   jniKey: "g_video__clr_gain_r", value: v)
            }
        case "green_gain":
            stage("picture_green_gain", "绿色增益", range: 524...1524, fallback: 1024) { [weak self] v in
                self?.setColorGain(title: "绿色增益", settingsKey: "picture_green_gain",
                                   jniKey: "g_video__clr_gain_g", value: v)
            }
        case "blue_gain":
            stage("picture_blue_gain", "蓝色增益", range: 524...1524, fallback: 1024) { [weak self] v in
                self?.setColorGain(title: "蓝色增益", settingsKey: "picture_blue_gain",
                                   jniKey: "g_video__clr_gain_b", value: v)
            }
        case "atmosphere_illumination":
            // 这一项的 settings 存的是原始挡位（0~14），界面显示 +1（1~15）
            let baseUI = adjustPendingValues[param]
                ?? (intValue("atmosphere_light_illumination", default: 9) + 1)
            let nextUI = clamp(baseUI + delta, 1, 15)
            adjustPendingValues[param] = nextUI
            currentValues["atmosphere_light_illumination"] = String(nextUI - 1)

            let delay = effectiveHotkeyDelay
            adjustPendingWork[param]?.cancel()
            adjustPendingWork[param] = nil

            guard delay > 0 else {
                HUDWindow.shared.show(title: "屏幕灯亮度", value: "\(nextUI)")
                adjustPendingValues[param] = nil
                setLightIllumination(nextUI)
                return
            }

            HUDWindow.shared.show(title: "屏幕灯亮度", value: "\(nextUI)", countdown: delay)
            let work = DispatchWorkItem { [weak self] in
                guard let self else { return }
                self.adjustPendingWork[param] = nil
                self.adjustPendingValues[param] = nil
                self.setLightIllumination(nextUI)
                HUDWindow.shared.endCountdown()
            }
            adjustPendingWork[param] = work
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
        default:
            break
        }
    }
}
