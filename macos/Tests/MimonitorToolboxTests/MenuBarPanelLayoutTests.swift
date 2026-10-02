import XCTest
@testable import MimonitorToolbox

final class MenuBarPanelLayoutTests: XCTestCase {
    func testShortPresetRowsFillTheAvailableWidthRatherThanTheirIntrinsicWidth() {
        for count in [1, 2, 3] {
            XCTAssertEqual(MenuBarPanelLayout.presetWidth(count: count), 292)
        }
        XCTAssertEqual(MenuBarPanelLayout.presetWidth(count: 5), 400)
    }

    func testPanelFitsItsContentAndScrollsOnlyWhenItReachesTheHeightLimit() {
        XCTAssertEqual(MenuBarPanelLayout.scrollHeight(contentHeight: 64), 64)
        XCTAssertEqual(MenuBarPanelLayout.scrollHeight(contentHeight: 180), 180)
        XCTAssertEqual(MenuBarPanelLayout.scrollHeight(contentHeight: 900), 420)
        XCTAssertEqual(MenuBarPanelLayout.scrollHeight(contentHeight: 0), 1)
    }
}
