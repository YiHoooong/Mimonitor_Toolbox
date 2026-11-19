import Foundation
import MimonitorPresetCore
#if canImport(Darwin)
import Darwin
private let tcpStreamType: Int32 = SOCK_STREAM
private func posixConnect(_ fd: Int32, _ addr: UnsafePointer<sockaddr>, _ len: socklen_t) -> Int32 {
    Darwin.connect(fd, addr, len)
}
#elseif canImport(Glibc)
import Glibc
private let tcpStreamType: Int32 = Int32(SOCK_STREAM.rawValue)
private func posixConnect(_ fd: Int32, _ addr: UnsafePointer<sockaddr>, _ len: socklen_t) -> Int32 {
    Glibc.connect(fd, addr, len)
}
#endif

struct ProcessResult {
    let output: String
    let exitCode: Int32
}

struct DaemonQueryResult: Equatable {
    let settings: [String: String]
    let jni: [String: Int]
    let flags: [String: String]
    let elapsedMs: Int?
}

struct DaemonBatchStep: Equatable {
    let index: Int
    let ok: Bool
    let detail: String
}

enum DaemonBatchOp: Equatable {
    case put(key: String, value: String)
    case jniSet(key: String, value: Int, upd: Int)
    case jniRamp(key: String, from: Int, to: Int, upd: Int, stepMs: Int, stepSize: Int)
    case colorGains(red: Int, green: Int, blue: Int)
    case hdrToneMapping(value: Int, upd: Int)
    case soundOutput(deviceId: Int)
    case setSource(sourceId: Int)
    case led(action: String, args: [String])
    case volSet(value: Int, unmuteFirst: Bool, showUi: Bool)
    case volMute(muted: Bool, showUi: Bool)
    case key(code: String)
    case sleep(ms: Int)
    case refreshPq
}

private final class LockedDataBox: @unchecked Sendable {
    private let lock = NSLock()
    private var storage = Data()

    func set(_ data: Data) {
        lock.lock()
        storage = data
        lock.unlock()
    }

    func get() -> Data {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

/// ADB 客户端（移植自 adb.py）：封装对独立 adb server 的调用，并支持混合加速 Daemon 通道。
final class AdbClient {
    let adbPath: String
    let serverPort: String
    var ip: String = ""
    var connectionMode: ConnectionMode = .adb

    private let channelLock = NSRecursiveLock()
    private let ignoredScanLock = NSLock()
    private var ignoredScanIPs: Set<String> = []
    private let daemonStateLock = NSRecursiveLock()
    private let lifecycleLock = NSLock()

    private var spawnBlocked: Bool = false
    private var daemonReady: Bool = false
    private var daemonIp: String = ""
    private var daemonForwardPort: Int?
    private var daemonPutSupported: Bool?
    private var daemonVolSupported: Bool?
    private var daemonJarHealedIp: String = ""
    private var daemonRetryAfter: Date = .distantPast
    private var daemonNeedsRestart: Bool = false

    private var batchDepth: Int = 0
    private var batchOps: [DaemonBatchOp] = []
    private var batchReadCache: [String: String] = [:]
    private var batchJniCache: [String: Int] = [:]
    /// 仅供单元测试将 `adb forward` 成功后的本地端口重定向到测试 TCP 服务器
    var testForwardPortOverride: Int?

    func clearIgnoredScanIPs() {
        ignoredScanLock.lock()
        ignoredScanIPs.removeAll()
        ignoredScanLock.unlock()
    }

    func unignoreScanIP(_ targetIP: String) {
        let trimmed = targetIP.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        ignoredScanLock.lock()
        ignoredScanIPs.remove(trimmed)
        ignoredScanLock.unlock()
    }

    private func ignoreScanIP(_ targetIP: String) {
        let trimmed = targetIP.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        ignoredScanLock.lock()
        ignoredScanIPs.insert(trimmed)
        ignoredScanLock.unlock()
    }

    private func isScanIPIgnored(_ targetIP: String) -> Bool {
        let trimmed = targetIP.trimmingCharacters(in: .whitespacesAndNewlines)
        ignoredScanLock.lock()
        defer { ignoredScanLock.unlock() }
        return ignoredScanIPs.contains(trimmed)
    }

    /// All ADB commands participate, including connection/recovery and JNI batch reads.
    /// A recursive lock permits a whole preset capture/apply to nest existing helpers.
    func transaction<T>(_ work: () throws -> T) rethrows -> T {
        channelLock.lock()
        defer { channelLock.unlock() }
        return try work()
    }

    init(adbPath: String = AdbClient.locateAdb(),
         serverPort: String = ProcessInfo.processInfo.environment["MIMONITOR_ADB_SERVER_PORT"] ?? "5038",
         connectionMode: ConnectionMode = .adb) {
        self.adbPath = adbPath
        self.serverPort = serverPort
        self.connectionMode = connectionMode
    }

    var serial: String { ip.isEmpty ? "" : "\(ip):5555" }

    var isDaemonMode: Bool {
        daemonStateLock.lock()
        defer { daemonStateLock.unlock() }
        return connectionMode == .daemon
    }

    /// Daemon 通道是否已就绪，可用于亮度键这类低延迟写入。
    /// 真正的请求仍会走 `ensureDaemon` 探活；这里只用来决定是否启用快速跟随路径。
    var isDaemonChannelReady: Bool {
        daemonStateLock.lock()
        defer { daemonStateLock.unlock() }
        return connectionMode == .daemon
            && daemonReady
            && daemonForwardPort != nil
            && daemonIp == ip
            && !daemonNeedsRestart
    }

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
        // 开发态（swift run）没有 .app 的内嵌资源：
        // 1) build_app.sh 下载的缓存 —— 跑过一次打包就有，最省事
        // 2) Homebrew / Android SDK 的常见位置
        // 之前只认 /opt/homebrew/bin/adb，Intel Mac 上是 /usr/local/bin/adb，
        // 开发构建于是静默地一条 adb 命令都执行失败。
        let cache = repoRootURL()
            .appendingPathComponent("macos/.cache/platform-tools/adb").path
        let candidates = [
            cache,
            "/opt/homebrew/bin/adb",                                  // Apple Silicon Homebrew
            "/usr/local/bin/adb",                                     // Intel Homebrew
            NSHomeDirectory() + "/Library/Android/sdk/platform-tools/adb",
        ]
        if let found = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) {
            return found
        }
        // 都不存在：返回缓存路径，让调用方在日志里给出可操作的提示
        return cache
    }

    // MARK: - 安全引用与参数检查

    static func isSafeToken(_ token: String) -> Bool {
        !token.isEmpty && token.allSatisfy { ch in
            ch.isASCII && (ch.isLetter || ch.isNumber || ch == "_" || ch == "." || ch == "-" || ch == "/" || ch == ":")
        }
    }

    static func shellQuoteIfNeeded(_ value: String) -> String {
        if isSafeToken(value) { return value }
        return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func hasAdbDisconnectMarker(_ text: String) -> Bool {
        let lower = text.lowercased()
        return lower.contains("device offline")
            || lower.contains("device not found")
            || lower.contains("not found")
            || lower.contains("offline")
            || lower.contains("unauthorized")
            || lower.contains("closed")
    }

    // MARK: - 进程执行

    /// adb 子进程统一环境：私有 server 端口 + 关闭 mDNS。
    ///
    /// 内嵌的 platform-tools 37.x 在 macOS 26 上 mDNS zero-config 会偶发 panic
    /// （SIGABRT，crash 栈是 libadbmdns_zero_config_driver → FQServiceName::try_from），
    /// 直接把 adb server 带走：表现为 install 只回一句空错误。
    /// 本 App 全程按 IP:5555 连接，不需要 mDNS 发现；外部显式设置优先。
    ///
    /// server 由 `adb start-server` fork 后环境就定型了，所以所有启动路径
    /// （普通命令和 `spawnStartServerDetached()`）必须共用这份环境。
    func adbEnvironment(base: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
        var env = base
        env["ANDROID_ADB_SERVER_PORT"] = serverPort
        if env["ADB_MDNS"] == nil { env["ADB_MDNS"] = "0" }
        return env
    }

    @discardableResult
    func run(_ args: [String], timeout: TimeInterval = 15, useChannelLock: Bool = true) -> ProcessResult {
        if useChannelLock {
            channelLock.lock()
        }
        defer {
            if useChannelLock {
                channelLock.unlock()
            }
        }

        lifecycleLock.lock()
        let blocked = spawnBlocked
        lifecycleLock.unlock()
        if blocked && args.first != "kill-server" {
            return ProcessResult(output: "应用正在退出，已取消 ADB 命令", exitCode: -1)
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: adbPath)
        process.arguments = args
        process.environment = adbEnvironment()

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
        } catch {
            return ProcessResult(output: "执行失败: \(error.localizedDescription)", exitCode: -1)
        }

        let outBox = LockedDataBox()
        let errBox = LockedDataBox()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async {
            outBox.set(stdout.fileHandleForReading.readDataToEndOfFile())
            group.leave()
        }
        group.enter()
        DispatchQueue.global().async {
            errBox.set(stderr.fileHandleForReading.readDataToEndOfFile())
            group.leave()
        }

        if group.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            if group.wait(timeout: .now() + 1.5) == .timedOut && process.isRunning {
                kill(process.processIdentifier, SIGKILL)
                _ = group.wait(timeout: .now() + 1.0)
            }
        }
        process.waitUntilExit()

