import Foundation
import XCTest
import MimonitorPresetCore
@testable import MimonitorToolbox
#if canImport(Darwin)
import Darwin
private let testTcpStreamType: Int32 = SOCK_STREAM
private func testPosixAccept(_ fd: Int32, _ addr: UnsafeMutablePointer<sockaddr>?, _ len: UnsafeMutablePointer<socklen_t>?) -> Int32 {
    Darwin.accept(fd, addr, len)
}
#elseif canImport(Glibc)
import Glibc
private let testTcpStreamType: Int32 = Int32(SOCK_STREAM.rawValue)
private func testPosixAccept(_ fd: Int32, _ addr: UnsafeMutablePointer<sockaddr>?, _ len: UnsafeMutablePointer<socklen_t>?) -> Int32 {
    Glibc.accept(fd, addr, len)
}
#endif

/// 轻量级本机 TCP 测试服务器，模拟 MtkDirectTool v2.1 Daemon 协议。
private final class MockDaemonServer: @unchecked Sendable {
    let listenFd: Int32
    let port: Int
    private let lock = NSLock()
    private var stopped = false
    private var acceptThread: Thread?
    private var _requests: [String] = []
    var requests: [String] {
        lock.lock()
        defer { lock.unlock() }
        return _requests
    }

    var handler: (String) -> String

    init(handler: @escaping (String) -> String) throws {
        self.handler = handler
        let fd = socket(AF_INET, testTcpStreamType, 0)
        guard fd >= 0 else {
            throw NSError(domain: "MockDaemonServer", code: 1, userInfo: [NSLocalizedDescriptionKey: "socket failed"])
        }
        var reuse: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        #if canImport(Darwin)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        #endif
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        _ = "127.0.0.1".withCString { inet_pton(AF_INET, $0, &addr.sin_addr) }

        let bound = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                bind(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0, listen(fd, 16) == 0 else {
            close(fd)
            throw NSError(domain: "MockDaemonServer", code: 2, userInfo: [NSLocalizedDescriptionKey: "bind/listen failed"])
        }

        var boundAddr = sockaddr_in()
        var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &boundAddr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                getsockname(fd, sa, &addrLen)
            }
        }
        self.listenFd = fd
        self.port = Int(UInt16(bigEndian: boundAddr.sin_port))
    }

    func start() {
        let t = Thread { [weak self] in
            self?.serveLoop()
        }
        acceptThread = t
        t.start()
    }

    func stop() {
        lock.lock()
        let already = stopped
        stopped = true
        lock.unlock()
        if !already {
            shutdown(listenFd, Int32(SHUT_RDWR))
            close(listenFd)
        }
    }

    deinit {
        stop()
    }

    private func isStopped() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopped
    }

    private func serveLoop() {
        while !isStopped() {
            var pfd = pollfd(fd: listenFd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, 50)
            if ready <= 0 { continue }
            let clientFd = testPosixAccept(listenFd, nil, nil)
            if clientFd < 0 { continue }
            handleClient(clientFd)
        }
    }

    private func handleClient(_ fd: Int32) {
        defer { close(fd) }
        var data = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        let deadline = Date().addingTimeInterval(2.0)

        while Date() < deadline {
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, 50)
            if ready < 0 { break }
            if ready == 0 {
                if !data.isEmpty {
                    let s = String(decoding: data, as: UTF8.self)
                    if s == "PING\n" || s == "QUIT\n" || s.contains("\nEND\n") || s.hasSuffix("\nEND") {
                        break
                    }
                }
                continue
            }
            let n = recv(fd, &buf, buf.count, 0)
            if n <= 0 { break }
            data.append(contentsOf: buf[0..<n])
            let text = String(decoding: data, as: UTF8.self)
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed == "PING" || trimmed == "QUIT" || text.contains("\nEND\n") || text.hasSuffix("\nEND") {
                break
            }
        }

        let reqText = String(decoding: data, as: UTF8.self)
        lock.lock()
        _requests.append(reqText)
        lock.unlock()

        let reply = handler(reqText)
        if !reply.isEmpty {
            let replyBytes = Array(reply.utf8)
            var sent = 0
            while sent < replyBytes.count {
                let w = replyBytes.withUnsafeBytes { raw in
                    send(fd, raw.baseAddress!.advanced(by: sent), replyBytes.count - sent, 0)
                }
                if w <= 0 { break }
                sent += w
            }
        }
    }
}

