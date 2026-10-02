import XCTest
@testable import MimonitorToolbox

final class PageRefreshIntentTests: XCTestCase {
    func testNewerRefreshInvalidatesOlderResultForTheSamePage() {
        var intent = PageRefreshIntent()
        let old = intent.begin("picture")
        let latest = intent.begin("picture")

        XCTAssertFalse(intent.isCurrent("picture", old))
        XCTAssertTrue(intent.isCurrent("picture", latest))
    }

    func testAnotherPagesRefreshDoesNotInvalidateThisPage() {
        var intent = PageRefreshIntent()
        let picture = intent.begin("picture")
        _ = intent.begin("game")

        XCTAssertTrue(intent.isCurrent("picture", picture))
    }
}
