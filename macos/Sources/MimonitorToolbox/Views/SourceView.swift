import SwiftUI

struct SourceView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageHeader(title: "信号源与音频") { state.forceRefreshPage("source") }

                SectionCard(title: "选择信号源") {
                    SegmentedOptionPicker(
                        options: OptionLists.source,
                        selectedValue: state.intValue("mitv.tvplayer.hdmi.last.source", default: -1),
                        onSelect: { state.setSource($0) }
                    )
                    .frame(maxWidth: .infinity)
                }

                SectionCard(title: "当前活跃信号源") {
                    VStack(spacing: 8) {
                        Text("当前活跃信号源").foregroundColor(.secondary)
                        Text(state.activeSource)
                            .font(.system(size: 32, weight: .bold))
                            .foregroundColor(Color(red: 0, green: 0.74, blue: 0.83))
                    }
                    .frame(maxWidth: .infinity)
                }

                SectionCard(title: "选择声音输出") {
                    SegmentedOptionPicker(
                        options: OptionLists.soundOutput,
                        selectedValue: state.intValue(RegisterMap.soundOutputKey, default: -1),
                        onSelect: { state.setSoundOutput($0) }
                    )
                    .frame(maxWidth: .infinity)
                }

                SectionCard(title: "当前声音输出") {
                    VStack(spacing: 8) {
                        Text("当前声音输出").foregroundColor(.secondary)
                        Text(state.activeSoundOutput)
                            .font(.system(size: 32, weight: .bold))
                            .foregroundColor(Color(red: 0, green: 0.74, blue: 0.83))
                    }
                    .frame(maxWidth: .infinity)
                }

                HStack(spacing: 12) {
                    SliderRow(
                        title: "显示器音量",
                        range: 0...100,
                        externalValue: state.intValue("volume_music_speaker", default: 15)
                    ) { v in
                        state.setMediaVolumeValue(v)
                    }
                    SelectableButton(title: "静音", isSelected: state.mediaVolumeMuted == true) {
                        state.toggleMediaMuteExplicit(showHUD: false)
                    }
                }
            }
            .padding(30)
        }
        .onAppear { state.refreshPage("source") }
        .loadingOverlay(state.loadingPages.contains("source"))
    }
}
