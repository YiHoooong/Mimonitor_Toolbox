import Foundation
import XCTest
@testable import MimonitorPresetCore

final class PresetTimePickerTests: XCTestCase {
    func testTimePickerBridgePreservesDailyMinutesInDifferentTimeZones() throws {
        for zone in ["Asia/Shanghai", "America/New_York"] {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(identifier: zone)!
            for (text, hour, minute) in [("00:00", 0, 0), ("02:30", 2, 30), ("23:59", 23, 59)] {
                let date = try XCTUnwrap(PresetSchedule.timeDate(text, calendar: calendar))
                XCTAssertEqual(calendar.component(.hour, from: date), hour)
                XCTAssertEqual(calendar.component(.minute, from: date), minute)
                XCTAssertEqual(PresetSchedule.timeText(date, calendar: calendar), text)
            }
        }
    }

    func testInvalidStoredTimeIsNotSilentlyConvertedToMidnight() {
        for text in ["24:00", "12:60", "2:30", "garbage"] {
            XCTAssertNil(PresetSchedule.timeDate(text))
        }
    }
}
