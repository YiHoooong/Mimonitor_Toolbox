import Foundation
import XCTest
@testable import MimonitorToolbox

final class AdbClientTests: XCTestCase {
    private func fake(_ script: String, test: (AdbClient) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("preset-adb-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("adb")
        try ("#!/bin/sh\n" + script).write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        let client = AdbClient(adbPath: executable.path)
        client.ip = "192.168.1.20"
        try test(client)
    }

    func testCheckedShellRejectsNonzeroExitAndBinderExceptions() throws {
        try fake("echo 'device offline'; exit 1\n") { client in
            XCTAssertThrowsError(try client.checkedShell("settings list global", target: "192.168.1.20:5555"))
        }
        try fake("echo 'Parcel: Exception occurred'; exit 0\n") { client in
            XCTAssertThrowsError(try client.checkedShell("service call TvService", target: "192.168.1.20:5555"))
        }
    }

    func testCheckedShellUsesCapturedTargetNotChangedIp() throws {
        try fake("printf '%s\\n' \"$@\"\n") { client in
            client.ip = "192.168.1.30"
            let output = try client.checkedShell("echo test", target: "192.168.1.20:5555")
            XCTAssertTrue(output.contains("192.168.1.20:5555"))
            XCTAssertFalse(output.contains("192.168.1.30:5555"))
        }
    }

    func testTransactionPreventsCommandsFromInterleavingWithPresetBatch() throws {
        try fake("printf '%s\\n' \"$@\"\n") { client in
            let lock = NSLock()
            var order: [String] = []
            let started = DispatchSemaphore(value: 0)
            let contenderStarted = DispatchSemaphore(value: 0)
            let finished = DispatchGroup()
            finished.enter()
            DispatchQueue.global().async {
                client.transaction {
                    _ = client.shell("first")
                    lock.lock(); order.append("first"); lock.unlock()
                    started.signal()
                    contenderStarted.wait()
                    Thread.sleep(forTimeInterval: 0.05)
                    _ = client.shell("second")
                    lock.lock(); order.append("second"); lock.unlock()
                }
                finished.leave()
            }
            finished.enter()
            DispatchQueue.global().async {
                started.wait()
                contenderStarted.signal()
                _ = client.shell("contender")
                lock.lock(); order.append("contender"); lock.unlock()
                finished.leave()
            }
            XCTAssertEqual(finished.wait(timeout: .now() + 5), .success)
            XCTAssertEqual(order, ["first", "second", "contender"])
        }
    }

    func testDisconnectUsesCapturedTargetAfterCurrentIpWasCleared() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("mimonitor-adb-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let fakeAdb = directory.appendingPathComponent("adb")
        try "#!/bin/sh\nprintf '%s\\n' \"$@\"\n".write(to: fakeAdb, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: fakeAdb.path)

        let client = AdbClient(adbPath: fakeAdb.path)
        client.ip = ""

        XCTAssertEqual(client.disconnect(ip: "192.168.1.20"), "disconnect\n192.168.1.20:5555\n")
    }
}