        let output = (String(data: outBox.get(), encoding: .utf8) ?? "")
            + (String(data: errBox.get(), encoding: .utf8) ?? "")
        return ProcessResult(output: output, exitCode: process.terminationStatus)
    }

    // MARK: - 跨平台 POSIX Socket（server 探活 & Daemon 通道）

    static func isTcpOpen(host: String, port: Int, timeout: Double = 1.0) -> Bool {
        guard port > 0 && port <= 65535 else { return false }
        var addr = sockaddr_in()
        #if canImport(Darwin)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        #endif
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port)).bigEndian
        let pton = host.withCString { cstr in
            inet_pton(AF_INET, cstr, &addr.sin_addr)
        }
        guard pton == 1 else { return false }

        let fd = socket(AF_INET, tcpStreamType, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }

        let flags = fcntl(fd, F_GETFL, 0)
        guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) >= 0 else { return false }

        let ret = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                posixConnect(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if ret == 0 { return true }
        guard errno == EINPROGRESS else { return false }

        var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
        let ready = poll(&pfd, 1, Int32(max(1, Int(timeout * 1000))))
        guard ready > 0 else { return false }

        var err: Int32 = 0
        var errLen = socklen_t(MemoryLayout<Int32>.size)
        guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &errLen) == 0 else { return false }
        return err == 0
    }

    static func pickFreeLocalPort() -> Int? {
        let fd = socket(AF_INET, tcpStreamType, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var reuse: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        #if canImport(Darwin)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        #endif
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        _ = "127.0.0.1".withCString { inet_pton(AF_INET, $0, &addr.sin_addr) }

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                bind(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else { return nil }

        var boundAddr = sockaddr_in()
        var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &boundAddr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                getsockname(fd, sa, &addrLen)
            }
        }
        guard nameResult == 0 else { return nil }
        let port = Int(UInt16(bigEndian: boundAddr.sin_port))
        return port > 0 ? port : nil
    }

    /// 通过本机 TCP 端口与常驻 MtkDirectTool Daemon 交换多行文本协议。
    /// 必须显式读到完整 `END` 行才算成功；连接中断/截断/超时一律返回 nil。
    static func rawSocketExchange(port: Int, payload: String, timeout: TimeInterval = 4.0) -> [String]? {
        guard port > 0 && port <= 65535 else { return nil }

        var addr = sockaddr_in()
        #if canImport(Darwin)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        #endif
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port)).bigEndian
        guard "127.0.0.1".withCString({ inet_pton(AF_INET, $0, &addr.sin_addr) }) == 1 else {
            return nil
        }

        let fd = socket(AF_INET, tcpStreamType, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var nodelay: Int32 = 1
        _ = setsockopt(fd, Int32(IPPROTO_TCP), TCP_NODELAY, &nodelay, socklen_t(MemoryLayout<Int32>.size))

        let flags = fcntl(fd, F_GETFL, 0)
        guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) >= 0 else { return nil }

        let deadline = Date().addingTimeInterval(max(0.1, timeout))
        let connectRes = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                posixConnect(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if connectRes != 0 {
            guard errno == EINPROGRESS else { return nil }
            let connectMs = Int32(max(1, min(timeout, 0.5) * 1000))
            var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            let ready = poll(&pfd, 1, connectMs)
            guard ready > 0 else { return nil }
            var err: Int32 = 0
            var errLen = socklen_t(MemoryLayout<Int32>.size)
            guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &errLen) == 0, err == 0 else { return nil }
        }

        let wireText = payload.hasSuffix("\n") ? payload : payload + "\n"
        let bytes = Array(wireText.utf8)
        var sent = 0
        while sent < bytes.count {
            let remainingMs = Int32(deadline.timeIntervalSinceNow * 1000)
            guard remainingMs > 0 else { return nil }
            let chunkSize = bytes.count - sent
            let written = bytes.withUnsafeBytes { rawBuf in
                guard let base = rawBuf.baseAddress else { return -1 }
                return send(fd, base.advanced(by: sent), chunkSize, 0)
            }
            if written > 0 {
                sent += written
                continue
            }
            if written == 0 { return nil }
            let err = errno
            if err == EINTR { continue }
            if err == EAGAIN || err == EWOULDBLOCK {
                var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                if poll(&pfd, 1, remainingMs) <= 0 { return nil }
                continue
            }
            return nil
        }

        var rawAccum = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        var lines: [String] = []
        var sawEnd = false
        var scanCursor = 0

        func extractCompleteLines() {
            while scanCursor < rawAccum.count {
                guard let nlIndex = rawAccum[scanCursor...].firstIndex(of: 0x0A) else { break }
                var lineSlice = rawAccum[scanCursor..<nlIndex]
                if lineSlice.last == 0x0D {
                    lineSlice = lineSlice.dropLast()
                }
                let line = String(decoding: lineSlice, as: UTF8.self)
                scanCursor = nlIndex + 1
                if line == "END" {
                    sawEnd = true
                    return
                }
                lines.append(line)
            }
        }

        while !sawEnd {
            let remainingMs = Int32(deadline.timeIntervalSinceNow * 1000)
            guard remainingMs > 0 else { return nil }
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, remainingMs)
            if ready < 0 {
                if errno == EINTR { continue }
                return nil
            }
            if ready == 0 { return nil }

            let count = recv(fd, &buffer, buffer.count, 0)
            if count > 0 {
                rawAccum.append(contentsOf: buffer[0..<count])
                extractCompleteLines()
            } else if count == 0 {
                // 对端关闭：若未收到完整 END 哨兵则视为截断失败
                extractCompleteLines()
                break
            } else {
                let err = errno
                if err == EINTR { continue }
                if err == EAGAIN || err == EWOULDBLOCK { continue }
                return nil
            }
        }

        guard sawEnd, !lines.isEmpty else { return nil }
        return lines
    }

    // MARK: - 基础命令

    /// Temporary, target-pinned validation. Never changes the active display or restarts its server.
    func probeScannedDevice(ip targetIP: String, skipIgnored: Bool = true) -> ScannedDevice? {
        guard !targetIP.isEmpty else { return nil }
        if skipIgnored && isScanIPIgnored(targetIP) { return nil }
        return transaction {
            let target = "\(targetIP):5555"
            defer { _ = run(["disconnect", target], timeout: 3) }
            _ = run(["connect", target], timeout: 5)
            let deadline = Date().addingTimeInterval(6)
            while true {
                let result = run(["-s", target, "get-state"], timeout: 2)
                let state = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
                if result.exitCode == 0 && state == "device" { break }
                if state.contains("unauthorized") {
                    ignoreScanIP(targetIP)
                    return nil
                }
                if Date() >= deadline { return nil }
                Thread.sleep(forTimeInterval: 0.25)
            }
            guard let model = try? checkedShell("getprop ro.product.model", target: target), !model.isEmpty else { return nil }
            if model.lowercased().contains("mitv") {
                unignoreScanIP(targetIP)
            } else {
                ignoreScanIP(targetIP)
            }
            return ScannedDevice(ip: targetIP, model: model)
        }
    }

    @discardableResult
    func shell(_ command: String) -> String {
        guard !serial.isEmpty else { return "" }
        return run(["-s", serial, "shell", command], timeout: 25)
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
        daemonStateLock.lock()
        let shouldStopDaemon = !ip.isEmpty && (daemonIp == ip || daemonIp.isEmpty)
        if shouldStopDaemon {
            mediaVolumeSetBinderVerified = nil
            mediaVolumeMuteBinderVerified = nil
        }
        daemonStateLock.unlock()
        if shouldStopDaemon {
            stopDaemon(nonBlockingForwardRemoval: true)
        }
        guard !ip.isEmpty else { return "" }
        return run(["disconnect", "\(ip):5555"], timeout: 5, useChannelLock: false).output
    }

    /// 等设备进入 device 状态。
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

    private var lastServerRestart = Date.distantPast

    func restartServer() {
        lastServerRestart = Date()
        resetDaemonState(removeForward: false)
        _ = run(["kill-server"], timeout: 5)
        Thread.sleep(forTimeInterval: 0.8)
        spawnStartServerDetached()
        for _ in 0..<10 {
            if isServerAlive() { break }
            Thread.sleep(forTimeInterval: 0.3)
        }
        Thread.sleep(forTimeInterval: 0.3)
    }

    func serverRestartAllowed(cooldown: TimeInterval = 25) -> Bool {
        Date().timeIntervalSince(lastServerRestart) >= cooldown
    }

    func ensureConnected() -> (ok: Bool, state: String) {
        return transaction {
            guard !ip.isEmpty else { return (false, "unknown") }

            var state = deviceState()
            if state == "device" && transportWorks() { return (true, state) }

            _ = run(["connect", serial], timeout: 10)
            state = waitForDevice(timeout: 6)
            if state == "device" && transportWorks() { return (true, state) }

            if serverRestartAllowed() {
                restartServer()
                _ = run(["connect", serial], timeout: 10)
                state = waitForDevice(timeout: 10)
            }
            return (state == "device" && transportWorks(), state)
        }
    }

    /// 探监听端口而非 `adb version`（纯客户端命令，server 没起也返回 0，假阳性）。
    func isServerAlive() -> Bool {
        Self.isTcpOpen(host: "127.0.0.1", port: Int(serverPort) ?? 5038, timeout: 1.0)
    }

    /// 退出时清理本应用独占的 adb server，避免 fork-server 残留持续重连（issue #25）。
    func killServer() {
        lifecycleLock.lock()
        spawnBlocked = true
        lifecycleLock.unlock()
        resetDaemonState(removeForward: false)
        _ = run(["kill-server"], timeout: 5, useChannelLock: false)
    }

    func warmUpServer() {
        if isServerAlive() { return }
        spawnStartServerDetached()
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if isServerAlive() { return }
            Thread.sleep(forTimeInterval: 0.2)
        }
    }

    private func spawnStartServerDetached() {
        lifecycleLock.lock()
        let blocked = spawnBlocked
        lifecycleLock.unlock()
        guard !blocked else { return }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: adbPath)
        process.arguments = ["start-server"]
        process.environment = adbEnvironment()
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        try? process.run()
    }

    @discardableResult
    func reconnect() -> String {
        resetDaemonState(removeForward: true)
        let (ok, state) = ensureConnected()
        return ok ? "connected" : state
    }

    func installApk(_ path: String) -> String {
        guard !serial.isEmpty else { return "error: no target" }
        return run(["-s", serial, "install", "-r", "-d", path], timeout: 120).output
    }

    func uninstallPackage(_ package: String) -> String {
        guard !serial.isEmpty else { return "error: no target" }
        return run(["-s", serial, "uninstall", package], timeout: 120).output
    }

    func deviceState() -> String {
        guard !ip.isEmpty else { return "unknown" }
        return run(["-s", serial, "get-state"], timeout: 3)
            .output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func getModel() -> String {
        let raw = shell("getprop ro.product.model").trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = raw.lowercased()
        let looksLikeError = raw.isEmpty
            || lower.contains("error") || lower.contains("offline")
            || lower.contains("unauthorized") || lower.contains("not found")
            || lower.contains("adb:")
        return looksLikeError ? "" : raw
    }

    func transportWorks() -> Bool {
        shell("echo __mimonitor_probe__").contains("__mimonitor_probe__")
    }

    // MARK: - 混合加速 Daemon 生命周期 & 通信

    @discardableResult
    func setConnectionMode(_ mode: ConnectionMode) -> ConnectionMode {
        daemonStateLock.lock()
        let previous = connectionMode
        connectionMode = mode
        daemonRetryAfter = .distantPast
        if mode != .daemon && previous == .daemon {
            batchDepth = 0
            batchOps.removeAll()
            batchReadCache.removeAll()
            batchJniCache.removeAll()
        }
        daemonStateLock.unlock()

        if mode != .daemon && previous == .daemon {
            stopDaemon()
        }
        return mode
    }

    func resetDaemonState(removeForward: Bool = true, nonBlockingForwardRemoval: Bool = false) {
        daemonStateLock.lock()
        let oldPort = daemonForwardPort
        let oldIp = daemonIp
        daemonReady = false
        daemonIp = ""
        daemonForwardPort = nil
        daemonPutSupported = nil
        daemonVolSupported = nil
        daemonJarHealedIp = ""
        daemonRetryAfter = .distantPast
        daemonNeedsRestart = false
        mediaVolumeSetBinderVerified = nil
        mediaVolumeMuteBinderVerified = nil
        daemonStateLock.unlock()

        if removeForward, let oldPort, !oldIp.isEmpty {
            lifecycleLock.lock()
            let blocked = spawnBlocked
            lifecycleLock.unlock()
            if !blocked {
                _ = run(
                    ["-s", "\(oldIp):5555", "forward", "--remove", "tcp:\(oldPort)"],
                    timeout: 2,
                    useChannelLock: !nonBlockingForwardRemoval
                )
            }
        }
    }

    func stopDaemon(nonBlockingForwardRemoval: Bool = false) {
        daemonStateLock.lock()
        let port = daemonForwardPort
        let activeIp = daemonIp
        daemonStateLock.unlock()

        if let port, !activeIp.isEmpty {
            lifecycleLock.lock()
            let blocked = spawnBlocked
            lifecycleLock.unlock()
            if !blocked {
                _ = Self.rawSocketExchange(port: port, payload: "QUIT\n", timeout: 0.8)
            }
        }
        resetDaemonState(removeForward: true, nonBlockingForwardRemoval: nonBlockingForwardRemoval)
    }

    private func setupAdbForward() -> Int? {
        daemonStateLock.lock()
        let oldPort = daemonForwardPort
        let oldIp = daemonIp
        daemonForwardPort = nil
        daemonStateLock.unlock()

        if let oldPort, oldIp == ip, !ip.isEmpty {
            _ = run(["-s", serial, "forward", "--remove", "tcp:\(oldPort)"], timeout: 2)
        }

        for _ in 0..<2 {
            guard let localPort = Self.pickFreeLocalPort() else { continue }
            let res = run(
                ["-s", serial, "forward", "tcp:\(localPort)", "tcp:\(ConnectionMode.mtkDaemonPort)"],
                timeout: 4
            )
            let lower = res.output.lowercased()
            if res.exitCode != 0
                || Self.hasAdbDisconnectMarker(res.output)
                || lower.contains("error:")
                || lower.contains("cannot bind")
                || lower.contains("failed") {
                continue
            }
            let effectivePort = testForwardPortOverride ?? localPort
            daemonStateLock.lock()
            daemonForwardPort = effectivePort
            daemonIp = ip
            daemonStateLock.unlock()
            return effectivePort
        }
        return nil
    }

    private func daemonSocketRequest(_ payload: String, timeout: TimeInterval = 4.0) -> [String]? {
        daemonStateLock.lock()
        let activePort = (daemonIp == ip && !ip.isEmpty) ? daemonForwardPort : nil
        daemonStateLock.unlock()
        guard let activePort else { return nil }

        lifecycleLock.lock()
        let blocked = spawnBlocked
        lifecycleLock.unlock()
        if blocked { return nil }

        let lines = Self.rawSocketExchange(port: activePort, payload: payload, timeout: timeout)
        if lines == nil {
            daemonStateLock.lock()
            daemonReady = false
            daemonStateLock.unlock()
        }
        return lines
    }

    private func parseDaemonPong(_ pongLine: String) {
        daemonStateLock.lock()
        defer { daemonStateLock.unlock() }
        for token in pongLine.split(whereSeparator: \.isWhitespace) {
            if token == "put=0" {
                daemonPutSupported = false
            } else if token == "put=1" {
                daemonPutSupported = true
            } else if token == "vol=0" {
                daemonVolSupported = false
            } else if token == "vol=1" {
                daemonVolSupported = true
            }
        }
    }

    @discardableResult
    func ensureDaemon(check: Bool = false) throws -> Bool {
        guard isDaemonMode, !ip.isEmpty else { return false }

        lifecycleLock.lock()
        let blocked = spawnBlocked
        lifecycleLock.unlock()
        if blocked {
            if check { throw PresetError("应用正在退出，Daemon 启动已取消") }
            return false
        }

        return try transaction {
            daemonStateLock.lock()
            let wrongIp = !daemonIp.isEmpty && daemonIp != ip
            daemonStateLock.unlock()
            if wrongIp {
                resetDaemonState(removeForward: true)
            }

            daemonStateLock.lock()
            let cachedReady = daemonReady
                && daemonForwardPort != nil
                && daemonJarHealedIp == ip
                && !daemonNeedsRestart
            let retryAfter = daemonRetryAfter
            let needsHeal = daemonJarHealedIp != ip
            daemonStateLock.unlock()

            if cachedReady {
                if let pong = daemonSocketRequest("PING\n", timeout: 1.2),
                   let head = pong.first,
                   head.hasPrefix("OK MtkDirectTool v2") {
                    parseDaemonPong(head)
                    daemonStateLock.lock()
                    daemonRetryAfter = .distantPast
                    daemonStateLock.unlock()
                    return true
                }
                daemonStateLock.lock()
                daemonReady = false
                daemonStateLock.unlock()
            }

            if Date() < retryAfter {
                return false
            }

            if needsHeal {
                if healJar("MtkDirectTool.jar") {
                    daemonStateLock.lock()
                    daemonJarHealedIp = ip
                    daemonStateLock.unlock()
                }
            }

            daemonStateLock.lock()
            let hasForward = daemonForwardPort != nil && daemonIp == ip
            daemonStateLock.unlock()

            var freshForward = false
            if !hasForward {
                if setupAdbForward() == nil {
                    daemonStateLock.lock()
                    daemonRetryAfter = Date().addingTimeInterval(5.0)
                    daemonStateLock.unlock()
                    return false
                }
                freshForward = true
            }

            var pong = daemonSocketRequest("PING\n", timeout: 1.2)
            if pong == nil && !freshForward {
                if setupAdbForward() == nil {
                    daemonStateLock.lock()
                    daemonRetryAfter = Date().addingTimeInterval(5.0)
                    daemonStateLock.unlock()
                    return false
                }
                pong = daemonSocketRequest("PING\n", timeout: 1.2)
            }

            if let pong, let head = pong.first {
                daemonStateLock.lock()
                let needsRestart = daemonNeedsRestart
                daemonStateLock.unlock()
                if !needsRestart && head.hasPrefix("OK MtkDirectTool v2") {
                    parseDaemonPong(head)
                    daemonStateLock.lock()
                    daemonReady = true
                    daemonRetryAfter = .distantPast
                    daemonStateLock.unlock()
                    return true
                }
                _ = daemonSocketRequest("QUIT\n", timeout: 0.8)
            }

            daemonStateLock.lock()
            daemonNeedsRestart = false
            daemonStateLock.unlock()

            let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            let launchCmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "daemon", String(ConnectionMode.mtkDaemonPort)])
            let launchOut: String
            if check {
                launchOut = try checkedShell(launchCmd, target: serial)
            } else {
                launchOut = shell(launchCmd)
            }
            if ip.isEmpty || Self.hasAdbDisconnectMarker(launchOut) {
                daemonStateLock.lock()
                daemonRetryAfter = Date().addingTimeInterval(5.0)
                daemonStateLock.unlock()
                return false
            }

            for delay in [0.15, 0.2, 0.25, 0.35, 0.5, 0.5, 0.5] {
                if ip.isEmpty || !isDaemonMode { return false }
                Thread.sleep(forTimeInterval: delay)
                if ip.isEmpty || !isDaemonMode { return false }
                lifecycleLock.lock()
                let isBlocked = spawnBlocked
                lifecycleLock.unlock()
                if isBlocked { return false }

                if let readyPong = daemonSocketRequest("PING\n", timeout: 1.2),
                   let head = readyPong.first,
                   head.hasPrefix("OK MtkDirectTool v2") {
                    parseDaemonPong(head)
                    daemonStateLock.lock()
                    daemonReady = true
                    daemonRetryAfter = .distantPast
                    daemonStateLock.unlock()
                    return true
                }
            }

            daemonStateLock.lock()
            daemonRetryAfter = Date().addingTimeInterval(5.0)
            daemonStateLock.unlock()
            return false
        }
    }

    func daemonQuery(settingsKeys: [String] = [],
                     jniKeys: [String] = [],
                     flags: [String] = [],
                     targetIp: String? = nil,
                     check: Bool = false) throws -> DaemonQueryResult? {
        guard isDaemonMode, !ip.isEmpty else { return nil }
        if let targetIp, !targetIp.isEmpty, targetIp != ip { return nil }

        let safeSettings = settingsKeys
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !$0.contains(where: \.isWhitespace) }
        let safeJni = jniKeys
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && $0.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" } }
        let safeFlags = flags
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !$0.contains(where: \.isWhitespace) }

        if safeSettings.isEmpty && safeJni.isEmpty && safeFlags.isEmpty {
            return DaemonQueryResult(settings: [:], jni: [:], flags: [:], elapsedMs: 0)
        }

        var lines = ["QUERY"]
        if !safeSettings.isEmpty {
            lines.append("SETTINGS " + safeSettings.joined(separator: " "))
        }
        if !safeJni.isEmpty {
            lines.append("JNI " + safeJni.joined(separator: " "))
        }
        if !safeFlags.isEmpty {
            lines.append("FLAGS " + safeFlags.joined(separator: " "))
        }
        lines.append("END")
        let payload = lines.joined(separator: "\n") + "\n"

        let resp: [String]? = try transaction {
            guard try ensureDaemon(check: check) else { return nil }
            if let first = daemonSocketRequest(payload, timeout: 5.0) {
                return first
            }
            guard try ensureDaemon(check: check) else { return nil }
            return daemonSocketRequest(payload, timeout: 5.0)
        }

        guard let resp, resp.first == "OK" else { return nil }

        var settings: [String: String] = [:]
        var jni: [String: Int] = [:]
        var flagMap: [String: String] = [:]
        var elapsedMs: Int?

        for line in resp.dropFirst() {
            if line.hasPrefix("S ") {
                let body = String(line.dropFirst(2))
                if let eq = body.firstIndex(of: "=") {
                    let k = String(body[..<eq])
                    let v = String(body[body.index(after: eq)...])
                    if !v.hasPrefix("__ERR__:") {
                        settings[k] = v
                    }
                }
            } else if line.hasPrefix("J ") {
                let body = String(line.dropFirst(2))
                if let eq = body.firstIndex(of: "=") {
                    let k = String(body[..<eq])
                    let v = String(body[body.index(after: eq)...])
                    if !v.hasPrefix("__ERR__:"), let n = Int(v.trimmingCharacters(in: .whitespaces)) {
                        jni[k] = n
                    }
                }
            } else if line.hasPrefix("F ") {
                let body = String(line.dropFirst(2))
                if let eq = body.firstIndex(of: "=") {
                    let k = String(body[..<eq])
                    let v = String(body[body.index(after: eq)...])
                    if !v.hasPrefix("__ERR__:") {
                        flagMap[k] = v
                    }
                }
            } else if line.hasPrefix("ELAPSED ") {
                let raw = line.dropFirst(8).trimmingCharacters(in: .whitespaces)
                elapsedMs = Int(raw)
            }
        }

        return DaemonQueryResult(settings: settings, jni: jni, flags: flagMap, elapsedMs: elapsedMs)
    }

    static func formatBatchLine(_ op: DaemonBatchOp) -> String? {
        switch op {
        case .put(let key, let value):
            let cleanKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !cleanKey.isEmpty,
                  !cleanKey.contains(where: \.isWhitespace),
                  !value.contains("\n"), !value.contains("\r") else {
                return nil
            }
            // 不对 value 做 trim，保留前后空白或交给 Java/ADB 精确落盘
            return "PUT \(cleanKey) \(value)"
        case .jniSet(let key, let value, let upd):
            guard isSafeToken(key) else { return nil }
            return "JNI_SET \(key) \(value) \(upd)"
        case .jniRamp(let key, let from, let to, let upd, let stepMs, let stepSize):
            guard isSafeToken(key) else { return nil }
            return "JNI_RAMP \(key) \(from) \(to) \(upd) \(stepMs) \(stepSize)"
        case .colorGains(let r, let g, let b):
            return "COLOR_GAINS \(r) \(g) \(b)"
        case .hdrToneMapping(let value, let upd):
            return "HDR_TM \(value) \(upd)"
        case .soundOutput(let deviceId):
            return "SOUND_OUT \(deviceId)"
        case .setSource(let sourceId):
            return "SET_SOURCE \(sourceId)"
        case .led(let action, let args):
            let allParts = [action] + args
            guard allParts.allSatisfy({ !$0.contains("\n") && !$0.contains("\r") && !$0.contains(where: \.isWhitespace) }) else {
                return nil
            }
            return (["LED"] + allParts).joined(separator: " ")
        case .volSet(let value, let unmuteFirst, let showUi):
            return "VOL_SET \(value) \(unmuteFirst ? 1 : 0) \(showUi ? 1 : 0)"
        case .volMute(let muted, let showUi):
            return "VOL_MUTE \(muted ? 1 : 0) \(showUi ? 1 : 0)"
        case .key(let code):
            guard isSafeToken(code) else { return nil }
            return "KEY \(code)"
        case .sleep(let ms):
            return "SLEEP \(max(0, ms))"
        case .refreshPq:
            return "REFRESH_PQ"
        }
    }

    static func estimateBatchTimeout(_ ops: [DaemonBatchOp]) -> TimeInterval {
        var expectedMs = 0
        for op in ops {
            switch op {
            case .sleep(let ms):
                expectedMs += min(5000, max(0, ms))
            case .jniRamp(_, let from, let to, _, let stepMs, let stepSize):
                if stepMs > 0 {
                    let delta = abs(to - from)
                    let steps = max(1, (delta + max(1, stepSize) - 1) / max(1, stepSize))
                    expectedMs += steps * stepMs
                } else {
                    expectedMs += 150
                }
            default:
                expectedMs += 40
            }
        }
        return max(8.0, Double(expectedMs) / 1000.0 + 5.0)
    }

    func daemonBatch(_ ops: [DaemonBatchOp], check: Bool = false) throws -> (ok: Bool, errIdx: Int?, steps: [DaemonBatchStep]) {
        if ops.isEmpty { return (true, nil, []) }
        guard isDaemonMode, !ip.isEmpty else { return (false, nil, []) }

        var formattedLines = ["BATCH"]
        for op in ops {
            guard let line = Self.formatBatchLine(op) else {
                return (false, nil, [])
            }
            formattedLines.append(line)
        }
        formattedLines.append("END")
        let payload = formattedLines.joined(separator: "\n") + "\n"
        let batchTimeout = Self.estimateBatchTimeout(ops)

        let resp: [String]? = try transaction {
            guard try ensureDaemon(check: check) else { return nil }
            // 不对已发送写入载荷的超时/断连盲目重放，防止写操作重复触发
            return daemonSocketRequest(payload, timeout: batchTimeout)
        }
        guard let resp, let head = resp.first else {
            return (false, nil, [])
        }

        var steps: [DaemonBatchStep] = []
        for line in resp.dropFirst() where line.hasPrefix("STEP ") {
            let parts = line.split(separator: " ", maxSplits: 3, omittingEmptySubsequences: false)
            if parts.count >= 4, let idx = Int(parts[1]) {
                let ok = parts[2] == "OK"
                steps.append(DaemonBatchStep(index: idx, ok: ok, detail: String(parts[3])))
            }
        }

        if head == "OK" {
            let allStepsOk = steps.allSatisfy(\.ok)
            return (allStepsOk, nil, steps)
        }
        if head.hasPrefix("ERR_AT ") {
            let rest = String(head.dropFirst(7))
            let idxToken = rest.split(separator: " ", maxSplits: 1).first.map(String.init) ?? "0"
            let errIdx = Int(idxToken) ?? 0
            return (false, errIdx, steps)
        }
        return (false, nil, steps)
    }

    static func coalesceBatchOps(_ ops: [DaemonBatchOp]) -> [DaemonBatchOp] {
        var lastRefreshIdx: Int?
        for (idx, op) in ops.enumerated() {
            if case .refreshPq = op {
                lastRefreshIdx = idx
            }
        }
        guard let keepIdx = lastRefreshIdx else { return ops }
        return ops.enumerated().compactMap { idx, op in
            if case .refreshPq = op, idx != keepIdx { return nil }
            return op
        }
    }

    @discardableResult
    func batchWrites<T>(prefetchSettings: [String] = [],
                        prefetchJni: [String] = [],
                        check: Bool = false,
                        _ work: () throws -> T) throws -> T {
        guard isDaemonMode else {
            return try work()
        }

        daemonStateLock.lock()
        let outermost = batchDepth == 0
        batchDepth += 1
        daemonStateLock.unlock()

        if outermost && (!prefetchSettings.isEmpty || !prefetchJni.isEmpty) {
            if let snapshot = try? daemonQuery(settingsKeys: prefetchSettings, jniKeys: prefetchJni, check: false) {
                daemonStateLock.lock()
                for (k, v) in snapshot.settings { batchReadCache[k] = v }
                for (k, v) in snapshot.jni { batchJniCache[k] = v }
                daemonStateLock.unlock()
            }
        }

        defer {
            daemonStateLock.lock()
            batchDepth = max(0, batchDepth - 1)
            if outermost {
                batchOps.removeAll()
                batchReadCache.removeAll()
                batchJniCache.removeAll()
            }
            daemonStateLock.unlock()
        }

        let result = try work()
        if outermost {
            try flushBatch(check: check)
        }
        return result
    }

    func batchSleep(_ ms: Int) {
        let delayMs = max(0, ms)
        guard delayMs > 0 else { return }
        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        if inBatch {
            batchOps.append(.sleep(ms: delayMs))
        }
        daemonStateLock.unlock()
        if !inBatch {
            Thread.sleep(forTimeInterval: Double(delayMs) / 1000.0)
        }
    }

    private func recordOrRun(_ op: DaemonBatchOp) -> Bool {
        daemonStateLock.lock()
        if connectionMode == .daemon && batchDepth > 0 {
            batchOps.append(op)
            daemonStateLock.unlock()
            return true
        }
        daemonStateLock.unlock()
        return false
    }

    func flushBatch(check: Bool = false) throws {
        daemonStateLock.lock()
        let pending = batchOps
        batchOps.removeAll()
        let putSupported = daemonPutSupported
        daemonStateLock.unlock()

        let coalesced = Self.coalesceBatchOps(pending)
        guard !coalesced.isEmpty else { return }

        if putSupported == false {
            var putRun: [(String, String)] = []
            var daemonSegment: [DaemonBatchOp] = []

            func flushPutRun() throws {
                guard !putRun.isEmpty else { return }
                try adbPutBatchDirect(putRun, check: check)
                putRun.removeAll()
            }
            func flushDaemonSegment() throws {
                guard !daemonSegment.isEmpty else { return }
                let segment = daemonSegment
                daemonSegment.removeAll()
                try executeDaemonSegmentWithFallback(segment, check: check)
            }

            for op in coalesced {
                if case .put(let k, let v) = op {
                    try flushDaemonSegment()
                    putRun.append((k, v))
                } else {
                    try flushPutRun()
                    daemonSegment.append(op)
                }
            }
            try flushPutRun()
            try flushDaemonSegment()
            return
        }

        try executeDaemonSegmentWithFallback(coalesced, check: check)
    }

    private func executeDaemonSegmentWithFallback(_ ops: [DaemonBatchOp], check: Bool) throws {
        let (ok, errIdx, steps) = try daemonBatch(ops, check: check)
        if ok { return }

        if let errIdx {
            let clamped = max(0, min(ops.count, errIdx))
            let stepMap = Dictionary(steps.map { ($0.index, $0.ok) }, uniquingKeysWith: { _, new in new })
            var fallbackIndices: [Int] = []
            for idx in 0..<clamped where stepMap[idx] != true {
                fallbackIndices.append(idx)
            }
            if clamped < ops.count {
                if case .put = ops[clamped] {
                    daemonStateLock.lock()
                    daemonPutSupported = false
                    daemonStateLock.unlock()
                }
                fallbackIndices.append(contentsOf: clamped..<ops.count)
            }
            try executeMixedAdbFallback(fallbackIndices.map { ops[$0] }, check: check)
            return
        }

        if !steps.isEmpty {
            let stepMap = Dictionary(steps.map { ($0.index, $0.ok) }, uniquingKeysWith: { _, new in new })
            var failedIndices: [Int] = []
            for idx in 0..<ops.count where stepMap[idx] != true {
                if case .put = ops[idx], stepMap[idx] == false {
                    daemonStateLock.lock()
                    daemonPutSupported = false
                    daemonStateLock.unlock()
                }
                failedIndices.append(idx)
            }
            if !failedIndices.isEmpty {
                try executeMixedAdbFallback(failedIndices.map { ops[$0] }, check: check)
            }
            return
        }

        try executeMixedAdbFallback(ops, check: check)
    }

    private func executeMixedAdbFallback(_ ops: [DaemonBatchOp], check: Bool) throws {
        var putRun: [(String, String)] = []
        func flushPuts() throws {
            guard !putRun.isEmpty else { return }
            try adbPutBatchDirect(putRun, check: check)
            putRun.removeAll()
        }

        for op in ops {
            if case .put(let k, let v) = op {
                putRun.append((k, v))
            } else {
                try flushPuts()
                try execOpViaAdb(op, check: check)
            }
        }
        try flushPuts()
    }

    private func execOpViaAdb(_ op: DaemonBatchOp, check: Bool) throws {
        switch op {
        case .put(let key, let value):
            try adbPutDirect(key, value, check: check)
        case .jniSet(let key, let value, let upd):
            let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            let cmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "set", key, String(value), String(upd)])
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .jniRamp(let key, let from, let to, let upd, let stepMs, let stepSize):
            adbJniRampDirect(key: key, from: from, to: to, settingsKeys: [], upd: upd, stepMs: stepMs, step: stepSize)
        case .colorGains(let r, let g, let b):
            let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            let cmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setColorGains", String(r), String(g), String(b)])
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .hdrToneMapping(let value, let upd):
            let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            let cmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setHdrToneMapping", String(value), String(upd)])
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .soundOutput(let deviceId):
            let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            let cmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setSoundOutput", String(deviceId)])
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .setSource(let sourceId):
            if jarSetInputSource(sourceId, target: serial) {
                return
            }
            try adbSetInputSource(sourceId, target: serial, check: check)
        case .led(let action, let args):
            let jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
            let cmd = buildTvserviceCommand(jar: jar, args: ["ColorfulLedTool", action] + args)
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .volSet(let value, let unmuteFirst, let showUi):
            _ = adbSetMediaVolumeDirect(value, unmuteFirst: unmuteFirst, showUI: showUi)
        case .volMute(let muted, let showUi):
            _ = adbSetMediaMutedDirect(muted, showUI: showUi)
        case .key(let code):
            let cmd = "input keyevent \(Self.shellQuoteIfNeeded(code))"
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        case .sleep(let ms):
            Thread.sleep(forTimeInterval: Double(max(0, ms)) / 1000.0)
        case .refreshPq:
            let cmd = "am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7"
            if check { _ = try checkedShell(cmd, target: serial) } else { _ = shell(cmd) }
        }
    }

    // MARK: - Settings 读写

    func settingsGet(_ key: String) -> String {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }

        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        let cached = inBatch ? batchReadCache[trimmed] : nil
        daemonStateLock.unlock()
        if let cached { return cached }

        if isDaemonMode, let q = try? daemonQuery(settingsKeys: [trimmed]),
           let value = q.settings[trimmed] {
            return value
        }
        return shell("settings get global \(Self.shellQuoteIfNeeded(trimmed))")
    }

    /// 一次 shell 调用批量读取多个 setting（Daemon 模式下优先走 QUERY 通道）。
    func settingsGetBatch(_ keys: [String]) -> [String: String] {
        let safe = keys.filter { key in
            !key.isEmpty && key.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "." }
        }
        guard !safe.isEmpty else { return [:] }

        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        var cachedValues: [String: String] = [:]
        var allFoundInCache = inBatch
        if inBatch {
            for k in safe {
                if let val = batchReadCache[k] {
                    cachedValues[k] = val
                } else {
                    allFoundInCache = false
                    break
                }
            }
        }
        daemonStateLock.unlock()

        if allFoundInCache {
            var filtered: [String: String] = [:]
            for k in safe {
                if let val = cachedValues[k], !val.isEmpty, val != "null", val != "N/A" {
                    filtered[k] = val
                }
            }
            return filtered
        }

        if isDaemonMode, let q = try? daemonQuery(settingsKeys: safe) {
            var filtered: [String: String] = [:]
            for k in safe {
                if let val = q.settings[k], !val.isEmpty, val != "null", val != "N/A" {
                    filtered[k] = val
                }
            }
            return filtered
        }

        var all: [String: String] = [:]
        for line in shell("settings list global").split(separator: "\n") {
            guard let idx = line.firstIndex(of: "=") else { continue }
            let key = String(line[..<idx])
            let value = String(line[line.index(after: idx)...])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            all[key] = value
        }

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
            if value.isEmpty || value == "null" || value == "N/A" { continue }
            values[key] = value
        }
        return values
    }

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

    func getOverrideDensity() -> Int? {
        for line in shell("wm density").split(separator: "\n") where line.contains("Override density") {
            guard let colon = line.lastIndex(of: ":") else { return nil }
            return Int(line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces))
        }
        return nil
    }

    private func adbPutDirect(_ key: String, _ value: String, check: Bool = false) throws {
        let cmd = "settings put global \(Self.shellQuoteIfNeeded(key)) \(Self.shellQuoteIfNeeded(value))"
        if check {
            _ = try checkedShell(cmd, target: serial)
        } else {
            _ = shell(cmd)
        }
    }

    private func adbPutBatchDirect(_ pairs: [(String, String)], check: Bool = false) throws {
        guard !pairs.isEmpty else { return }
        let cmd = pairs.map { "settings put global \(Self.shellQuoteIfNeeded($0.0)) \(Self.shellQuoteIfNeeded($0.1))" }
            .joined(separator: "; ")
        if check {
            _ = try checkedShell(cmd, target: serial)
        } else {
            _ = shell(cmd)
        }
    }

    func settingsPut(_ key: String, _ value: String) {
        if recordOrRun(.put(key: key, value: value)) { return }
        if isDaemonMode {
            daemonStateLock.lock()
            let putSupported = daemonPutSupported
            daemonStateLock.unlock()
            if putSupported != false {
                if let (ok, errIdx, steps) = try? daemonBatch([.put(key: key, value: value)]),
                   ok, errIdx == nil, steps.allSatisfy(\.ok) {
                    return
                }
                if let (_, errIdx, _) = try? daemonBatch([.put(key: key, value: value)]), errIdx != nil {
                    daemonStateLock.lock()
                    daemonPutSupported = false
                    daemonStateLock.unlock()
                }
                _ = try? executeMixedAdbFallback([.put(key: key, value: value)], check: false)
                return
            }
        }
        try? adbPutDirect(key, value, check: false)
    }

    /// 一次写多个 settings。
    func settingsPutBatch(_ pairs: [(String, String)]) {
        guard !pairs.isEmpty else { return }
        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        if inBatch {
            for (k, v) in pairs {
                batchOps.append(.put(key: k, value: v))
            }
        }
        let putSupported = daemonPutSupported
        daemonStateLock.unlock()
        if inBatch { return }

        if isDaemonMode && putSupported != false {
            let ops = pairs.map { DaemonBatchOp.put(key: $0.0, value: $0.1) }
            if let (ok, _, steps) = try? daemonBatch(ops), ok, steps.allSatisfy(\.ok) {
                return
            }
        }
        try? adbPutBatchDirect(pairs, check: false)
    }

    func keyevent(_ code: String) {
        if recordOrRun(.key(code: code)) { return }
        shell("input keyevent \(Self.shellQuoteIfNeeded(code))")
    }

    // MARK: - 音量 / 静音

    enum AudioServiceTxn {
        static let adjustStreamVolume = 10
        static let setStreamVolume = 12
        static let isStreamMute = 17
        static let getStreamVolumeForMute = 22
    }
    static let streamMusic = 3
    static let adjustMute = -100
    static let adjustUnmute = 100
    static let binderShellPackage = "com.android.shell"

    /// AudioService 的两个 transaction 是不同方法，必须分别回读验证：
    /// ROM 可能只改其中一个，不能拿一个的验证结果去信任另一个。
    private var mediaVolumeSetBinderVerified: Bool?
    private var mediaVolumeMuteBinderVerified: Bool?

    func mediaVolume() -> Int? {
        if isDaemonMode, let q = try? daemonQuery(flags: ["volume"]),
           let raw = q.flags["volume"], let value = Int(raw), (0...100).contains(value) {
            return value
        }
        let output = shell("service call audio \(AudioServiceTxn.getStreamVolumeForMute) i32 \(Self.streamMusic)")
        if let value = Self.parseServiceCallInt(output), (0...100).contains(value) {
            return value
        }
        let raw = shell("settings get system volume_music_speaker")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return Int(raw)
    }

    func mediaMuted() -> Bool? {
        if isDaemonMode, let q = try? daemonQuery(flags: ["muted"]),
           let raw = q.flags["muted"] {
            return raw == "1" || raw.lowercased() == "true"
        }
        let output = shell("service call audio \(AudioServiceTxn.isStreamMute) i32 \(Self.streamMusic)")
        guard let value = Self.parseServiceCallInt(output) else { return nil }
        return value != 0
    }

    @discardableResult
    func setMediaVolume(_ value: Int, unmuteFirst: Bool = false, showUI: Bool = false) -> Bool {
        let target = max(0, min(100, value))
        if recordOrRun(.volSet(value: target, unmuteFirst: unmuteFirst, showUi: showUI)) {
            return true
        }
        if isDaemonMode {
            daemonStateLock.lock()
            let volSupported = daemonVolSupported
            daemonStateLock.unlock()
            if volSupported != false,
               let (ok, _, _) = try? daemonBatch([.volSet(value: target, unmuteFirst: unmuteFirst, showUi: showUI)]),
               ok {
                return true
            }
        }
        return adbSetMediaVolumeDirect(target, unmuteFirst: unmuteFirst, showUI: showUI)
    }

    @discardableResult
    private func adbSetMediaVolumeDirect(_ target: Int, unmuteFirst: Bool, showUI: Bool) -> Bool {
        let clamped = max(0, min(100, target))
        let flags = showUI ? 1 : 0
        if unmuteFirst { _ = adbSetMediaMutedDirect(false, showUI: false) }
        if mediaVolumeSetBinderVerified != false {
            let output = shell("service call audio \(AudioServiceTxn.setStreamVolume) i32 \(Self.streamMusic) i32 \(clamped) i32 \(flags) s16 \(Self.binderShellPackage)")
            if Self.serviceCallSucceeded(output) {
                if mediaVolumeSetBinderVerified == nil {
                    let applied = mediaVolume() == clamped
                    mediaVolumeSetBinderVerified = applied
                    if applied { return true }
                } else {
                    return true
                }
            } else {
                mediaVolumeSetBinderVerified = false
            }
        }
        applyMediaVolumeByKeyEvent(clamped)
        return false
    }

    @discardableResult
    func setMediaMuted(_ muted: Bool, showUI: Bool = false) -> Bool {
        if recordOrRun(.volMute(muted: muted, showUi: showUI)) {
            return true
        }
        if isDaemonMode {
            daemonStateLock.lock()
            let volSupported = daemonVolSupported
            daemonStateLock.unlock()
            if volSupported != false,
               let (ok, _, _) = try? daemonBatch([.volMute(muted: muted, showUi: showUI)]),
               ok {
                return true
            }
        }
        return adbSetMediaMutedDirect(muted, showUI: showUI)
    }

    @discardableResult
    private func adbSetMediaMutedDirect(_ muted: Bool, showUI: Bool) -> Bool {
        let flags = showUI ? 1 : 0
        if mediaVolumeMuteBinderVerified != false {
            let direction = muted ? Self.adjustMute : Self.adjustUnmute
            let output = shell("service call audio \(AudioServiceTxn.adjustStreamVolume) i32 \(Self.streamMusic) i32 \(direction) i32 \(flags) s16 \(Self.binderShellPackage)")
            if Self.serviceCallSucceeded(output) {
                if mediaVolumeMuteBinderVerified == nil {
                    let applied = mediaMuted() == muted
                    mediaVolumeMuteBinderVerified = applied
                    if applied { return true }
                } else {
                    return true
                }
            } else {
                mediaVolumeMuteBinderVerified = false
            }
        }
        shell("input keyevent KEYCODE_VOLUME_MUTE")
        return false
    }

    private func applyMediaVolumeByKeyEvent(_ target: Int) {
        guard let current = mediaVolume() else { return }
        let delta = target - current
        guard delta != 0 else { return }
        let key = delta > 0 ? "KEYCODE_VOLUME_UP" : "KEYCODE_VOLUME_DOWN"
        let batch = Array(repeating: key, count: min(abs(delta), 100)).joined(separator: " ")
        shell("input keyevent \(batch)")
    }

    static func serviceCallSucceeded(_ output: String) -> Bool {
        guard output.contains("Result: Parcel(") else { return false }
        let lower = output.lowercased()
        return !lower.contains("error") && !lower.contains("exception")
    }

    static func parseServiceCallInt(_ output: String) -> Int? {
        let hexDigits = Set("0123456789abcdefABCDEF")
        var tokens: [String] = []
        var current = ""
        for character in output {
            if hexDigits.contains(character) {
                current.append(character)
            } else {
                if current.count == 8 { tokens.append(current) }
                current = ""
            }
        }
        if current.count == 8 { tokens.append(current) }
        guard tokens.count >= 2, let last = tokens.last, let value = Int(last, radix: 16) else { return nil }
        return value
    }

    func refreshPq() {
        if recordOrRun(.refreshPq) { return }
        if isDaemonMode, let (ok, _, _) = try? daemonBatch([.refreshPq]), ok {
            return
        }
        shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7")
    }

    // MARK: - JNI / TvService

    func buildTvserviceCommand(jar: String, args: [String]) -> String {
        let encoded = args.map { "\\${IFS}\(Self.shellQuoteIfNeeded($0))" }.joined()
        return "service call TvService 3 s16 \"sh -c eval\\${IFS}CLASSPATH=\(Self.shellQuoteIfNeeded(jar))\\${IFS}/system/bin/app_process\\${IFS}/data/data/mitv.service/cache\(encoded)\""
    }

    func buildTvserviceShellCommand(parts: [String]) -> String {
        let encoded = parts.map { "\\${IFS}\(Self.shellQuoteIfNeeded($0))" }.joined()
        return "service call TvService 3 s16 \"sh -c eval\(encoded)\""
    }

    func jniSet(key: String, value: String, upd: Int = 3) {
        if let intVal = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) {
            if recordOrRun(.jniSet(key: key, value: intVal, upd: upd)) { return }
            if isDaemonMode, let (ok, _, _) = try? daemonBatch([.jniSet(key: key, value: intVal, upd: upd)]), ok {
                return
            }
        }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "set", key, value, String(upd)]))
    }

    func jniRamp(key: String, from: Int, to: Int, settingsKeys: [String] = [],
                 upd: Int = 3, stepMs: Int = 0, step: Int = 1) {
        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        if inBatch {
            batchOps.append(.jniRamp(key: key, from: from, to: to, upd: upd, stepMs: stepMs, stepSize: step))
            for sk in settingsKeys {
                batchOps.append(.put(key: sk, value: String(to)))
            }
        }
        daemonStateLock.unlock()
        if inBatch { return }

        if isDaemonMode {
            var ops: [DaemonBatchOp] = [
                .jniRamp(key: key, from: from, to: to, upd: upd, stepMs: stepMs, stepSize: step)
            ]
            for sk in settingsKeys {
                ops.append(.put(key: sk, value: String(to)))
            }
            // 只回退失败的那几步。Daemon 以 system_app 运行时 Settings.Global
            // 写入会被拒（PUT_DENIED），但前面的 JNI_RAMP 已经生效；如果这里
            // 整体回退到 adbJniRampDirect，会把同一段渐变从旧值再跑一遍，
            // 表现为“到目标亮度后又闪回旧值、再回到目标”。
            try? executeDaemonSegmentWithFallback(ops, check: false)
            return
        }
        adbJniRampDirect(key: key, from: from, to: to, settingsKeys: settingsKeys, upd: upd, stepMs: stepMs, step: step)
    }

    private func adbJniRampDirect(key: String, from: Int, to: Int, settingsKeys: [String],
                                  upd: Int, stepMs: Int, step: Int) {
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        let dir = "/sdcard/Download/Mimonitor_Toolbox"
        let done = "\(dir)/.mtk_ramp_done"
        let ramp = buildTvserviceCommand(jar: jar, args: [
            "MtkDirectTool", "ramp", key, String(from), String(to), String(upd),
            String(stepMs), done, String(step)
        ])
        var parts = ["mkdir -p \(dir)", "rm -f \(done)", "\(ramp) >/dev/null"]
        parts.append("i=0; while [ $i -lt 80 ] && [ ! -f \(done) ]; do sleep 0.1; i=$((i+1)); done")
        parts.append(contentsOf: settingsKeys.map {
            "settings put global \(Self.shellQuoteIfNeeded($0)) \(to)"
        })
        shell(parts.joined(separator: "; "))
    }

    func hdrToneMapping(_ value: String, upd: Int = 3) {
        if let intVal = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) {
            if recordOrRun(.hdrToneMapping(value: intVal, upd: upd)) { return }
            if isDaemonMode, let (ok, _, _) = try? daemonBatch([.hdrToneMapping(value: intVal, upd: upd)]), ok {
                return
            }
        }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setHdrToneMapping", value, String(upd)]))
    }

    func setColorGains(red: String, green: String, blue: String) {
        if let r = Int(red), let g = Int(green), let b = Int(blue) {
            if recordOrRun(.colorGains(red: r, green: g, blue: b)) { return }
            if isDaemonMode, let (ok, _, _) = try? daemonBatch([.colorGains(red: r, green: g, blue: b)]), ok {
                return
            }
        }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setColorGains", red, green, blue]))
    }

    func setSoundOutput(_ deviceId: Int, target: String? = nil) throws {
        let resolvedTarget = (target?.isEmpty == false) ? target! : serial
        if resolvedTarget == serial {
            if recordOrRun(.soundOutput(deviceId: deviceId)) { return }
            if isDaemonMode, let (ok, _, _) = try? daemonBatch([.soundOutput(deviceId: deviceId)], check: true), ok {
                return
            }
        }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        try checkedShell(
            buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setSoundOutput", String(deviceId)]),
            target: resolvedTarget
        )
    }

    /// 优先通过 MtkDirectTool.jar（Daemon / CLI）切换输入信号源，失败时回退原始 ADB。
    func setInputSource(_ sourceId: Int, target: String? = nil, check: Bool = true) throws {
        let resolvedTarget = (target?.isEmpty == false) ? target! : serial
        if resolvedTarget == serial {
            if recordOrRun(.setSource(sourceId: sourceId)) { return }
            if isDaemonMode, let (ok, _, _) = try? daemonBatch([.setSource(sourceId: sourceId)], check: false), ok {
                return
            }
        }
        if jarSetInputSource(sourceId, target: resolvedTarget) {
            return
        }
        try adbSetInputSource(sourceId, target: resolvedTarget, check: check)
    }

    @discardableResult
    func jarSetInputSource(_ sourceId: Int, target: String? = nil) -> Bool {
        let resolvedTarget = (target?.isEmpty == false) ? target! : serial
        guard !ip.isEmpty, resolvedTarget == serial, healJar("MtkDirectTool.jar") else { return false }
        let jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        let resultFile = "/sdcard/Download/Mimonitor_Toolbox/.mtk_source_result.txt"
        let setCmd = buildTvserviceCommand(jar: jar, args: ["MtkDirectTool", "setSource", String(sourceId)])
        let cmd = "mkdir -p /sdcard/Download/Mimonitor_Toolbox; rm -f \(resultFile); \(setCmd) >/dev/null; i=0; while [ $i -lt 30 ] && [ ! -f \(resultFile) ]; do sleep 0.1; i=$((i+1)); done; cat \(resultFile) 2>/dev/null"
        let out = shell(cmd)
        return out.contains("RESULT: SET_SOURCE")
            && !Self.hasAdbDisconnectMarker(out)
            && !out.contains("ERROR")
            && !out.contains("Exception")
    }

    func adbSetInputSource(_ sourceId: Int, target: String? = nil, check: Bool = false) throws {
        let resolvedTarget = (target?.isEmpty == false) ? target! : serial
        let stopCmd = "am force-stop com.xiaomi.mitv.tvplayer"
        let startCmd = "am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input \(sourceId) -f 0x10000000"
        if check {
            _ = try checkedShell(stopCmd, target: resolvedTarget)
            _ = try checkedShell(startCmd, target: resolvedTarget)
        } else {
            _ = shell(stopCmd)
            _ = shell(startCmd)
        }
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
        let safeKeys = keys.filter { !$0.isEmpty && $0.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" } }
        guard !safeKeys.isEmpty else { return [:] }

        daemonStateLock.lock()
        let inBatch = connectionMode == .daemon && batchDepth > 0
        var cached: [String: String] = [:]
        var allCached = inBatch
        if inBatch {
            for k in safeKeys {
                if let v = batchJniCache[k] {
                    cached[k] = String(v)
                } else {
                    allCached = false
                    break
                }
            }
        }
        daemonStateLock.unlock()
        if allCached { return cached }

        if isDaemonMode, let q = try? daemonQuery(jniKeys: safeKeys) {
            var values: [String: String] = [:]
            for k in safeKeys {
                if let v = q.jni[k] {
                    values[k] = String(v)
                }
            }
            if values.count == safeKeys.count {
                return values
            }
        }

        return transaction {
            guard let command = jniBatchCommand(keys: safeKeys) else { return [:] }
            return parseJniBatch(shell(command))
        }
    }

    func jniBatchGetChecked(keys: [String], target: String) throws -> [String: String] {
        let safeKeys = keys.filter { !$0.isEmpty && $0.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" } }
        guard !safeKeys.isEmpty else { return [:] }

        if isDaemonMode, target == serial,
           let q = try? daemonQuery(jniKeys: safeKeys, check: true) {
            var values: [String: String] = [:]
            for k in safeKeys {
                if let v = q.jni[k] {
                    values[k] = String(v)
                }
            }
            if values.count == safeKeys.count {
                return values
            }
        }

        return try transaction {
            guard let command = jniBatchCommand(keys: safeKeys) else { return [:] }
            let values = parseJniBatch(try checkedShell(command, target: target))
            let missing = safeKeys.filter { Int(values[$0] ?? "") == nil }
            guard missing.isEmpty else { throw PresetError("JNI 参数读取不完整，保留原数据：\(missing.joined(separator: "、"))") }
            return values
        }
    }

    func colorfulLed(action: String, args: [String] = []) {
        if recordOrRun(.led(action: action, args: args)) { return }
        if isDaemonMode, let (ok, _, _) = try? daemonBatch([.led(action: action, args: args)]), ok {
            return
        }
        let jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
        shell(buildTvserviceCommand(jar: jar, args: ["ColorfulLedTool", action] + args))
    }

    // MARK: - Jar 部署

    func ensureJars() {
        _ = healJar("MtkDirectTool.jar")
        _ = healJar("ColorfulLedTool.jar")
    }

    @discardableResult
    private func healJar(_ filename: String) -> Bool {
        guard !ip.isEmpty, let local = AdbClient.runtimeResource(filename) else { return false }
        let sdcardJar = "/sdcard/\(filename)"
        let cacheJar = "/data/data/mitv.service/cache/\(filename)"

        var localSize = 0
        if let attrs = try? FileManager.default.attributesOfItem(atPath: local),
           let n = attrs[.size] as? NSNumber {
            localSize = n.intValue
        }
        guard localSize > 0 else { return false }

        let sizeOutput = shell("echo $(stat -c %s \(sdcardJar) 2>/dev/null || echo 0) $(stat -c %s \(cacheJar) 2>/dev/null || echo 0)")
        let tokens = sizeOutput.split(whereSeparator: \.isWhitespace).compactMap { Int($0) }
        let sdSize = tokens.first ?? 0
        let cacheSize = tokens.count >= 2 ? tokens[1] : 0

        var pushed = false
        if sdSize != localSize {
            let pushRes = run(["-s", serial, "push", local, sdcardJar], timeout: 30)
            let pushLower = pushRes.output.lowercased()
            if pushRes.exitCode != 0 || Self.hasAdbDisconnectMarker(pushRes.output) || pushLower.contains("error:") {
                return false
            }
            pushed = true
        }

        if pushed || cacheSize != localSize {
            let cpOut = shell(buildTvserviceShellCommand(parts: ["cp", sdcardJar, cacheJar]))
            if Self.hasAdbDisconnectMarker(cpOut) {
                return false
            }
        }
        return true
    }
}
