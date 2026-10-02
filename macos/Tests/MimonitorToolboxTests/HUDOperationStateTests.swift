import XCTest
@testable import MimonitorToolbox

final class HUDOperationStateTests: XCTestCase {
    func testCompletionRequiresVerifiedApplicationsOnTheCurrentConnection() {
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: true, hasError: false, verifiedApplications: [true]), .success)
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: true, hasError: false, verifiedApplications: [true, false]), .failure)
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: true, hasError: true, verifiedApplications: [true]), .failure)
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: true, hasError: false, verifiedApplications: []), .cancelled)
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: false, hasError: false, verifiedApplications: [true]), .cancelled)
        XCTAssertEqual(HUDOperationResult.completion(isCurrentConnection: false, hasError: true, verifiedApplications: [false]), .cancelled)
    }

    func testSpinnerStaysVisibleAndCannotBeReplacedByAnUnrelatedValueHint() {
        var state = HUDOperationState()
        let token = state.begin()
        XCTAssertEqual(state.phase, .running)
        XCTAssertTrue(state.keepsVisible)
        XCTAssertFalse(state.acceptsValueHint)
        state.showValueHint()
        XCTAssertEqual(state.phase, .running)
        XCTAssertTrue(state.finish(token: token, result: .success))
        XCTAssertEqual(state.phase, .finished(.success))
        XCTAssertFalse(state.keepsVisible)
        XCTAssertTrue(state.acceptsValueHint)
        state.showValueHint()
        XCTAssertNil(state.phase)
    }

    func testFailureAndCancellationNeverBecomeASuccessCheckmark() {
        for result in [HUDOperationResult.failure, .cancelled] {
            var state = HUDOperationState()
            let token = state.begin()
            XCTAssertTrue(state.finish(token: token, result: result))
            XCTAssertEqual(state.phase, .finished(result))
            XCTAssertFalse(state.keepsVisible)
            XCTAssertFalse(state.finish(token: token, result: .success))
            XCTAssertEqual(state.phase, .finished(result))
        }
    }

    func testOldCompletionCannotOverwriteTheSpinnerForANewerSwitch() {
        var state = HUDOperationState()
        let old = state.begin()
        let latest = state.begin()
        XCTAssertFalse(state.finish(token: old, result: .success))
        XCTAssertEqual(state.phase, .running)
        XCTAssertTrue(state.finish(token: latest, result: .success))
        XCTAssertEqual(state.phase, .finished(.success))
    }
}
