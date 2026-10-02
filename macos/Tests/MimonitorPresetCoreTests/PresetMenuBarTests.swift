import Foundation
import XCTest
@testable import MimonitorPresetCore

final class PresetMenuBarTests: XCTestCase {
    func testVisibilityPersistsAndFiltersByIdentityRatherThanName() throws {
        let suite = "menu-preset-test-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PresetStore(defaults: defaults)
        var config = PresetConfiguration()
        config.presets = [PicturePreset(id: "a", name: "办公", values: ["picture_backlight": "40"]),
                          PicturePreset(id: "b", name: "游戏", values: ["picture_backlight": "80"])]
        try store.save(config)
        let engine = PresetEngine(store: store)
        try engine.setMenuBarVisibility(id: "b", visible: true)
        try engine.setMenuBarVisibility(id: PicturePreset.baselineID, visible: true)
        try engine.setMenuBarVisibility(id: "b", visible: true)
        try engine.rename(id: "b", name: "新游戏")

        let reloaded = PresetStore(defaults: defaults).configuration
        XCTAssertEqual(Set(reloaded.menuBarPresetIDs), ["b", "__baseline__"])
        XCTAssertEqual(reloaded.menuBarPresetIDs.count, 2)
        XCTAssertEqual(reloaded.menuBarPresets.map(\.id), ["__baseline__", "b"])
        XCTAssertEqual(reloaded.menuBarPresets.last?.name, "新游戏")
        XCTAssertNil(reloaded.activePresetID, "Visibility changes must not apply any preset")

        try engine.setMenuBarVisibility(id: "b", visible: false)
        XCTAssertEqual(engine.configuration.menuBarPresets.map(\.id), ["__baseline__"])
    }

    func testDeletedPresetsLoseTheirMenuBarEntryAndUnknownIdsAreRejected() throws {
        let suite = "menu-preset-test-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PresetStore(defaults: defaults)
        var config = PresetConfiguration()
        config.presets = [PicturePreset(id: "a", name: "办公", values: ["picture_backlight": "40"])]
        config.menuBarPresetIDs = ["a"]
        try store.save(config)
        let engine = PresetEngine(store: store)
        XCTAssertThrowsError(try engine.setMenuBarVisibility(id: "missing", visible: true))
        try engine.delete(id: "a", device: nil)
        XCTAssertTrue(engine.configuration.menuBarPresetIDs.isEmpty)
        XCTAssertTrue(engine.configuration.menuBarPresets.isEmpty)
    }

    func testOldConfigurationStillLoadsAndDoesNotExposeDeletedMenuTargets() throws {
        let old = Data(#"{"presets":[{"id":"a","name":"办公","values":{}}]}"#.utf8)
        var configuration = try JSONDecoder().decode(PresetConfiguration.self, from: old)
        XCTAssertTrue(configuration.menuBarPresetIDs.isEmpty)
        configuration.menuBarPresetIDs = ["missing", "a", "a"]
        XCTAssertEqual(configuration.menuBarPresets.map(\.id), ["a"])
    }
}
