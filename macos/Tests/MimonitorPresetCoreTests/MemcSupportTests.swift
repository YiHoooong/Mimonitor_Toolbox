import XCTest
@testable import MimonitorPresetCore

final class MemcSupportTests: XCTestCase {
    func testLevelLabelsIncludeCustomWithoutArrayBoundsCrash() {
        XCTAssertEqual(MemcSupport.levelLabel(0), "关")
        XCTAssertEqual(MemcSupport.levelLabel(1), "低")
        XCTAssertEqual(MemcSupport.levelLabel(2), "中")
        XCTAssertEqual(MemcSupport.levelLabel(3), "高")
        XCTAssertEqual(MemcSupport.levelLabel(4), "自定义")
        XCTAssertEqual(MemcSupport.levelLabel(99), "99")
    }

    func testCaseADisallowedModesBlockWriteAndOfferMovieSwitch() {
        for mode in ["14", "18", "69", "30", "2", "22", "34", "43", "73"] {
            let report = MemcSupport.evaluate(
                settings: ["picture_mode": mode, "mitv.tvplayer.hdmi.last.source": "23"],
                jni: ["g_video__picture_mode": mode, "g_video__freesync_switch": "0",
                      "g_fusion_picture__hdmi_edid_version": "1", "g_video__dlg_vrr_switch": "0"]
            )
            XCTAssertEqual(report.outcome, .disallowedMode, "mode \(mode) should be Case A")
            XCTAssertFalse(report.modeSupported)
            let prompt = MemcPromptState(report: report, level: 2)
            XCTAssertEqual(prompt?.title, "当前画面模式不支持开启运动补偿")
            XCTAssertEqual(prompt?.canSwitchToMovie, true)
            XCTAssertTrue(prompt?.message.contains("✗ 画面模式支持") == true)
            XCTAssertTrue(prompt?.message.contains("ℹ 输入信号刷新率 ≤ 120Hz") == true)
        }
    }

