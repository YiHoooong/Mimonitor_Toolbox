import Foundation
import XCTest
import MimonitorPresetCore
@testable import MimonitorToolbox

final class AdbPresetDeviceTests: XCTestCase {
    private func device(incomplete: Bool = false, customTemperature: Bool = false, staleTemperature: Bool = false,
                        invalidTemperature: Bool = false,
                        test: (AdbPresetDevice) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("preset-device-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("adb")
        let updates = directory.appendingPathComponent("settings-updates")
        try Data().write(to: updates)
        let script = """
        #!/bin/sh
        case "$4" in
          'settings get global mitv.tvplayer.hdmi.last.source') echo 29 ;;
          'settings list global')
            printf '%s\\n' 'picture_mode=14' 'tv_picture_light_sensor=0' 'picture_backlight=40' \
              'picture_brightness=50' 'picture_contrast=60' 'picture_saturation=51' \
              'picture_hue=49' 'picture_sharpness=30' 'picture_color_temperature=\(customTemperature && !staleTemperature ? "3" : "1")' \
              'picture_red_gain=1024' 'picture_green_gain=1024' 'picture_blue_gain=1024' \
              'picture_local_dimming=3' 'picture_dynamic_definition=0' 'picture_response_time=2' \
              'tv_picture_advanced_video_color_space=6'
            cat '\(updates.path)' ;;
          'settings put global '*)
            set -- $4
            printf '%s=%s\\n' "$4" "$5" >> '\(updates.path)' ;;
          *batchGet*)
            printf '%s\\n' 'g_disp__disp_back_light=52' 'g_video__vid_gamut_mapping_mode=6' \
              'g_video__clr_temp=\(invalidTemperature ? "99" : (customTemperature ? "0" : "2"))' 'g_video__vid_local_dimming=3' 'g_video__light_sensor_switch=0' \
              'g_video__vid_insert_black=0' 'g_video__vid_od_response_time=2' \
              '\(incomplete ? "__error__=missing" : "g_video__dp_adaptive_sync=0")'
            case "$4" in
              *g_video__clr_gain_r*) printf '%s\\n' 'g_video__clr_gain_r=900' 'g_video__clr_gain_g=1000' 'g_video__clr_gain_b=1100' ;;
            esac ;;
        esac
        exit 0
        """
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        let client = AdbClient(adbPath: executable.path)
        client.ip = "192.168.1.30"
        try test(AdbPresetDevice(adb: client, identity: "192.168.1.20"))
    }

    func testCaptureReadsHardwareRatherThanSettingsBacklight() throws {
        try device { device in
            let values = try device.capture()
            XCTAssertEqual(values["picture_backlight"], "52")
            XCTAssertEqual(values["freesync"], "0")
            XCTAssertEqual(values["picture_color_temperature"], "1")
        }
    }

    func testCaptureRejectsMissingFreeSyncRatherThanSavingGuessedOffValue() throws {
        try device(incomplete: true) { device in XCTAssertThrowsError(try device.capture()) }
    }

    func testSuccessfulAdbExitDoesNotHideFailedHardwareWrite() throws {
        try device { device in
            var values = try device.capture()
            values["picture_backlight"] = "60"
            let report = try device.apply(values)
            XCTAssertFalse(report.ok)
            XCTAssertTrue(report.failed.contains { $0.hasPrefix("背光:") })
            XCTAssertFalse(report.applied.contains("背光"))
            XCTAssertTrue(report.applied.contains("对比度"))
        }
    }

    func testGainCaptureReadsHardwareAndVerificationCatchesSilentGainFailure() throws {
        try device(customTemperature: true) { device in
            var values = try device.capture()
            XCTAssertEqual(values["picture_red_gain"], "900")
            XCTAssertEqual(values["picture_green_gain"], "1000")
            values["picture_red_gain"] = "1024"
            let report = try device.apply(values)
            XCTAssertFalse(report.ok)
            XCTAssertTrue(report.failed.contains { $0.hasPrefix("色增益:") })
        }
    }

    func testDynamicDefinitionReadbackCatchesSilentJniFailure() throws {
        try device { device in
            var values = try device.capture()
            values["picture_dynamic_definition"] = "2"
            let report = try device.apply(values)
            XCTAssertFalse(report.ok)
            XCTAssertTrue(report.failed.contains { $0.hasPrefix("动态清晰度:") })
        }
    }

    func testStaleStandardTemperatureSettingDoesNotHideHardwareCustomGains() throws {
        try device(customTemperature: true, staleTemperature: true) { device in
            let values = try device.capture()
            XCTAssertEqual(values["picture_color_temperature"], "3")
            XCTAssertEqual(values["picture_red_gain"], "900")
        }
    }

    func testCaptureRejectsUnknownHardwareTemperatureInsteadOfUsingSettingsMirror() throws {
        try device(invalidTemperature: true) { device in XCTAssertThrowsError(try device.capture()) }
    }
}
