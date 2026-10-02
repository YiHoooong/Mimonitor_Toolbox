import SwiftUI

struct PictureView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "画面设置") { state.forceRefreshPage("picture") }

                if state.presetConfiguration.applicationIncomplete {
                    Text("上次预设未完整应用。请到「预设模式」重新应用或返回无预设，确认前不会自动保存混合参数。")
                        .font(.callout).foregroundColor(.orange)
                }
                if state.activePresetID != nil {
                    SectionCard(title: "当前预设：\(state.activePresetName)") {
                        HStack {
                            Text("调整会自动保存到此预设；HDR/FreeSync 记忆已暂停。")
                                .font(.callout).foregroundColor(.secondary)
                            Spacer()
                            Button("管理预设") { state.requestedPage = .presets }
                        }
                    }
                }

                // 画面模式
                SectionCard(title: "画面模式") {
                    HStack(spacing: 10) {
                        ForEach([14, 10, 9], id: \.self) { v in
                            SelectableButton(title: RegisterMap.modeNames[v] ?? "\(v)",
                                             isSelected: state.intValue("picture_mode", default: -1) == v) {
                                state.setMode(v)
                            }
                        }
                        Button("恢复默认") { state.resetCurrentMode() }
                        Text(state.pictureModeHint).font(.callout).foregroundColor(.secondary)
                        Spacer()
                        // 自动调整亮度（光感）：状态以 MTK 侧回读为准（见 applyJniOverrides）
                        Toggle("自动调整亮度", isOn: Binding(
                            get: { state.intValue("tv_picture_light_sensor", default: 0) == 1 },
                            set: { state.setLightSensor($0) }
                        ))
                        .toggleStyle(.switch)
                    }
                }

                // 滑条
                SliderRow(title: "背光", range: 1...100,
                          externalValue: state.intValue("picture_backlight", default: 50)) { v in
                    state.setPictureSlider(title: "背光", value: v,
                                           jniKey: "g_disp__disp_back_light",
                                           settingsKeys: ["picture_backlight", "xiaomi_picture_backlight"])
                }
                SliderRow(title: "黑色级别", range: 0...100,
                          externalValue: state.intValue("picture_brightness", default: 50)) { v in
                    state.setPictureSlider(title: "黑色级别", value: v, jniKey: nil,
                                           settingsKeys: ["picture_brightness"])
                }
                SliderRow(title: "对比度", range: 0...100,
                          externalValue: state.intValue("picture_contrast", default: 50)) { v in
                    state.setPictureSlider(title: "对比度", value: v, jniKey: nil,
                                           settingsKeys: ["picture_contrast"])
                }
                SliderRow(title: "饱和度", range: 0...100,
                          externalValue: state.intValue("picture_saturation", default: 50)) { v in
                    state.setPictureSlider(title: "饱和度", value: v, jniKey: nil,
                                           settingsKeys: ["picture_saturation"])
                }
                SliderRow(title: "色调", range: 0...100,
                          externalValue: state.intValue("picture_hue", default: 50)) { v in
                    state.setPictureSlider(title: "色调", value: v, jniKey: nil,
                                           settingsKeys: ["picture_hue"])
                }
                SliderRow(title: "锐度", range: 0...100,
                          externalValue: state.intValue("picture_sharpness", default: 50)) { v in
                    state.setPictureSlider(title: "锐度", value: v, jniKey: nil,
                                           settingsKeys: ["picture_sharpness"])
                }

                // 色温
                ButtonGroupSection(title: "色温", options: OptionLists.colorTemp,
                                   selectedValue: state.intValue("picture_color_temperature", default: 1)) { v in
                    state.setColorTemp(v)
                }

                // 自定义色温才显示增益
                if state.isCustomColorTemp {
                    SliderRow(title: "红色增益", range: 524...1524,
                              externalValue: state.intValue("picture_red_gain", default: 1024)) { v in
                        state.setColorGain(title: "红色增益", settingsKey: "picture_red_gain",
                                           jniKey: "g_video__clr_gain_r", value: v)
                    }
                    SliderRow(title: "绿色增益", range: 524...1524,
                              externalValue: state.intValue("picture_green_gain", default: 1024)) { v in
                        state.setColorGain(title: "绿色增益", settingsKey: "picture_green_gain",
                                           jniKey: "g_video__clr_gain_g", value: v)
                    }
                    SliderRow(title: "蓝色增益", range: 524...1524,
                              externalValue: state.intValue("picture_blue_gain", default: 1024)) { v in
                        state.setColorGain(title: "蓝色增益", settingsKey: "picture_blue_gain",
                                           jniKey: "g_video__clr_gain_b", value: v)
                    }
                }

                ButtonGroupSection(title: "精密控光", options: OptionLists.localDimming,
                                   selectedValue: state.intValue("picture_local_dimming", default: 3)) { v in
                    state.setLocalDimming(v)
                }

                // 只在部分画面模式下存在这一项；SDR 模式（如标准/电影）下原版会隐藏
                if state.showHdrToneMapping {
                    ButtonGroupSection(title: "HDR 色调映射", options: OptionLists.hdrToneMapping,
                                       selectedValue: state.intValue("settings_display_hdr_color_tone", default: 0)) { v in
                        state.setHdrToneMapping(v)
                    }
                }

                ButtonGroupSection(title: "动态清晰度", options: OptionLists.dynamicDefinition,
                                   selectedValue: state.intValue("picture_dynamic_definition", default: 0)) { v in
                    state.setDynamicDefinition(v)
                }

                ButtonGroupSection(title: "灰阶响应时间", options: OptionLists.responseTime,
                                   selectedValue: state.intValue("picture_response_time", default: 1)) { v in
                    state.setResponseTime(v)
                }

                ButtonGroupSection(title: "色域", options: OptionLists.gamut,
                                   selectedValue: state.intValue("tv_picture_advanced_video_color_space", default: 0)) { v in
                    state.setGamut(v)
                }
            }
            .padding(30)
        }
        .disabled(state.presetConfiguration.applicationIncomplete)
        .onAppear { state.refreshPage("picture") }
        .loadingOverlay(state.loadingPages.contains("picture"))
    }
}