final class HybridDaemonTests: XCTestCase {
    private func makeFakeAdb(script: String = "exit 0") throws -> (dir: URL, adbPath: String, logURL: URL) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("hybrid-daemon-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let adb = dir.appendingPathComponent("adb")
        let logURL = dir.appendingPathComponent("adb.log")
        try Data().write(to: logURL)
        let fullScript = """
        #!/bin/sh
        printf '%s\\n' "$*" >> '\(logURL.path)'
        \(script)
        """
        try fullScript.write(to: adb, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: adb.path)
        return (dir, adb.path, logURL)
    }

    func testConnectionModeNormalizationAndPersistence() {
        XCTAssertEqual(ConnectionMode.mtkDaemonPort, 27891)
        XCTAssertEqual(ConnectionMode.normalize("daemon"), .daemon)
        XCTAssertEqual(ConnectionMode.normalize(" HYBRID "), .daemon)
        XCTAssertEqual(ConnectionMode.normalize("fast"), .daemon)
        XCTAssertEqual(ConnectionMode.normalize("adb"), .adb)
        XCTAssertEqual(ConnectionMode.normalize(nil), .adb)
        XCTAssertEqual(ConnectionMode.normalize("unknown"), .adb)

        let suiteName = "HybridDaemonTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }
        XCTAssertEqual(ConnectionMode.load(from: defaults), .adb)
        ConnectionMode.daemon.save(to: defaults)
        XCTAssertEqual(ConnectionMode.load(from: defaults), .daemon)
    }

    func testFormatBatchLinePreservesTrailingSpaceAndRejectsNewlines() {
        XCTAssertEqual(AdbClient.formatBatchLine(.put(key: "k1", value: "val  ")), "PUT k1 val  ")
        XCTAssertNil(AdbClient.formatBatchLine(.put(key: "k1", value: "a\nb")))
        XCTAssertNil(AdbClient.formatBatchLine(.put(key: "k1", value: "a\rb")))
        XCTAssertNil(AdbClient.formatBatchLine(.put(key: "bad key", value: "1")))
        XCTAssertEqual(
            AdbClient.coalesceBatchOps([.put(key: "a", value: "1"), .refreshPq, .put(key: "b", value: "2"), .refreshPq]),
            [.put(key: "a", value: "1"), .put(key: "b", value: "2"), .refreshPq]
        )
        XCTAssertGreaterThanOrEqual(
            AdbClient.estimateBatchTimeout([.sleep(ms: 4000), .jniRamp(key: "k", from: 0, to: 100, upd: 3, stepMs: 20, stepSize: 5)]),
            9.0
        )
    }

    func testShellQuotingAndTcpProbeValidation() {
        XCTAssertEqual(AdbClient.shellQuoteIfNeeded("picture_mode"), "picture_mode")
        XCTAssertEqual(AdbClient.shellQuoteIfNeeded("a; rm -rf /"), "'a; rm -rf /'")
        XCTAssertEqual(AdbClient.shellQuoteIfNeeded("it's"), "'it'\\''s'")
        XCTAssertFalse(AdbClient.isTcpOpen(host: "not_an_ip", port: 5555, timeout: 0.1))
    }

    func testRawSocketExchangeRequiresExplicitEndSentinel() throws {
        let server = try MockDaemonServer { _ in
            return "OK MtkDirectTool v2.1 pid=100 uid=1000 put=1\n" // truncated: no END
        }
        server.start()
        defer { server.stop() }

        XCTAssertNil(AdbClient.rawSocketExchange(port: server.port, payload: "PING\n", timeout: 1.0))

        server.handler = { _ in
            "OK MtkDirectTool v2.1 pid=100 uid=1000 put=1\nEND\n"
        }
        let lines = AdbClient.rawSocketExchange(port: server.port, payload: "PING\n", timeout: 1.0)
        XCTAssertEqual(lines, ["OK MtkDirectTool v2.1 pid=100 uid=1000 put=1"])
    }

