import XCTest
@testable import MimonitorToolbox

final class ConnectionIntentTests: XCTestCase {
    func testScanAutomaticallyConnectsOnlyOneIdentifiedMonitor() {
        var intent = ConnectionIntent()
        let request = intent.beginScan()
        let monitor = ScannedDevice(ip: "192.168.1.20", model: "MiTV-Monitor")
        let phone = ScannedDevice(ip: "192.168.1.30", model: "Pixel 8")
        XCTAssertEqual(intent.scanConnectionTarget(request: request, devices: [phone, monitor]), monitor)
        XCTAssertNil(intent.scanConnectionTarget(request: request, devices: [phone]))
        XCTAssertNil(intent.scanConnectionTarget(request: request, devices: []))
        XCTAssertNil(intent.scanConnectionTarget(request: request, devices: [monitor, ScannedDevice(ip: "192.168.1.40", model: "mitv second")]))
    }

    func testObsoleteScanCannotTriggerAutomaticConnection() {
        var intent = ConnectionIntent()
        let request = intent.beginScan()
        let devices = [ScannedDevice(ip: "192.168.1.20", model: "MiTV")]
        intent.disconnect()
        XCTAssertNil(intent.scanConnectionTarget(request: request, devices: devices))
        _ = intent.beginConnection()
        XCTAssertNil(intent.scanConnectionTarget(request: request, devices: devices))
    }
    func testStartupAttemptIsCancelledByAnExplicitAction() {
        var intent = ConnectionIntent()
        XCTAssertTrue(intent.allowsStartupAttempt)

        _ = intent.beginScan()

        XCTAssertFalse(intent.allowsStartupAttempt)
    }

    func testManualDisconnectCancelsDelayedStartupAttempt() {
        var intent = ConnectionIntent()

        intent.disconnect()

        XCTAssertFalse(intent.allowsStartupAttempt)
    }

    func testManualDisconnectStopsRecoveryUntilExplicitConnect() {
        var intent = ConnectionIntent()
        let oldRequest = intent.beginConnection()

        intent.disconnect()
        XCTAssertFalse(intent.allowsAutomaticRecovery)
        XCTAssertFalse(intent.isCurrent(oldRequest))

        let newRequest = intent.beginConnection()
        XCTAssertTrue(intent.allowsAutomaticRecovery)
        XCTAssertTrue(intent.isCurrent(newRequest))
    }

    func testScanResultCannotReplaceANewerConnection() {
        var intent = ConnectionIntent()
        let scanRequest = intent.beginScan()
        let connectionRequest = intent.beginConnection()

        XCTAssertFalse(intent.isCurrent(scanRequest))
        XCTAssertTrue(intent.isCurrent(connectionRequest))
    }

    func testScanningStopsRecoveryOfThePreviousDevice() {
        var intent = ConnectionIntent()
        _ = intent.beginConnection()

        _ = intent.beginScan()

        XCTAssertFalse(intent.allowsAutomaticRecovery)
    }

    func testDisconnectInvalidatesAnInFlightScan() {
        var intent = ConnectionIntent()
        let scanRequest = intent.beginScan()

        intent.disconnect()

        XCTAssertFalse(intent.isCurrent(scanRequest))
    }
}
