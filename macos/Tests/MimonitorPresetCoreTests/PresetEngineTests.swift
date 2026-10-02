import Foundation
import XCTest
@testable import MimonitorPresetCore

/// Only the physical monitor is replaced. Persistence and transitions are real.
private final class Monitor: PresetDevice {
    var identity = "192.168.1.20"
    var values = ["picture_mode": "14", "picture_backlight": "40"]
    var failure = false
    func capture() throws -> [String: String] { values }
    func apply(_ values: [String: String]) throws -> PresetApplyReport {
        if failure { return PresetApplyReport(applied: [], failed: ["背光: 写入失败"], skipped: []) }
        self.values = values
        return PresetApplyReport(applied: ["画面模式", "背光"], failed: [], skipped: [])
    }
}

final class PresetEngineTests: XCTestCase {
    private var suites: [String] = []
    override func tearDown() {
        for suite in suites { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        super.tearDown()
    }
    private func store() -> PresetStore {
        let suite = "preset-test-\(UUID().uuidString)"
        suites.append(suite)
        let defaults = UserDefaults(suiteName: suite)!
        return PresetStore(defaults: defaults)
    }

    func testSwitchingPresetsDoesNotOverwriteBaselineAndRestoreClearsActive() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let first = try engine.create(name: "日间", device: monitor)
        monitor.values["picture_backlight"] = "20"
        let second = try engine.create(name: "夜间", device: monitor)
        try engine.apply(id: first.id, device: monitor)
        try engine.apply(id: second.id, device: monitor)
        try engine.restore(device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "40")
        XCTAssertNil(engine.configuration.activePresetID)
    }

