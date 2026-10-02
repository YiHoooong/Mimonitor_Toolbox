import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct HomeView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("红米G Pro ToolBox").font(.largeTitle.bold())
                    Text("通过无线 ADB 连接并调优您的 MiniLED 旗舰显示器")
                        .font(.callout).foregroundColor(.secondary)
                }

                SectionCard(title: "连接到显示器") {
                    HStack(spacing: 10) {
                        Text("显示器 IP:")
                        TextField("请输入 IP 地址", text: $state.ipInput)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 200)
                        Button("开始连接") { state.connect() }.buttonStyle(.borderedProminent)
                        Button("扫描内网") { state.scanNet() }
                        Button("网络诊断") { state.runDiagnostics() }
                        Button("断开连接") { state.disconnectAdb() }
                        Spacer()
                    }
                    HStack(spacing: 10) {
                        Text("已扫描设备:")
                        Picker("", selection: Binding(
                            get: { state.selectedDevice },
                            set: { state.selectScannedDevice($0) }
                        )) {
                            Text("请选择扫描到的显示器...").tag("")
                            ForEach(state.scannedDevices, id: \.self) { d in
                                Text(state.scannedDeviceModels[d].map { "\($0) (\(d))" } ?? d).tag(d)
                            }
                        }
                        .frame(width: 220)
                        Text("连接状态:")
                        Text(state.statusText).fontWeight(.bold).foregroundColor(state.statusColor)
                        Spacer()
                    }
                }

                SectionCard(title: "实时操作日志") {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 2) {
                                ForEach(Array(state.logLines.enumerated()), id: \.offset) { _, line in
                                    Text(line)
                                        .font(.system(.caption, design: .monospaced))
                                        .foregroundColor(.green)
                                }
                            }
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                        }
                        .frame(height: 200)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Color(red: 0.12, green: 0.12, blue: 0.12)))
                        .onChange(of: state.logSeq) { _ in
                            guard let last = state.logLines.indices.last else { return }
                            // 等 LazyVStack 把新行布局出来再滚，否则会滚到旧的末尾
                            DispatchQueue.main.async {
                                proxy.scrollTo(last, anchor: .bottom)
                            }
                        }
                    }
                    HStack {
                        Toggle("记录到本地文件", isOn: Binding(
                            get: { state.logToFileEnabled },
                            set: { state.toggleLogFile($0) }
                        ))
                        Spacer()
                        Button("复制全部") {
                            let pb = NSPasteboard.general
                            pb.clearContents()
                            pb.setString(state.logLines.joined(separator: "\n"), forType: .string)
                            state.log("日志已复制到剪贴板")
                        }
                        Button("导出日志") { exportLog() }
                        Button("打开日志目录") { state.openLogDir() }
                    }
                }
            }
            .padding(30)
        }
    }

    private func exportLog() {
        let panel = NSSavePanel()
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd_HHmmss"
        panel.nameFieldStringValue = "Mimonitor_log_\(f.string(from: Date())).txt"
        panel.allowedContentTypes = [.plainText]
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            state.exportLog(to: url)
        }
    }
}
