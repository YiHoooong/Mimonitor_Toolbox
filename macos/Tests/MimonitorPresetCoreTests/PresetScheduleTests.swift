import XCTest
@testable import MimonitorPresetCore

final class PresetScheduleTests: XCTestCase {
    func testDailyAndOvernightIntervalsIncludeStartButExcludeEnd() {
        let cases: [(String, String, Int, Bool)] = [
            ("09:00", "10:00", 539, false), ("09:00", "10:00", 540, true),
            ("09:00", "10:00", 600, false), ("23:00", "02:00", 1380, true),
            ("23:00", "02:00", 60, true), ("23:00", "02:00", 120, false),
        ]
        for (start, end, minute, expected) in cases {
            let task = ScheduledPresetTask(presetID: "p", start: start, end: end)
            XCTAssertEqual(task.contains(minute: minute), expected)
        }
    }

    func testMalformedEqualAndDisabledIntervalsNeverRun() {
        for (start, end) in [("9:00", "10:00"), ("24:00", "10:00"),
                             ("09:60", "10:00"), ("09:00", "09:00")] {
            XCTAssertFalse(ScheduledPresetTask(presetID: "p", start: start, end: end).isValid)
        }
        let disabled = ScheduledPresetTask(presetID: "p", start: "09:00", end: "10:00", enabled: false)
        XCTAssertFalse(disabled.contains(minute: 550))
    }

    func testLastMatchingTaskWins() {
        let a = ScheduledPresetTask(presetID: "a", start: "09:00", end: "11:00")
        let b = ScheduledPresetTask(presetID: "b", start: "10:00", end: "12:00")
        XCTAssertEqual(PresetSchedule.activeTask([a, b], minute: 630)?.presetID, "b")
        XCTAssertEqual(PresetSchedule.activeTask([b, a], minute: 630)?.presetID, "a")
    }
}