    func testCreateAndRenameAvoidDuplicateNamesWithoutBreakingTaskReference() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let first = try engine.create(name: "阅读", device: monitor)
        let second = try engine.create(name: " 阅读 ", device: monitor)
        XCTAssertEqual(second.name, "阅读 (2)")
        let task = ScheduledPresetTask(presetID: first.id, start: "09:00", end: "10:00")
        try engine.saveTask(task)
        try engine.rename(id: first.id, name: "办公")
        XCTAssertEqual(engine.configuration.tasks.first?.presetID, first.id)
    }

    func testAutosaveOnlyUpdatesThePresetCapturedByTheEdit() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let first = try engine.create(name: "一", device: monitor)
        let second = try engine.create(name: "二", device: monitor)
        monitor.values["picture_backlight"] = "80"
        try engine.synchronize(id: first.id, device: monitor)
        XCTAssertEqual(engine.configuration.presets.first?.values["picture_backlight"], "40")
        try engine.synchronize(id: second.id, device: monitor)
        XCTAssertEqual(engine.configuration.presets.last?.values["picture_backlight"], "80")
    }

    func testFailedApplyKeepsBaselineAndDoesNotMarkTargetActive() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let first = try engine.create(name: "一", device: monitor)
        try engine.restore(device: monitor)
        monitor.failure = true
        XCTAssertThrowsError(try engine.apply(id: first.id, device: monitor))
        XCTAssertNil(engine.configuration.activePresetID)
        XCTAssertEqual(engine.configuration.baseline?.values["picture_backlight"], "40")
    }

    func testOverlappingTasksReturnToOriginalPresetIdentity() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let manual = try engine.create(name: "手动", device: monitor)
        monitor.values["picture_backlight"] = "70"
        let day = try engine.create(name: "日间", device: monitor)
        monitor.values["picture_backlight"] = "20"
        let night = try engine.create(name: "夜间", device: monitor)
        try engine.apply(id: manual.id, device: monitor)
        try engine.saveTask(ScheduledPresetTask(presetID: day.id, start: "09:00", end: "11:00"))
        try engine.saveTask(ScheduledPresetTask(presetID: night.id, start: "10:00", end: "12:00"))
        try engine.reconcile(minute: 9 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "70")
        try engine.reconcile(minute: 10 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "20")
        try engine.reconcile(minute: 12 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "40")
        XCTAssertEqual(engine.configuration.activePresetID, manual.id)
        XCTAssertNil(engine.configuration.session)
    }

    func testDisconnectedTaskIsNotExecutedOutsideItsTimeWindow() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "一", device: monitor)
        try engine.restore(device: monitor)
        try engine.saveTask(ScheduledPresetTask(presetID: preset.id, start: "09:00", end: "10:00"))
        try engine.reconcile(minute: 9 * 60, device: nil)
        XCTAssertNil(engine.configuration.session)
        monitor.values["picture_backlight"] = "55"
        try engine.reconcile(minute: 10 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "55")
    }

    func testEndedTaskReturnsToBaselineAfterReconnectAndStoreReload() throws {
        let storage = store()
        let engine = PresetEngine(store: storage)
        let monitor = Monitor()
        let preset = try engine.create(name: "一", device: monitor)
        try engine.restore(device: monitor)
        monitor.values["picture_backlight"] = "55"
        try engine.saveTask(ScheduledPresetTask(presetID: preset.id, start: "09:00", end: "10:00"))
        try engine.reconcile(minute: 9 * 60, device: monitor)
        try engine.reconcile(minute: 10 * 60, device: nil)
        let reloaded = PresetEngine(store: PresetStore(defaults: storage.defaults))
        try reloaded.reconcile(minute: 10 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "55")
        XCTAssertNil(reloaded.configuration.session)
    }

    func testTaskFailureRetriesWithoutReplacingBaseline() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "一", device: monitor)
        try engine.restore(device: monitor)
        monitor.values["picture_backlight"] = "55"
        try engine.saveTask(ScheduledPresetTask(presetID: preset.id, start: "09:00", end: "10:00"))
        monitor.failure = true
        XCTAssertThrowsError(try engine.reconcile(minute: 9 * 60, device: monitor))
        monitor.failure = false
        monitor.values["picture_backlight"] = "99"
        try engine.reconcile(minute: 9 * 60 + 1, device: monitor)
        try engine.reconcile(minute: 10 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "55")
    }

    func testBaselineTaskAndDeletionReturnToPreviousPreset() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "一", device: monitor)
        monitor.values["picture_backlight"] = "20"
        try engine.synchronize(id: preset.id, device: monitor)
        let task = ScheduledPresetTask(presetID: PicturePreset.baselineID, start: "09:00", end: "10:00")
        try engine.saveTask(task)
        try engine.reconcile(minute: 9 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "40")
        try engine.deleteTask(id: task.id)
        try engine.reconcile(minute: 9 * 60, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "20")
        XCTAssertEqual(engine.configuration.activePresetID, preset.id)
    }

    func testCannotRestoreSnapshotToDifferentMonitor() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        _ = try engine.create(name: "一", device: monitor)
        monitor.identity = "192.168.1.30"
        monitor.values["picture_backlight"] = "99"
        XCTAssertThrowsError(try engine.restore(device: monitor))
        XCTAssertEqual(monitor.values["picture_backlight"], "99")
    }

    func testTaskReturnsToLatestSavedPresetValuesNotOldDeviceValues() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let previous = try engine.create(name: "电影", device: monitor)
        monitor.values["picture_backlight"] = "70"
        let game = try engine.create(name: "游戏", device: monitor)
        try engine.apply(id: previous.id, device: monitor)
        let task = ScheduledPresetTask(presetID: game.id, start: "09:00", end: "10:00")
        try engine.saveTask(task)
        try engine.reconcile(minute: 540, device: monitor)
        // User temporarily switches to and edits the original preset during the task.
        try engine.apply(id: previous.id, device: monitor)
        monitor.values["picture_backlight"] = "33"
        try engine.synchronize(id: previous.id, device: monitor)
        try engine.apply(id: game.id, device: monitor)
        try engine.reconcile(minute: 600, device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "33")
        XCTAssertEqual(engine.configuration.activePresetID, previous.id)
    }

    func testDeletedReturnPresetFallsBackToBaseline() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let previous = try engine.create(name: "电影", device: monitor)
        monitor.values["picture_backlight"] = "70"
        let game = try engine.create(name: "游戏", device: monitor)
        try engine.apply(id: previous.id, device: monitor)
        try engine.saveTask(ScheduledPresetTask(presetID: game.id, start: "09:00", end: "10:00"))
        try engine.reconcile(minute: 540, device: monitor)
        try engine.delete(id: previous.id, device: monitor)
        try engine.reconcile(minute: 600, device: monitor)
        XCTAssertNil(engine.configuration.activePresetID)
        XCTAssertEqual(monitor.values["picture_backlight"], "40")
    }

    func testDifferentMonitorCannotReplaceBaselineOfUnfinishedTask() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "游戏", device: monitor)
        try engine.restore(device: monitor)
        try engine.saveTask(ScheduledPresetTask(presetID: preset.id, start: "09:00", end: "10:00"))
        try engine.reconcile(minute: 540, device: monitor)
        monitor.identity = "192.168.1.30"
        monitor.values["picture_backlight"] = "99"
        XCTAssertThrowsError(try engine.apply(id: preset.id, device: monitor))
        XCTAssertThrowsError(try engine.create(name: "另一台", device: monitor))
        XCTAssertEqual(engine.configuration.baseline?.deviceIdentity, "192.168.1.20")
        XCTAssertEqual(monitor.values["picture_backlight"], "99")
    }

    func testManualApplyRetryDoesNotReplaceBaselineWithPartialFailedValues() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "游戏", device: monitor)
        try engine.restore(device: monitor)
        monitor.values["picture_backlight"] = "60"
        monitor.failure = true
        XCTAssertThrowsError(try engine.apply(id: preset.id, device: monitor))
        // Some items reached hardware despite the failed batch.
        monitor.values["picture_backlight"] = "99"
        monitor.failure = false
        try engine.apply(id: preset.id, device: monitor)
        try engine.restore(device: monitor)
        XCTAssertEqual(monitor.values["picture_backlight"], "60")
    }

    func testFailedSwitchCannotAutosaveMixedValuesIntoOriginalPreset() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let previous = try engine.create(name: "电影", device: monitor)
        monitor.values["picture_backlight"] = "70"
        let game = try engine.create(name: "游戏", device: monitor)
        try engine.apply(id: previous.id, device: monitor)
        monitor.failure = true
        XCTAssertThrowsError(try engine.apply(id: game.id, device: monitor))
        monitor.values["picture_backlight"] = "99"
        XCTAssertThrowsError(try engine.synchronize(id: previous.id, device: monitor))
        XCTAssertEqual(engine.configuration.presets.first?.values["picture_backlight"], "40")
    }

    func testNextDailyOccurrenceReappliesAfterMissedInactiveGap() throws {
        let engine = PresetEngine(store: store())
        let monitor = Monitor()
        let preset = try engine.create(name: "游戏", device: monitor)
        try engine.restore(device: monitor)
        try engine.saveTask(ScheduledPresetTask(presetID: preset.id, start: "09:00", end: "10:00"))
        let day = Date(timeIntervalSince1970: 1_700_000_000)
        try engine.reconcile(minute: 540, device: monitor, now: day)
        monitor.values["picture_backlight"] = "99"
        try engine.reconcile(minute: 540, device: monitor, now: day.addingTimeInterval(86400))
        XCTAssertEqual(monitor.values["picture_backlight"], "40")
        try engine.reconcile(minute: 600, device: monitor, now: day.addingTimeInterval(86400))
        XCTAssertNil(engine.configuration.activePresetID)
    }
}
