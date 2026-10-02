import Foundation
import XCTest
@testable import MimonitorToolbox

final class AdbClientTests: XCTestCase {
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
