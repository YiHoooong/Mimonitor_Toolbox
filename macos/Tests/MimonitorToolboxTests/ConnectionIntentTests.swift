import XCTest
@testable import MimonitorToolbox

final class ConnectionIntentTests: XCTestCase {
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
