import Foundation
import XCTest
@testable import MimonitorToolbox

final class KnownDeviceTests: XCTestCase {
    func testDeviceIdentityParsesStructuredOutputWithSerialPriority() {
        let output = """
        model=MiTV-MUFF1
        serial=SN12345678
        boot_serial=BOOT999
        wlan_mac=aa:bb:cc:dd:ee:ff
        eth_mac=11:22:33:44:55:66
        """
        let identity = DeviceIdentity.parse(shellOutput: output)
        XCTAssertEqual(identity, DeviceIdentity(model: "MiTV-MUFF1", serial: "SN12345678"))
    }

    func testDeviceIdentityFallsBackToBootSerialThenMacAndIgnoresPlaceholders() {
        let withBootSerial = """
        model=MiTV-MUFF1
        serial=unknown
        boot_serial=BOOT-XYZ-001
        wlan_mac=aa:bb:cc:dd:ee:ff
        eth_mac=
        """
        XCTAssertEqual(
            DeviceIdentity.parse(shellOutput: withBootSerial),
            DeviceIdentity(model: "MiTV-MUFF1", serial: "BOOT-XYZ-001")
        )

        let withMacOnly = """
        model=MiTV-MUFF1
        serial=0123456789abcdef
        boot_serial=
        wlan_mac=02:00:00:00:00:00
        eth_mac=10:20:30:ab:cd:ef
        """
        XCTAssertEqual(
            DeviceIdentity.parse(shellOutput: withMacOnly),
            DeviceIdentity(model: "MiTV-MUFF1", serial: "MAC-102030ABCDEF")
        )
    }

    func testDeviceIdentityRemainsCompatibleWithSingleLineModelOutput() {
        XCTAssertEqual(
            DeviceIdentity.parse(shellOutput: "MiTV-Monitor\n"),
            DeviceIdentity(model: "MiTV-Monitor", serial: "")
        )
        XCTAssertNil(DeviceIdentity.parse(shellOutput: "error: device offline"))
        XCTAssertNil(DeviceIdentity.parse(shellOutput: "model=\nserial=ABC123"))
    }

    func testUpsertDistinguishesTwoSameModelMonitorsBySerialAndPreservesAliases() {
        let t1 = Date(timeIntervalSince1970: 1_000)
        let t2 = Date(timeIntervalSince1970: 2_000)
        let t3 = Date(timeIntervalSince1970: 3_000)

        var devices: [KnownDevice] = []
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "SN-LEFT", now: t1)
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.21", model: "MiTV-MUFF1", serial: "SN-RIGHT", now: t2)
        devices = KnownDeviceStore.setAlias(devices, id: "SN-LEFT", alias: "左屏")
        devices = KnownDeviceStore.setAlias(devices, id: "SN-RIGHT", alias: "右屏")

        XCTAssertEqual(devices.count, 2)
        XCTAssertEqual(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.20")?.label, "左屏 (192.168.1.20)")
        XCTAssertEqual(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.21")?.label, "右屏 (192.168.1.21)")

        // 路由器 DHCP 给左屏分配了新 IP，序列号匹配后自动更新 IP 且保留备注
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.88", model: "MiTV-MUFF1", serial: "SN-LEFT", now: t3)
        XCTAssertEqual(devices.count, 2)
        XCTAssertNil(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.20"))
        XCTAssertEqual(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.88")?.label, "左屏 (192.168.1.88)")
    }

    func testUpsertMigratesLegacyIPRecordWhenSerialBecomesAvailable() {
        var devices: [KnownDevice] = []
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "")
        devices = KnownDeviceStore.setAlias(devices, id: "ip:192.168.1.20", alias: "主屏")

        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "SN-MAIN")
        XCTAssertEqual(devices.count, 1)
        XCTAssertEqual(devices[0].id, "SN-MAIN")
        XCTAssertEqual(devices[0].serial, "SN-MAIN")
        XCTAssertEqual(devices[0].trimmedAlias, "主屏")
    }

    func testUpsertClearsStaleIPWhenAnotherSerialTakesOverSameIP() {
        var devices: [KnownDevice] = []
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "SN-A")
        devices = KnownDeviceStore.setAlias(devices, id: "SN-A", alias: "左屏")

        // SN-B 拿到了 192.168.1.20：SN-A 的 IP 被清空避免冲突，但备注仍保留
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "SN-B")
        XCTAssertEqual(devices.count, 2)
        XCTAssertEqual(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.20")?.id, "SN-B")
        let deviceA = devices.first(where: { $0.id == "SN-A" })
        XCTAssertEqual(deviceA?.ip, "")
        XCTAssertEqual(deviceA?.trimmedAlias, "左屏")

        // 稍后扫到 SN-A 的新 IP，自动恢复可连接状态与原备注
        devices = KnownDeviceStore.upsert(devices, ip: "192.168.1.25", model: "MiTV-MUFF1", serial: "SN-A")
        XCTAssertEqual(KnownDeviceStore.device(in: devices, matchingIP: "192.168.1.25")?.label, "左屏 (192.168.1.25)")
    }

    func testStoreSaveLoadAndRemoveRoundTrip() {
        let suiteName = "KnownDeviceTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        var devices = KnownDeviceStore.upsert([], ip: "192.168.1.20", model: "MiTV-MUFF1", serial: "SN-01")
        devices = KnownDeviceStore.setAlias(devices, id: "SN-01", alias: "副屏")
        KnownDeviceStore.save(devices, defaults: defaults)

        let loaded = KnownDeviceStore.load(defaults: defaults)
        XCTAssertEqual(loaded, devices)

        let removed = KnownDeviceStore.remove(loaded, id: "SN-01")
        XCTAssertTrue(removed.isEmpty)
    }
}
