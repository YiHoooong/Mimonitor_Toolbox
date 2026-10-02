import Foundation

/// Shared by the memory switches and their action handlers.
public struct PresetControlPolicy {
    private let configuration: PresetConfiguration
    private let deviceIdentity: String?
    private let isSwitching: Bool

    public init(configuration: PresetConfiguration, deviceIdentity: String?, isSwitching: Bool) {
        self.configuration = configuration
        self.deviceIdentity = deviceIdentity
        self.isSwitching = isSwitching
    }

    public var memoriesLocked: Bool {
        let hasActivePreset = configuration.activePresetID != nil
            && configuration.activeDeviceIdentity == deviceIdentity
        return hasActivePreset || isSwitching || configuration.applicationIncomplete
    }
    public func effectiveMemoryEnabled(savedPreference: Bool) -> Bool {
        savedPreference && !memoriesLocked
    }
    public func memoryPreferenceChange(requested: Bool) -> Bool? {
        memoriesLocked ? nil : requested
    }
}
