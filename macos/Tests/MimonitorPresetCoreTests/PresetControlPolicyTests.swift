import XCTest
@testable import MimonitorPresetCore

final class PresetControlPolicyTests: XCTestCase {
    func testActivePresetLocksBothDirectionsOfMemoryChangesWithoutErasingSavedPreference() {
        var configuration = PresetConfiguration()
        configuration.activePresetID = "gaming"
        configuration.activeDeviceIdentity = "monitor-a"
        let policy = PresetControlPolicy(configuration: configuration, deviceIdentity: "monitor-a", isSwitching: false)

        XCTAssertTrue(policy.memoriesLocked)
        XCTAssertFalse(policy.effectiveMemoryEnabled(savedPreference: true))
        XCTAssertNil(policy.memoryPreferenceChange(requested: true))
        XCTAssertNil(policy.memoryPreferenceChange(requested: false))

        // Returning to no preset restores the preference, not a forced-off value.
        configuration.activePresetID = nil
        let restored = PresetControlPolicy(configuration: configuration, deviceIdentity: "monitor-a", isSwitching: false)
        XCTAssertFalse(restored.memoriesLocked)
        XCTAssertTrue(restored.effectiveMemoryEnabled(savedPreference: true))
        XCTAssertEqual(restored.memoryPreferenceChange(requested: false), false)
    }

    func testSwitchingAndIncompleteApplicationsKeepMemoryControlsLocked() {
        let switching = PresetControlPolicy(configuration: PresetConfiguration(), deviceIdentity: "monitor-a", isSwitching: true)
        XCTAssertTrue(switching.memoriesLocked)
        XCTAssertNil(switching.memoryPreferenceChange(requested: true))

        var configuration = PresetConfiguration()
        configuration.applicationIncomplete = true
        let incomplete = PresetControlPolicy(configuration: configuration, deviceIdentity: "monitor-a", isSwitching: false)
        XCTAssertTrue(incomplete.memoriesLocked)
        XCTAssertFalse(incomplete.effectiveMemoryEnabled(savedPreference: true))
        XCTAssertNil(incomplete.memoryPreferenceChange(requested: false))
    }

    func testPresetForAnotherDeviceDoesNotLockThisDevicesMemoryControls() {
        var configuration = PresetConfiguration()
        configuration.activePresetID = "gaming"
        configuration.activeDeviceIdentity = "monitor-a"
        let policy = PresetControlPolicy(configuration: configuration, deviceIdentity: "monitor-b", isSwitching: false)
        XCTAssertFalse(policy.memoriesLocked)
        XCTAssertTrue(policy.effectiveMemoryEnabled(savedPreference: true))
        XCTAssertEqual(policy.memoryPreferenceChange(requested: true), true)
    }
}
