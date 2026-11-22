import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct HomeView: View {
    @EnvironmentObject var state: AppState
    @State private var showModeHelp = false
    @State private var showAliasPopover = false
    @State private var aliasDraft = ""

    private var editableDevice: KnownDevice? {
        let targetIP = state.selectedDevice.isEmpty
            ? state.ipInput.trimmingCharacters(in: .whitespaces)
            : state.selectedDevice
        return state.knownDevice(forIP: targetIP)
    }

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
                        Text("显示器列表:")
                        Picker("", selection: Binding(
                            get: { state.selectedDevice },
                            set: { state.selectScannedDevice($0) }
                        )) {
                            Text("请选择显示器...").tag("")
                            ForEach(state.devicePickerIPs, id: \.self) { d in
                                Text(state.deviceDisplayLabel(forIP: d)).tag(d)
                            }
                        }
                        .frame(width: 240)
                        Button {
                            aliasDraft = editableDevice?.alias ?? ""
                            showAliasPopover = true
                        } label: {
                            Image(systemName: "pencil")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(editableDevice == nil)
                        .help(editableDevice == nil ? "连接或选中一台显示器后可设置备注" : "编辑当前显示器备注（用于多屏区分）")
                        .popover(isPresented: $showAliasPopover) {
                            if let device = editableDevice {
                                VStack(alignment: .leading, spacing: 10) {
                                    Text("显示器备注").font(.headline)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("型号：\(device.model.isEmpty ? "未知" : device.model)")
                                        Text("IP：\(device.ip)")
                                        Text("标识：\(device.identityDescription)")
                                    }
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .textSelection(.enabled)

                                    TextField("例如：左屏 / 主屏", text: $aliasDraft)
                                        .textFieldStyle(.roundedBorder)
                                        .onSubmit {
                                            state.updateDeviceAlias(id: device.id, alias: aliasDraft)
                                            showAliasPopover = false
                                        }

                                    HStack(spacing: 8) {
                                        Button("移除设备", role: .destructive) {
                                            state.removeKnownDevice(id: device.id)
                                            showAliasPopover = false
                                        }
                                        Spacer()
                                        if device.hasAlias {
                                            Button("清空") {
                                                state.updateDeviceAlias(id: device.id, alias: "")
                                                showAliasPopover = false
                                            }
                                        }
                                        Button("保存") {
                                            state.updateDeviceAlias(id: device.id, alias: aliasDraft)
                                            showAliasPopover = false
                                        }
                                        .buttonStyle(.borderedProminent)
                                    }
                                }
                                .padding(14)
                                .frame(width: 300)
                            }
                        }
                        Text("连接状态:")
                        Text(state.statusText).fontWeight(.bold).foregroundColor(state.statusColor)
                        Spacer()
                    }
                    HStack(spacing: 10) {
                        Text("连接模式:")
                        Picker("", selection: Binding(
                            get: { state.connectionMode },
                            set: { state.setConnectionMode($0) }
                        )) {
                            ForEach(ConnectionMode.allCases) { mode in
                                Text(mode.label).tag(mode)
                            }
                        }
                        .frame(width: 220)
                        Button {
                            showModeHelp.toggle()
                        } label: {
                            Image(systemName: "questionmark.circle")
                        }
                        .buttonStyle(.plain)
                        .help("查看标准 ADB 与混合加速模式区别")
                        .popover(isPresented: $showModeHelp) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("连接模式说明").font(.headline)
                                ForEach(ConnectionMode.allCases) { mode in
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(mode.label).font(.subheadline.bold())
                                        Text(mode.descriptionText)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }
                            .padding(14)
                            .frame(width: 340)
                        }
                        Text(state.connectionMode == .daemon ? "当前启用 Daemon 通道加速（不可用时自动回退 ADB）" : "兼容性优先，逐条走标准 ADB")
                            .font(.caption)
                            .foregroundColor(.secondary)
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