    func testDaemonQueryBatchModeSwitchAndDisconnectCleanup() throws {
        let server = try MockDaemonServer { req in
            if req.hasPrefix("PING") {
                return "OK MtkDirectTool v2.1 pid=42 uid=1000 put=1\nEND\n"
            }
            if req.hasPrefix("QUERY") {
                return "OK\nS picture_mode=14\nJ g_disp__disp_back_light=77\nF volume=28\nF muted=0\nELAPSED 3\nEND\n"
            }
            if req.hasPrefix("BATCH") {
                return "OK\nSTEP 0 OK JNI_SET g_disp__disp_back_light=0\nSTEP 1 OK PUT picture_backlight\nSTEP 2 OK REFRESH_PQ\nELAPSED 5\nEND\n"
            }
            if req.hasPrefix("QUIT") {
                return "OK BYE\nEND\n"
            }
            return "ERR UNKNOWN_CMD\nEND\n"
        }
        server.start()
        defer { server.stop() }

        let (dir, adbPath, logURL) = try makeFakeAdb()
        defer { try? FileManager.default.removeItem(at: dir) }

        let client = AdbClient(adbPath: adbPath, connectionMode: .daemon)
        client.ip = "192.168.1.20"
        client.testForwardPortOverride = server.port

        XCTAssertTrue(try client.ensureDaemon())
        let q = try client.daemonQuery(
            settingsKeys: ["picture_mode"],
            jniKeys: ["g_disp__disp_back_light"],
            flags: ["volume", "muted"]
        )
        XCTAssertEqual(q?.settings["picture_mode"], "14")
        XCTAssertEqual(q?.jni["g_disp__disp_back_light"], 77)
        XCTAssertEqual(client.mediaVolume(), 28)
        XCTAssertEqual(client.mediaMuted(), false)

        try client.batchWrites {
            client.jniSet(key: "g_disp__disp_back_light", value: "80")
            client.settingsPut("picture_backlight", "80")
            client.refreshPq()
            client.refreshPq() // coalesced
        }

        let batchReqs = server.requests.filter { $0.hasPrefix("BATCH") }
        XCTAssertEqual(batchReqs.count, 1)
        XCTAssertTrue(batchReqs[0].contains("JNI_SET g_disp__disp_back_light 80 3"))
        XCTAssertTrue(batchReqs[0].contains("PUT picture_backlight 80"))
        // Only one REFRESH_PQ after coalescing
        XCTAssertEqual(batchReqs[0].components(separatedBy: "REFRESH_PQ").count - 1, 1)

        // Switching back to .adb sends QUIT + removes forward port
        client.setConnectionMode(.adb)
        XCTAssertTrue(server.requests.contains { $0.hasPrefix("QUIT") })
        let adbLog = try String(contentsOf: logURL, encoding: .utf8)
        XCTAssertTrue(adbLog.contains("forward tcp:"))
        XCTAssertTrue(adbLog.contains("tcp:27891"))
        XCTAssertTrue(adbLog.contains("forward --remove tcp:\(server.port)"))

        // Switch back to .daemon and verify disconnect() also tears down daemon + forward
        client.setConnectionMode(.daemon)
        XCTAssertTrue(try client.ensureDaemon())
        let quitCountBefore = server.requests.filter { $0.hasPrefix("QUIT") }.count
        _ = client.disconnect(ip: "192.168.1.20")
        let quitCountAfter = server.requests.filter { $0.hasPrefix("QUIT") }.count
        XCTAssertEqual(quitCountAfter, quitCountBefore + 1)
        let adbLog2 = try String(contentsOf: logURL, encoding: .utf8)
        XCTAssertTrue(adbLog2.contains("disconnect 192.168.1.20:5555"))
    }

