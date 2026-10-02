import SwiftUI

struct ContentView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.scenePhase) private var scenePhase
    @State private var selection: Page? = .home

    /// 侧边栏固定宽度。
    ///
    /// 不用 `NavigationSplitView`：它的分隔条天生可拖，
    /// `navigationSplitViewColumnWidth(min:ideal:max:)` 把 min/max 设成相等也拦不住
    /// （实测仍然能拖）。干脆用 HStack 自己排，从根上没有分隔条。
    /// 侧边栏视觉仍靠 `List` + `.listStyle(.sidebar)`，和原生一致。
    private let sidebarWidth: CGFloat = 220

    var body: some View {
        HStack(spacing: 0) {
            List(Page.allCases, selection: $selection) { page in
                Label(page.title, systemImage: page.icon)
                    .tag(page)
            }
            .listStyle(.sidebar)
            // 关键：脱离 NavigationSplitView 之后，`.sidebar` 样式的那层半透明材质
            // 会去采样**桌面壁纸**而不是窗口背景 —— 壁纸偏暗时，浅色模式下的边栏
            // 会被染成暗色，和右边内容区对不上。
            // 盖一层不透明的 `.background`（语义色，随外观自适应）挡住它。
            // 再叠极淡的一层 primary，保留一点"这是边栏"的层次感。
            .scrollContentBackground(.hidden)
            .background {
                Rectangle()
                    .fill(.background)
                    .overlay(Color.primary.opacity(0.03))
            }
            .frame(width: sidebarWidth)

            Divider()

            Group {
                if let selection {
                    detailView(for: selection)
                } else {
                    Text("请选择一个页面")
                        .foregroundColor(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(.background)
        }
        // 用户去系统设置授权后切回来，权限状态要自动刷新
        .onChange(of: scenePhase) { phase in
            if phase == .active { state.refreshAccessibilityStatus() }
        }
        .onChange(of: state.requestedPage) { page in
            guard let page else { return }
            selection = page
            state.requestedPage = nil
        }
        .onChange(of: selection) { page in
            guard let page else { return }
            // 未连接时不允许进入需要连接的页面（对应原版 _on_page_changed 的弹回主页）
            if state.needsConnection(page) && !state.isConnected {
                state.log("未连接显示器，请先在主页连接！")
                selection = .home
                return
            }
            state.onPageAppear(page)
        }
        .overlay {
            if state.isPresetBusy {
                ZStack {
                    Color.black.opacity(0.10)
                    VStack(spacing: 12) {
                        ProgressView()
                        Text(state.presetOperationText).font(.callout)
                    }
                    .padding(24)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            }
        }
        .alert("预设与自动任务", isPresented: Binding(
            get: { state.presetError != nil },
            set: { if !$0 { state.presetError = nil } }
        )) {
            Button("好", role: .cancel) { state.presetError = nil }
        } message: { Text(state.presetError ?? "") }
    }

    @ViewBuilder
    private func detailView(for page: Page) -> some View {
        switch page {
        case .home: HomeView()
        case .picture: PictureView()
        case .presets: PresetsView()
        case .autoTasks: AutomaticTasksView()
        case .game: GameView()
        case .source: SourceView()
        case .light: LightView()
        case .menuBar: MenuBarSettingsView()
        case .tools: ToolsView()
        case .remote: RemoteView()
        }
    }
}
