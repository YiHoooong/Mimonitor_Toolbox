import XCTest
@testable import MimonitorPresetCore

final class PicturePresetPlanTests: XCTestCase {
    private let values = [
        "freesync": "0", "picture_mode": "18", "tv_picture_light_sensor": "1",
        "picture_backlight": "52", "picture_brightness": "50", "picture_contrast": "60",
        "picture_saturation": "51", "picture_hue": "49", "picture_sharpness": "30",
        "picture_color_temperature": "3", "picture_red_gain": "1024",
        "picture_green_gain": "1100", "picture_blue_gain": "950",
        "picture_local_dimming": "3", "settings_display_hdr_color_tone": "0",
        "picture_dynamic_definition": "0", "picture_response_time": "2",
        "tv_picture_advanced_video_color_space": "6",
    ]

    func testFreeSyncAndModeAreWrittenBeforeModeDependentParameters() {
        var commands: [PresetCommand] = []
        let result = PicturePresetPlan.apply(values, source: "29") { commands.append($0) }
        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.applied.count, 16)
        XCTAssertEqual(Array(commands.prefix(3)), [
            .jni("g_video__dp_adaptive_sync", "0", 3), .refresh,
            .setting("picture_mode", "18"),
        ])
        XCTAssertTrue(commands.contains(.jni("g_video__light_sensor_switch", "1", 1)))
        XCTAssertTrue(commands.contains(.colorGains("1024", "1100", "950")))
        XCTAssertTrue(commands.contains(.hdrToneMapping("5")))
        XCTAssertTrue(commands.contains(.setting("picture_hdr_tone_mapping", "5")))
        XCTAssertTrue(commands.contains(.setting("settings_display_hdr_color_tone", "0")))
        XCTAssertTrue(commands.contains(.setting("tv_picture_video_color_space", "6")))
    }

    func testHdmiFreeSyncUsesItsOwnOnEnum() {
        var changed = values; changed["freesync"] = "1"
        var first: PresetCommand?
        _ = PicturePresetPlan.apply(changed, source: "23") { if first == nil { first = $0 } }
        XCTAssertEqual(first, .jni("g_video__freesync_switch", "3", 3))
    }

    func testStandardTemperatureAndSdrSkipInapplicableCommands() {
        var changed = values
        changed["picture_mode"] = "14"; changed["picture_color_temperature"] = "1"
        var commands: [PresetCommand] = []
        let report = PicturePresetPlan.apply(changed, source: "30") { commands.append($0) }
        XCTAssertTrue(report.ok)
        XCTAssertEqual(report.skipped.count, 2)
        XCTAssertFalse(commands.contains(.colorGains("1024", "1100", "950")))
        XCTAssertFalse(commands.contains(.hdrToneMapping("5")))
    }

    func testFailedItemStopsItsRemainingCommandsButOtherItemsStillApply() {
        var commands: [PresetCommand] = []
        let report = PicturePresetPlan.apply(values, source: "29") { command in
            if command == .jni("g_disp__disp_back_light", "52", 3) { throw PresetError("设备错误") }
            commands.append(command)
        }
        XCTAssertFalse(report.ok)
        XCTAssertEqual(report.failed.count, 1)
        XCTAssertFalse(commands.contains(.setting("picture_backlight", "52")))
        XCTAssertTrue(commands.contains(.setting("picture_contrast", "60")))
        XCTAssertTrue(commands.contains(.jni("g_video__vid_gamut_mapping_mode", "6", 3)))
    }

    func testInvalidColorGainNeverReachesDeviceAndMissingFreeSyncIsSkipped() {
        var changed = values
        changed["picture_red_gain"] = "4000"; changed.removeValue(forKey: "freesync")
        var commands: [PresetCommand] = []
        let report = PicturePresetPlan.apply(changed, source: "29") { commands.append($0) }
        XCTAssertFalse(report.ok)
        XCTAssertEqual(report.failed.count, 1)
        XCTAssertEqual(report.skipped.count, 1)
        XCTAssertFalse(commands.contains(.colorGains("4000", "1100", "950")))
    }

    func testCaptureUsesHardwareEnumsAndRejectsInvalidValues() {
        let normalized = PicturePresetPlan.normalize(
            settings: ["picture_mode": "14", "picture_backlight": "40", "picture_contrast": "null"],
            jni: ["g_disp__disp_back_light": "52", "g_video__clr_temp": "6",
                  "g_video__vid_local_dimming": "2", "g_video__vid_hdr_tone_mapping_mode": "5",
                  "g_video__dp_adaptive_sync": "1"], source: "29")
        XCTAssertEqual(normalized["picture_backlight"], "52")
        XCTAssertEqual(normalized["picture_color_temperature"], "8")
        XCTAssertEqual(normalized["picture_local_dimming"], "2")
        XCTAssertEqual(normalized["settings_display_hdr_color_tone"], "0")
        XCTAssertEqual(normalized["freesync"], "1")
        XCTAssertNil(normalized["picture_contrast"])
        XCTAssertNil(normalized["g_video__clr_temp"])
    }

    func testUnknownHardwareEnumsDoNotRetainMisleadingSettingsValues() {
        let values = PicturePresetPlan.normalize(
            settings: ["picture_color_temperature": "1", "settings_display_hdr_color_tone": "0"],
            jni: ["g_video__clr_temp": "99", "g_video__vid_hdr_tone_mapping_mode": "-1",
                  "g_video__dp_adaptive_sync": "-1"], source: "29")
        XCTAssertNil(values["picture_color_temperature"])
        XCTAssertNil(values["settings_display_hdr_color_tone"])
        XCTAssertNil(values["freesync"])
    }
}