    func testStepErrorAndErrAtFallbackOnlyReplayFailedOpsAndPropagateCheckErrors() throws {
        let server = try MockDaemonServer { req in
            if req.hasPrefix("PING") {
                return "OK MtkDirectTool v2.1 pid=55 uid=1000 put=1\nEND\n"
            }
            if req.hasPrefix("BATCH") && req.contains("VOL_SET") {
                // Step 0 OK, Step 1 ERR
                return "OK\nSTEP 0 OK JNI_SET g_disp__disp_back_light=0\nSTEP 1 ERR VOL_VERIFY_FAILED\nELAPSED 4\nEND\n"
            }
            if req.hasPrefix("BATCH") && req.contains("PUT picture_contrast") {
                // ERR_AT step 1 (PUT denied) -> step 0 succeeded, step 1 & 2 fall back
                return "ERR_AT 1 SecurityException: denied\nSTEP 0 OK JNI_SET g_video__clr_temp=2\nEND\n"
            }
            if req.hasPrefix("QUIT") {
                return "OK BYE\nEND\n"
            }
            return "OK\nEND\n"
        }
        server.start()
        defer { server.stop() }

        let (dir, adbPath, logURL) = try makeFakeAdb(script: """
        case "$4" in
          'service call audio 12 '*) echo 'Result: Parcel(00000000)' ;;
          'service call audio 22 '*) echo 'Result: Parcel(00000000 00000023)' ;;
          *'error_key'*) echo 'error: denied'; exit 1 ;;
        esac
        exit 0
        """)
        defer { try? FileManager.default.removeItem(at: dir) }

        let client = AdbClient(adbPath: adbPath, connectionMode: .daemon)
        client.ip = "192.168.1.20"
        client.testForwardPortOverride = server.port

        // 1. STEP 1 ERR only falls back step 1 (volume), not step 0 (JNI_SET)
        try client.batchWrites {
            client.jniSet(key: "g_disp__disp_back_light", value: "50")
            _ = client.setMediaVolume(35)
        }
        let log1 = try String(contentsOf: logURL, encoding: .utf8)
        XCTAssertFalse(log1.contains("MtkDirectTool set g_disp__disp_back_light"))
        XCTAssertTrue(log1.contains("service call audio 12"))

        // 2. ERR_AT 1 triggers fallback for steps 1..end while skipping already-succeeded step 0
        try client.batchWrites {
            client.jniSet(key: "g_video__clr_temp", value: "2")
            client.settingsPut("picture_contrast", "55")
            client.settingsPut("picture_brightness", "45")
            client.refreshPq()
        }
        let log2 = try String(contentsOf: logURL, encoding: .utf8)
        XCTAssertFalse(log2.contains("MtkDirectTool set g_video__clr_temp"))
        XCTAssertTrue(log2.contains("settings put global picture_contrast 55; settings put global picture_brightness 45"))
        XCTAssertTrue(log2.contains("PIC_MODE_CHANGED"))

        // 3. check: true propagates PresetError when fallback fails (daemonPutSupported is now false)
        XCTAssertThrowsError(try client.batchWrites(check: true) {
            client.settingsPut("error_key", "1")
        })
    }

    func testForwardFailureSetsCooldownAndFallsBackToStandardAdb() throws {
        let (dir, adbPath, logURL) = try makeFakeAdb(script: """
        case "$3" in
          forward) echo 'error: cannot bind'; exit 1 ;;
          shell)
            case "$4" in
              'settings get global picture_mode') echo 14 ;;
            esac ;;
        esac
        exit 0
        """)
        defer { try? FileManager.default.removeItem(at: dir) }

        let client = AdbClient(adbPath: adbPath, connectionMode: .daemon)
        client.ip = "192.168.1.20"

        XCTAssertEqual(client.settingsGet("picture_mode"), "14")
        let forwardCountAfterFirst = try String(contentsOf: logURL, encoding: .utf8)
            .components(separatedBy: "forward tcp:").count - 1
        XCTAssertEqual(forwardCountAfterFirst, 2) // 2 attempts before 5s cooldown

        // Immediate second call respects cooldown and goes straight to ADB without calling forward again
        XCTAssertEqual(client.settingsGet("picture_mode"), "14")
        let forwardCountAfterSecond = try String(contentsOf: logURL, encoding: .utf8)
            .components(separatedBy: "forward tcp:").count - 1
        XCTAssertEqual(forwardCountAfterSecond, 2)
    }

