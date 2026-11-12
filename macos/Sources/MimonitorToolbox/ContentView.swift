import SwiftUI

struct ContentView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.scenePhase) private var scenePhase
    @State private var selection: Page? = .home

    var body: some View {
        NavigationSplitView {
            // 全部交给系统：List + .sidebar 提供原生选中态、悬停、拖拽和键盘导航，
            // NavigationSplitView 则让侧边栏用上系统材质，并自动延伸到标题栏下方。
            List(selection: $selection) {
                Section("显示器") {
                    pageRows([.home, .picture, .game, .source, .light, .remote])
                }
                Section("自动化") {
                    pageRows([.presets, .autoTasks])
                }
                Section("设置") {
                    pageRows([.menuBar, .tools])
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 210, ideal: 228, max: 280)
        } detail: {
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
        .navigationSplitViewStyle(.balanced)
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
    private func pageRows(_ pages: [Page]) -> some View {
        ForEach(pages) { page in
            Label(page.title, systemImage: page.icon)
                .tag(page)
        }
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
