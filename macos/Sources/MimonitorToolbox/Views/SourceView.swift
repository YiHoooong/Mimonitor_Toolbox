import SwiftUI

struct SourceView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageHeader(title: "信号源切换") { state.forceRefreshPage("source") }

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
            }
            .padding(30)
        }
        .onAppear { state.refreshPage("source") }
        .loadingOverlay(state.loadingPages.contains("source"))
    }
}