    func testPresetDeviceUsesDaemonFastPathOnlyWhenIdentityMatchesActiveIp() throws {
        let server = try MockDaemonServer { req in
            if req.hasPrefix("PING") {
                return "OK MtkDirectTool v2.1 pid=77 uid=1000 put=1\nEND\n"
            }
            if req.hasPrefix("QUERY") {
                return """
                OK
                S mitv.tvplayer.hdmi.last.source=29
                S picture_mode=14
                S tv_picture_light_sensor=0
                S picture_backlight=40
                S picture_brightness=50
                S picture_contrast=60
                S picture_saturation=51
                S picture_hue=49
                S picture_sharpness=30
                S picture_color_temperature=1
                S picture_red_gain=1024
                S picture_green_gain=1024
                S picture_blue_gain=1024
                S picture_local_dimming=3
                S picture_dynamic_definition=0
                S picture_response_time=2
                S tv_picture_advanced_video_color_space=6
                S \(MemcSupport.primarySettingKey)=0
                J g_disp__disp_back_light=55
                J g_video__vid_gamut_mapping_mode=6
                J g_video__clr_temp=2
                J g_video__vid_local_dimming=3
                J g_video__light_sensor_switch=0
                J g_video__vid_od_response_time=2
                J g_video__vid_insert_black=0
                J g_video__vid_mjc_effect=0
                J g_video__vid_game_mode_with_memc_switch=0
                J g_video__picture_mode=14
                J g_video__dp_adaptive_sync=0
                END
                """ + "\n"
            }
            if req.hasPrefix("BATCH") {
                return "OK\nSTEP 0 OK JNI_SET g_disp__disp_back_light=0\nEND\n"
            }
            if req.hasPrefix("QUIT") {
                return "OK BYE\nEND\n"
            }
            return "ERR UNKNOWN_CMD\nEND\n"
        }
        server.start()
        defer { server.stop() }

        let (dir, adbPath, _) = try makeFakeAdb()
        defer { try? FileManager.default.removeItem(at: dir) }

        let client = AdbClient(adbPath: adbPath, connectionMode: .daemon)
        client.ip = "192.168.1.20"
        client.testForwardPortOverride = server.port

        // Matching identity uses daemon QUERY and BATCH
        let matchingDevice = AdbPresetDevice(adb: client, identity: "192.168.1.20")
        let captured = try matchingDevice.capture()
        XCTAssertEqual(captured["picture_backlight"], "55")
        XCTAssertEqual(captured["freesync"], "0")
        XCTAssertTrue(server.requests.contains { $0.hasPrefix("QUERY") })

        let report = try matchingDevice.apply(captured)
        XCTAssertTrue(report.ok)
        XCTAssertTrue(server.requests.contains { $0.hasPrefix("BATCH") })

        // Mismatched identity refuses active daemon and uses target-pinned checkedShell (fails since fake adb returns empty)
        let foreignDevice = AdbPresetDevice(adb: client, identity: "192.168.1.99")
        XCTAssertThrowsError(try foreignDevice.capture())
    }

    func testSetInputSourcePrefersDaemonThenFallsBackToRawAdbAndDirectHotkeys() throws {
        XCTAssertEqual(AdbClient.formatBatchLine(.setSource(sourceId: 29)), "SET_SOURCE 29")

        #if os(macOS)
        let actionIds = Set(hotkeyActions.map(\.id))
        XCTAssertFalse(actionIds.contains("input_source_cycle"))
        XCTAssertTrue(actionIds.contains("input_source_hdmi1"))
        XCTAssertTrue(actionIds.contains("input_source_hdmi2"))
        XCTAssertTrue(actionIds.contains("input_source_dp"))
        XCTAssertTrue(actionIds.contains("input_source_usbc"))
        #endif

        // 1) Daemon mode sends SET_SOURCE in BATCH without raw ADB shell commands
        let server = try MockDaemonServer { req in
            if req.hasPrefix("PING") {
                return "OK MtkDirectTool v2.1 pid=77 uid=1000 put=1\nEND\n"
            }
            if req.hasPrefix("BATCH") {
                return "OK\nSTEP 0 OK SET_SOURCE 23\nEND\n"
            }
            if req.hasPrefix("QUIT") {
                return "OK BYE\nEND\n"
            }
            return "ERR UNKNOWN_CMD\nEND\n"
        }
        server.start()
        defer { server.stop() }

        let (dir, adbPath, logURL) = try makeFakeAdb()
        defer { try? FileManager.default.removeItem(at: dir) }

        let daemonClient = AdbClient(adbPath: adbPath, connectionMode: .daemon)
        daemonClient.ip = "192.168.1.20"
        daemonClient.testForwardPortOverride = server.port
        try daemonClient.setInputSource(23, check: true)
        XCTAssertTrue(server.requests.contains { $0.contains("SET_SOURCE 23") })
        let daemonLog = (try? String(contentsOf: logURL, encoding: .utf8)) ?? ""
        XCTAssertFalse(daemonLog.contains("EXTSRC_PLAY"))

        // 2) ADB mode without JAR falls back to raw ADB am force-stop + am start
        let compatClient = AdbClient(adbPath: adbPath, connectionMode: .adb)
        compatClient.ip = "192.168.1.20"
        try compatClient.setInputSource(24, check: false)
        let compatLog = try String(contentsOf: logURL, encoding: .utf8)
        XCTAssertTrue(compatLog.contains("am force-stop com.xiaomi.mitv.tvplayer"))
        XCTAssertTrue(compatLog.contains("am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY"))
        XCTAssertTrue(compatLog.contains("--ei input 24"))
    }
}
