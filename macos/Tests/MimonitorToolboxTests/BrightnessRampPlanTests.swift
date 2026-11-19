import XCTest
@testable import MimonitorToolbox

final class BrightnessRampPlanTests: XCTestCase {
    func testSmallChangesWriteDirectly() {
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 50, to: 51),
                       BrightnessWritePlan(useRamp: false, step: 1, stepMs: 0))
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 50, to: 52),
                       BrightnessWritePlan(useRamp: false, step: 2, stepMs: 0))
    }

    func testOneNativeOSDChicletUsesOnePercentMicroSteps() {
        // 6.25% 一格：6 -> 6 次写；7 -> 7 次写，间隔 8ms。
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 31, to: 37),
                       BrightnessWritePlan(useRamp: true, step: 1, stepMs: 8))
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 31, to: 38),
                       BrightnessWritePlan(useRamp: true, step: 1, stepMs: 8))
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 38, to: 31),
                       BrightnessWritePlan(useRamp: true, step: 1, stepMs: 8))
    }

    func testLargerCoalescedJumpsKeepFastRamp() {
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 31, to: 43),
                       BrightnessWritePlan(useRamp: true, step: 1, stepMs: 0))
        XCTAssertEqual(BrightnessRampPlanner.plan(from: 20, to: 100),
                       BrightnessWritePlan(useRamp: true, step: 5, stepMs: 0))
    }
}