    func testNilPictureModeBlocksWriteAsCaseAAndSupportsSettingsFallbacks() {
        let nilModeReport = MemcSupport.evaluate(
            settings: ["mitv.tvplayer.hdmi.last.source": "23"],
            jni: ["g_video__freesync_switch": "0", "g_fusion_picture__hdmi_edid_version": "1",
                  "g_video__dlg_vrr_switch": "0"]
        )
        XCTAssertNil(nilModeReport.pictureMode)
        XCTAssertEqual(nilModeReport.outcome, .disallowedMode)
        XCTAssertFalse(nilModeReport.modeSupported)
        let prompt = MemcPromptState(report: nilModeReport, level: 2)
        XCTAssertEqual(prompt?.title, "当前画面模式不支持开启运动补偿")
        XCTAssertEqual(prompt?.canSwitchToMovie, true)
        XCTAssertTrue(prompt?.message.contains("✗ 画面模式支持：当前为「未知模式」") == true)

        // Fallback to picture_preset_scenario, settings["freesync"], settings["mode_320"] when JNI absent
        let fallbackReport = MemcSupport.evaluate(
            settings: [
                "picture_preset_scenario": "9",
                "mitv.tvplayer.hdmi.last.source": "23",
                "freesync": "1",
                "mode_320": "1",
            ],
            jni: [:]
        )
        XCTAssertEqual(fallbackReport.pictureMode, 9)
        XCTAssertTrue(fallbackReport.modeSupported)
        XCTAssertEqual(fallbackReport.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(fallbackReport.checklistText.contains("✗ FreeSync 已关闭"))
        XCTAssertTrue(fallbackReport.checklistText.contains("✗ 320Hz / DLG 模式已关闭：320Hz 模式已开启"))
    }

    func testCaseBUnmetFreesyncOr320HzOrDlgOrContentAdaptiveStillSaves() {
        // Movie mode + HDMI FreeSync ON (note: ignore dp_adaptive_sync when on HDMI)
        let freesyncReport = MemcSupport.evaluate(
            settings: ["picture_mode": "9", "mitv.tvplayer.hdmi.last.source": "23"],
            jni: ["g_video__picture_mode": "9", "g_video__freesync_switch": "3",
                  "g_video__dp_adaptive_sync": "0", "g_fusion_picture__hdmi_edid_version": "1",
                  "g_video__dlg_vrr_switch": "0"]
        )
        XCTAssertEqual(freesyncReport.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(freesyncReport.modeSupported)
        let fsPrompt = MemcPromptState(report: freesyncReport, level: 1)
        XCTAssertEqual(fsPrompt?.title, "已保存「低」档位，部分条件未满足")
        XCTAssertEqual(fsPrompt?.canSwitchToMovie, false)
        XCTAssertTrue(fsPrompt?.message.contains("✗ FreeSync 已关闭") == true)

        // Game mode (64) + DP 320Hz ON
        let dp320Report = MemcSupport.evaluate(
            settings: ["picture_mode": "64", "mitv.tvplayer.hdmi.last.source": "29"],
            jni: ["g_video__picture_mode": "64", "g_video__dp_adaptive_sync": "0",
                  "g_fusion_picture__dp_edid_version": "3", "g_video__dlg_vrr_switch": "0"]
        )
        XCTAssertEqual(dp320Report.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(dp320Report.checklistText.contains("✗ 320Hz / DLG 模式已关闭：320Hz 模式已开启"))

        // Mode 10 + DLG VRR ON
        let dlgReport = MemcSupport.evaluate(
            settings: ["picture_mode": "10", "mitv.tvplayer.hdmi.last.source": "24"],
            jni: ["g_video__picture_mode": "10", "g_video__freesync_switch": "0",
                  "g_fusion_picture__hdmi_edid_version": "1", "g_video__dlg_vrr_switch": "1"]
        )
        XCTAssertEqual(dlgReport.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(dlgReport.checklistText.contains("DLG 高帧率已开启"))

        // Mode 5 (DV IQ) + content adaptive ON
        let dvIqReport = MemcSupport.evaluate(
            settings: ["picture_mode": "5", "mitv.tvplayer.hdmi.last.source": "23"],
            jni: ["g_video__picture_mode": "5", "g_video__freesync_switch": "0",
                  "g_fusion_picture__hdmi_edid_version": "1", "g_video__dlg_vrr_switch": "0",
                  "g_video__vid_content_adaptive": "1"]
        )
        XCTAssertEqual(dvIqReport.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(dvIqReport.checklistText.contains("✗ 内容自适应已关闭"))
    }

    func testCaseCReadyOnMovieOrGameAndNeverFakesRefreshRateCheckmark() {
        // On HDMI, stale dp_adaptive_sync=1 must be ignored.
        for mode in ["9", "10", "64"] {
            let report = MemcSupport.evaluate(
                settings: ["picture_mode": mode, "mitv.tvplayer.hdmi.last.source": "23"],
                jni: ["g_video__picture_mode": mode, "g_video__freesync_switch": "0",
                      "g_video__dp_adaptive_sync": "1", "g_fusion_picture__hdmi_edid_version": "1",
                      "g_video__dlg_vrr_switch": "0"]
            )
            XCTAssertEqual(report.outcome, .ready)
            XCTAssertNil(MemcPromptState(report: report, level: 3))
            XCTAssertTrue(report.checklistText.contains("ℹ 输入信号刷新率 ≤ 120Hz"))
            XCTAssertFalse(report.checklistText.contains("✓ 输入信号刷新率 ≤ 120Hz"))
        }
    }

    func testHostRefreshRateEvaluationCoversAbove120AndBelowOrEqual120WithDisclaimer() {
        let highRateReport = MemcSupport.evaluate(
            settings: ["picture_mode": "9", "mitv.tvplayer.hdmi.last.source": "23"],
            jni: ["g_video__picture_mode": "9", "g_video__freesync_switch": "0",
                  "g_fusion_picture__hdmi_edid_version": "1", "g_video__dlg_vrr_switch": "0"],
            hostRefreshHz: 160
        )
        XCTAssertEqual(highRateReport.outcome, .unmetRuntimeConditions)
        XCTAssertTrue(highRateReport.checklistText.contains("✗ 输入信号刷新率 ≤ 120Hz"))
        XCTAssertTrue(highRateReport.checklistText.contains("160Hz"))
        XCTAssertTrue(highRateReport.checklistText.contains("可能不准"))

        let okRateReport = MemcSupport.evaluate(
            settings: ["picture_mode": "9", "mitv.tvplayer.hdmi.last.source": "23"],
            jni: ["g_video__picture_mode": "9", "g_video__freesync_switch": "0",
                  "g_fusion_picture__hdmi_edid_version": "1", "g_video__dlg_vrr_switch": "0"],
            hostRefreshHz: 120
        )
        XCTAssertEqual(okRateReport.outcome, .ready)
        XCTAssertTrue(okRateReport.checklistText.contains("✓ 输入信号刷新率 ≤ 120Hz"))
        XCTAssertTrue(okRateReport.checklistText.contains("120Hz"))
        XCTAssertTrue(okRateReport.checklistText.contains("仅供参考"))
    }
}
