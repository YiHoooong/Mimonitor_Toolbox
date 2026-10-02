import Foundation
import XCTest
@testable import MimonitorPresetCore

final class PresetMenuBarTests: XCTestCase {
    func testFourSlotLimitIncludesBaselineAndCanBeFreedByRemovingASelection() throws {
        let suite = "menu-preset-limit-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PresetStore(defaults: defaults)
        var config = PresetConfiguration()
        config.presets = (1...5).map { PicturePreset(id: "p\($0)", name: "预设\($0)", values: [:]) }
        try store.save(config)
        let engine = PresetEngine(store: store)
        for id in ["__baseline__", "p1", "p2", "p3"] {
            try engine.setMenuBarVisibility(id: id, visible: true)
        }
        XCTAssertFalse(engine.configuration.canEnableMenuBarPreset(id: "p4"))
        XCTAssertTrue(engine.configuration.canEnableMenuBarPreset(id: "p1"))
        XCTAssertThrowsError(try engine.setMenuBarVisibility(id: "p4", visible: true))
        XCTAssertEqual(engine.configuration.menuBarPresets.count, 4)
        // Re-enabling an existing selection is idempotent, even when all slots are occupied.
        try engine.setMenuBarVisibility(id: "p1", visible: true)
        XCTAssertEqual(engine.configuration.menuBarPresets.count, 4)
        try engine.setMenuBarVisibility(id: "p1", visible: false)
        XCTAssertTrue(engine.configuration.canEnableMenuBarPreset(id: "p4"))
        try engine.setMenuBarVisibility(id: "p4", visible: true)
        XCTAssertEqual(engine.configuration.menuBarPresets.map(\.id), ["__baseline__", "p2", "p3", "p4"])
    }

    func testOldOverLimitConfigurationKeepsOnlyFirstFourValidUniqueSelections() throws {
        var config = PresetConfiguration()
        config.presets = (1...5).map { PicturePreset(id: "p\($0)", name: "预设\($0)", values: [:]) }
        config.menuBarPresetIDs = ["missing", "__baseline__", "p1", "p1", "p2", "p3", "p4", "p5"]
        let reloaded = try JSONDecoder().decode(PresetConfiguration.self, from: JSONEncoder().encode(config))
        XCTAssertEqual(reloaded.menuBarPresetIDs, ["__baseline__", "p1", "p2", "p3"])
        XCTAssertEqual(reloaded.menuBarPresets.count, 4)
        XCTAssertEqual(reloaded.presets.count, 5, "Menu limits must never remove saved presets")
    }

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
