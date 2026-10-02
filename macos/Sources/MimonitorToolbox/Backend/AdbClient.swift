import Foundation
import MimonitorPresetCore

struct ProcessResult {
    let output: String
    let exitCode: Int32
}

/// ADB 客户端（移植自 adb.py）：封装对独立 adb server 的调用。
final class AdbClient {
    let adbPath: String
    let serverPort: String
    var ip: String = ""
    private let channelLock = NSRecursiveLock()

    /// All ADB commands participate, including connection/recovery and JNI batch reads.
    /// A recursive lock permits a whole preset capture/apply to nest existing helpers.
    func transaction<T>(_ work: () throws -> T) rethrows -> T {
        channelLock.lock()
        defer { channelLock.unlock() }
        return try work()
    }

    init(adbPath: String = AdbClient.locateAdb(),
         serverPort: String = ProcessInfo.processInfo.environment["MIMONITOR_ADB_SERVER_PORT"] ?? "5038") {
        self.adbPath = adbPath
        self.serverPort = serverPort
    }

    var serial: String { ip.isEmpty ? "" : "\(ip):5555" }

    // MARK: - 资源定位

    /// 仓库根目录（开发态定位 assets/ 用）。打包成 .app 后走 Bundle.main。
    static func repoRootURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // Backend
            .deletingLastPathComponent() // MimonitorToolbox
            .deletingLastPathComponent() // Sources
            .deletingLastPathComponent() // macos
            .deletingLastPathComponent() // Mimonitor_Toolbox（仓库根）
    }

    static func runtimeResource(_ filename: String) -> String? {
        if let url = Bundle.main.url(forResource: filename, withExtension: nil, subdirectory: "runtime") {
            return url.path
        }
        let dev = repoRootURL().appendingPathComponent("assets/runtime/\(filename)")
        return FileManager.default.fileExists(atPath: dev.path) ? dev.path : nil
    }

    static func guardianApkPath() -> String? {
        if let url = Bundle.main.url(forResource: "adbguardian-signed", withExtension: "apk", subdirectory: "adb_guardian") {
            return url.path
        }
        let dev = repoRootURL().appendingPathComponent("assets/adb_guardian/adbguardian-signed.apk")
        return FileManager.default.fileExists(atPath: dev.path) ? dev.path : nil
    }

    static func locateAdb() -> String {
        if let bundled = runtimeResource("adb"),
           FileManager.default.isExecutableFile(atPath: bundled) {
            return bundled
        }
        // 回退到 PATH 上的 adb（brew install android-platform-tools）
        return "/opt/homebrew/bin/adb"
    }

    // MARK: - 进程执行

    @discardableResult
    func run(_ args: [String], timeout: TimeInterval = 15) -> ProcessResult {
        channelLock.lock()
        defer { channelLock.unlock() }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: adbPath)
        process.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["ANDROID_ADB_SERVER_PORT"] = serverPort
        process.environment = env

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
        } catch {
            return ProcessResult(output: "执行失败: \(error.localizedDescription)", exitCode: -1)
        }

        var outData = Data()
        var errData = Data()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async { outData = stdout.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        group.enter()
        DispatchQueue.global().async { errData = stderr.fileHandleForReading.readDataToEndOfFile(); group.leave() }

        if group.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            _ = group.wait(timeout: .now() + 2)
        }
        process.waitUntilExit()

        let output = (String(data: outData, encoding: .utf8) ?? "")
            + (String(data: errData, encoding: .utf8) ?? "")
        return ProcessResult(output: output, exitCode: process.terminationStatus)
    }

    // MARK: - 基础命令

    /// Temporary, target-pinned validation. Never changes the active display or restarts its server.
    func probeScannedDevice(ip targetIP: String) -> ScannedDevice? {
        guard !targetIP.isEmpty else { return nil }
        return transaction {
            let target = "\(targetIP):5555"
            defer { _ = run(["disconnect", target], timeout: 3) }
            _ = run(["connect", target], timeout: 5)
            let deadline = Date().addingTimeInterval(6)
            while true {
                let result = run(["-s", target, "get-state"], timeout: 2)
                let state = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
                if result.exitCode == 0 && state == "device" { break }
                if state.contains("unauthorized") || Date() >= deadline { return nil }
                Thread.sleep(forTimeInterval: 0.25)
            }
            guard let model = try? checkedShell("getprop ro.product.model", target: target), !model.isEmpty else { return nil }
            return ScannedDevice(ip: targetIP, model: model)
        }
    }

    @discardableResult
    func shell(_ command: String) -> String {
        run(["-s", serial, "shell", command], timeout: 25)
            .output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @discardableResult
    func checkedShell(_ command: String, target: String) throws -> String {
        guard !target.isEmpty else { throw PresetError("未连接显示器") }
        let result = run(["-s", target, "shell", command], timeout: 25)
        let output = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = output.lowercased()
        guard result.exitCode == 0,
              !lower.contains("exception"), !lower.contains("error:"),
              !lower.contains("device offline"), !lower.contains("device not found"),
              !lower.contains("permission denial"), !lower.contains("permission denied") else {
            throw PresetError(output.isEmpty ? "ADB 命令失败（\(result.exitCode)）" : output)
        }
        return output
    }

    @discardableResult
    func connect() -> String {
        guard !ip.isEmpty else { return "" }
        return run(["connect", serial], timeout: 10).output
    }

    @discardableResult
    func disconnect(ip: String) -> String {
        guard !ip.isEmpty else { return "" }
        return run(["disconnect", "\(ip):5555"], timeout: 5).output
    }

    /// 等设备进入 device 状态。
    ///
    /// `adb connect` 只是把 TCP 连接挂上去就立刻返回，此时 `get-state` 往往还是
    /// `offline`（adbd 握手还没完成）。只查一次就会把成功的连接误判成失败，
    /// 所以要轮询等它翻转。`unauthorized` 不会自愈，直接返回。
    func waitForDevice(timeout: TimeInterval = 6) -> String {
        let deadline = Date().addingTimeInterval(timeout)
        var state = deviceState()
        while state != "device" && Date() < deadline {
            if state == "unauthorized" { return state }
            Thread.sleep(forTimeInterval: 0.4)
            state = deviceState()
        }
        return state
    }

    /// 重启 adb server。
    ///
    /// 长时间运行的 server 会失效：TCP 已经断了、但 server 仍持有旧 transport，
    /// 此时 `adb connect` 会报 "No route to host" 或 "already connected"，
    /// 而 kill + start 出一个全新的 server 立刻就能连上。
    private var lastServerRestart = Date.distantPast

    func restartServer() {
        lastServerRestart = Date()
        _ = run(["kill-server"], timeout: 5)
        Thread.sleep(forTimeInterval: 0.8)
        _ = run(["start-server"], timeout: 10)
        // 等 server 真正开始监听再往下走：紧接着就 connect 的话很容易落到 offline
        for _ in 0..<10 {
            if isServerAlive() { break }
            Thread.sleep(forTimeInterval: 0.3)
        }
        Thread.sleep(forTimeInterval: 0.3)
    }

    /// 距上次重启 server 是否已过冷却期。
    ///
    /// 恢复过程本身要十几秒（设备侧 adbd 得先清掉旧的 transport），期间重启 server
    /// 会把正在进行的握手打断，于是「重试越勤 → 越连不上」。所以重启必须限流。
    func serverRestartAllowed(cooldown: TimeInterval = 25) -> Bool {
        Date().timeIntervalSince(lastServerRestart) >= cooldown
    }

    /// 对应原版 ensure_connected：已连接就直接返回，否则连接并等待就绪。
    ///
    /// 注意这里**故意不做 `disconnect`**：实测在「卡在 offline」时，先 disconnect 会让
    /// server 转入 "No route to host"，越修越坏；而 kill-server + start-server 才是
    /// 唯一能可靠治好的手段，所以失败后直接走重启 server 这条路。
    func ensureConnected() -> (ok: Bool, state: String) {
        guard !ip.isEmpty else { return (false, "unknown") }

        // get-state 说 device 还不够，要确认真能跑命令（可能是残留的死连接）
        var state = deviceState()
        if state == "device" && transportWorks() { return (true, state) }

        _ = run(["connect", serial], timeout: 10)
        state = waitForDevice(timeout: 6)
        if state == "device" && transportWorks() { return (true, state) }

        // 连不上或卡在 offline：重启 server 后再试一轮。
        // 受冷却期限制——恢复需要时间，重启太勤反而打断自己。
        if serverRestartAllowed() {
            restartServer()
            _ = run(["connect", serial], timeout: 10)
            state = waitForDevice(timeout: 10)
        }
        return (state == "device" && transportWorks(), state)
    }

    /// server 是否还活着（能响应 version 查询）。
    func isServerAlive() -> Bool {
        run(["version"], timeout: 3).exitCode == 0
    }

    /// 预热：提前把 adb server 拉起来。
    ///
    /// 冷启动时第一条 adb 命令要顺带等 server 起来，紧接着 connect 很容易落到 offline。
    /// 启动时先预热，自动连接那一步就能快好几秒。
    func warmUpServer() {
        // start-server 本身幂等：已有 server 时直接返回，没有才拉起。
        // 不要先 isServerAlive() 再 start-server —— 那会连做两次启动操作，
        // 和随后的自动连接撞在一起。
        _ = run(["start-server"], timeout: 10)
    }

    /// 硬重连（保活守护部署后、设备重启后调用）。
    @discardableResult
    func reconnect() -> String {
        let (ok, state) = ensureConnected()
        return ok ? "connected" : state
    }

    /// 安装 APK（-r 覆盖安装，-d 允许降级），返回 adb 原始输出。
    func installApk(_ path: String) -> String {
        run(["-s", serial, "install", "-r", "-d", path], timeout: 120).output
    }

    func deviceState() -> String {
        guard !ip.isEmpty else { return "unknown" }
        return run(["-s", serial, "get-state"], timeout: 3)
            .output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 设备型号，用于连接后确认对端确实是显示器（原版 get_model）。
    /// 命令失败时 adb 会把错误文本原样吐出来（如 "adb: device offline"），不能当型号用。
    func getModel() -> String {
        let raw = shell("getprop ro.product.model").trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = raw.lowercased()
        let looksLikeError = raw.isEmpty
            || lower.contains("error") || lower.contains("offline")
            || lower.contains("unauthorized") || lower.contains("not found")
            || lower.contains("adb:")
        return looksLikeError ? "" : raw
    }

    /// 通道是否真的能用。
    ///
    /// `get-state` 返回 "device" **不代表**连接可用：adb server 会保留上一条已经死掉的
    /// TCP 连接，这时 get-state 说 device，但任何真实命令都会失败并把设备标成 offline。
    /// 所以必须用一条真实命令确认。
    func transportWorks() -> Bool {
        shell("echo __mimonitor_probe__").contains("__mimonitor_probe__")
    }

    func settingsGet(_ key: String) -> String {
        shell("settings get global \(key)")
    }

    /// 一次 shell 调用批量读取多个 setting。
    ///
    /// 用 `settings list global` 一次拿回全部 global 设置（约 460 条 / 80ms），
    /// 再在本地挑出需要的键。对比：
    ///   - 逐个 `settings get`：21 次 adb 进程启动 ≈ 2.3s
    ///   - 合并成一条 shell 循环：21 次设备端进程 ≈ 0.94s
    ///   - `settings list global`：一次往返 ≈ 0.08s（快一个数量级）
    func settingsGetBatch(_ keys: [String]) -> [String: String] {
        // 只允许安全字符，避免注入到 shell 命令里
        let safe = keys.filter { key in
            !key.isEmpty && key.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "." }
        }
        guard !safe.isEmpty else { return [:] }

        var all: [String: String] = [:]
        for line in shell("settings list global").split(separator: "\n") {
            guard let idx = line.firstIndex(of: "=") else { continue }
            let key = String(line[..<idx])
            let value = String(line[line.index(after: idx)...])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            all[key] = value
        }

        // list 整体失败时退回逐键读取（老设备 / 权限受限的情况）
        if all.isEmpty {
            let cmd = "for k in \(safe.joined(separator: " ")); do echo $k=$(settings get global $k); done"
            for line in shell(cmd).split(separator: "\n") {
                guard let idx = line.firstIndex(of: "=") else { continue }
                let key = String(line[..<idx])
                let value = String(line[line.index(after: idx)...])
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                all[key] = value
            }
        }

        var values: [String: String] = [:]
        for key in safe {
            guard let value = all[key] else { continue }
            // 与原版一致：空值 / null / N/A 视为未读到，不覆盖已有值
            if value.isEmpty || value == "null" || value == "N/A" { continue }
            values[key] = value
        }
        return values
    }

    /// 解析 `wm size` 的 Override size（移植自原版 _check_4k_state）。
    /// 没有 Override 时返回 nil，表示用的是面板原生分辨率。
    func getOverrideSize() -> (width: Int, height: Int)? {
        for line in shell("wm size").split(separator: "\n") where line.contains("Override size") {
            guard let colon = line.lastIndex(of: ":") else { return nil }
            let parts = line[line.index(after: colon)...]
                .trimmingCharacters(in: .whitespaces)
                .split(separator: "x")
            guard parts.count == 2,
                  let w = Int(parts[0].trimmingCharacters(in: .whitespaces)),
                  let h = Int(parts[1].trimmingCharacters(in: .whitespaces)) else { return nil }
            return (w, h)
        }
        return nil
    }

    /// 读取 `wm density` 的 Override 值（同样是 UI 缩放的判据之一）。
    func getOverrideDensity() -> Int? {
        for line in shell("wm density").split(separator: "\n") where line.contains("Override density") {
            guard let colon = line.lastIndex(of: ":") else { return nil }
            return Int(line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces))
        }
        return nil
    }

    func settingsPut(_ key: String, _ value: String) {
        shell("settings put global \(key) \(value)")
    }

    func keyevent(_ code: String) {
        shell("input keyevent \(code)")
    }

    func refreshPq() {
        shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7")
    }

    // MARK: - JNI / TvService

    /// 构造 `service call TvService 3 s16 "sh -c eval..."` 命令（移植自 adb.py）。
    func buildTvserviceCommand(jar: String, args: [String]) -> String {
        let encoded = args.map { "\\${IFS}\($0)" }.joined()
        return "service call TvService 3 s16 \"sh -c eval\\${IFS}CLASSPATH=\(jar)\\${IFS}/system/bin/app_process\\${IFS}/data/data/mitv.service/cache\(encoded)\""
    }

    /// 构造一条经 TvService 执行的外部命令（`sh -c eval` + `${IFS}` 分隔），移植自 adb.py。
    ///
    /// TvService 的 runSystemCommand 直接调 `Runtime.exec(String)`，按空白切分且
    /// **不起 shell**。所以命令体里的空格必须写成字面量 `${IFS}`：`sh` 会把它展开回
    /// 空白，再交给 `eval` 重新解析。
    ///
    /// 写成"真实空格 + 转义双引号"的老形式会静默失败 —— `sh -c` 只拿到被截断的第一个
    /// 词（如 `"cp`），而 `service call` 依旧返回 Parcel，看着像成功。
    func buildTvserviceShellCommand(parts: [String]) -> String {
        let encoded = parts.map { "\\${IFS}\($0)" }.joined()
        return "service call TvService 3 s16 \"sh -c eval\(encoded)\""
    }

    func jniSet(key: String, value: String, upd: Int = 3) {
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "set", key, value, String(upd)]))
    }

    func hdrToneMapping(_ value: String, upd: Int = 3) {
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setHdrToneMapping", value, String(upd)]))
    }

    func setColorGains(red: String, green: String, blue: String) {
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setColorGains", red, green, blue]))
    }

    private func jniBatchCommand(keys: [String]) -> String? {
        let safeKeys = keys.filter { !$0.isEmpty && $0.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" } }
        guard !safeKeys.isEmpty else { return nil }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        let batch = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "batchGet"] + safeKeys)
        let resultFile = "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt"
        return "mkdir -p /sdcard/Download/Mimonitor_Toolbox; rm -f \(resultFile); \(batch) >/dev/null; i=0; while [ $i -lt 30 ] && [ ! -f \(resultFile) ]; do sleep 0.1; i=$((i+1)); done; cat \(resultFile) 2>/dev/null"
    }

    private func parseJniBatch(_ out: String) -> [String: String] {
        var values: [String: String] = [:]
        for line in out.split(separator: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            guard !t.isEmpty, !t.hasPrefix("__"), let idx = t.firstIndex(of: "=") else { continue }
            let key = String(t[..<idx])
            let raw = String(t[t.index(after: idx)...])
            if raw.hasPrefix("ERROR") { continue }
            values[key] = raw
        }
        return values
    }

    func jniBatchGet(keys: [String]) -> [String: String] {
        transaction {
            guard let command = jniBatchCommand(keys: keys) else { return [:] }
            return parseJniBatch(shell(command))
        }
    }

    func jniBatchGetChecked(keys: [String], target: String) throws -> [String: String] {
        try transaction {
            guard let command = jniBatchCommand(keys: keys) else { return [:] }
            let values = parseJniBatch(try checkedShell(command, target: target))
            let missing = keys.filter { Int(values[$0] ?? "") == nil }
            guard missing.isEmpty else { throw PresetError("JNI 参数读取不完整，保留原数据：\(missing.joined(separator: "、"))") }
            return values
        }
    }

    func colorfulLed(action: String, args: [String] = []) {
        let jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["ColorfulLedTool", action] + args))
    }

    // MARK: - Jar 部署

    func ensureJars() {
        healJar("MtkDirectTool.jar")
        healJar("ColorfulLedTool.jar")
    }

    private func healJar(_ filename: String) {
        guard let local = AdbClient.runtimeResource(filename) else { return }
        let sdcardJar = "/sdcard/\(filename)"
        let cacheJar = "/data/data/mitv.service/cache/\(filename)"

        let sizeLines = shell("stat -c %s \(sdcardJar) 2>/dev/null || echo 0").split(separator: "\n")
        let sdSize = Int(sizeLines.last.map(String.init) ?? "0") ?? 0

        var localSize = 0
        if let attrs = try? FileManager.default.attributesOfItem(atPath: local),
           let n = attrs[.size] as? NSNumber {
            localSize = n.intValue
        }

        if sdSize < 1000 || (localSize > 0 && sdSize != localSize) {
            run(["-s", serial, "push", local, sdcardJar], timeout: 30)
        }
        shell(buildTvserviceShellCommand(parts: ["cp", sdcardJar, cacheJar]))
    }
}
