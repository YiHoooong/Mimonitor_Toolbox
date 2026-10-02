import SwiftUI
import MimonitorPresetCore

struct PresetsView: View {
    @EnvironmentObject var state: AppState
    @State private var editor: PresetNameRequest?
    @State private var deleting: PicturePreset?
    @State private var showDelete = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("预设模式").font(.title2.bold())
                Text("从当前显示器设置创建预设。应用后，在画面页、快捷键或菜单栏中的调整会自动保存到当前预设。")
                    .foregroundColor(.secondary)
                if !state.isConnected {
                    Label("未连接显示器，可以管理名称；创建、应用和编辑需要先连接。", systemImage: "wifi.slash")
                        .foregroundColor(.secondary)
                }
                PresetCard(title: "无预设", summary: "不使用任何预设，仅修改当前显示器状态",
                           isCurrent: baselineIsCurrent, applicationIncomplete: false) {
                    Button { state.restoreBaseline(edit: true) } label: {
                        Text("编辑画面").frame(width: 64)
                    }
                    .disabled(!state.isConnected || (!baselineIsCurrent && !state.canRestoreBaseline))
                    Button { state.restoreBaseline() } label: {
                        Text("应用").frame(width: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!state.canRestoreBaseline || baselineIsCurrent)
                }
                ForEach(state.presetConfiguration.presets) { preset in
                    PresetCard(title: preset.name, summary: summary(preset),
                               isCurrent: state.isConnected && state.activePresetID == preset.id && !state.presetConfiguration.applicationIncomplete,
                               applicationIncomplete: state.activePresetID == preset.id && state.presetConfiguration.applicationIncomplete) {
                        Menu {
                            Button("重命名") { editor = PresetNameRequest(preset: preset) }
                            Button("删除…", role: .destructive) { deleting = preset; showDelete = true }
                        } label: {
                            Image(systemName: "ellipsis").frame(width: 24, height: 24)
                        }
                        .menuStyle(.borderlessButton)
                        .menuIndicator(.hidden)
                        .fixedSize()
                        .frame(width: 28, height: 28)
                        .background(Theme.control, in: RoundedRectangle(cornerRadius: 6))
                        .help("管理预设")
                        .accessibilityLabel("管理预设 \(preset.name)")
                        Button { state.applyPreset(id: preset.id, edit: true) } label: {
                            Text("编辑画面").frame(width: 64)
                        }
                        .disabled(!state.isConnected)
                        Button { state.applyPreset(id: preset.id) } label: {
                            Text("应用").frame(width: 48)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!state.isConnected || (state.activePresetID == preset.id && !state.presetConfiguration.applicationIncomplete))
                    }
                }
                Button {
                    editor = PresetNameRequest(preset: nil)
                } label: {
                    Label("从当前设置新建预设", systemImage: "plus")
                        .frame(maxWidth: .infinity).padding(14)
                }
                .disabled(!state.isConnected || state.presetConfiguration.applicationIncomplete)
            }
            .padding(24)
            .disabled(state.isPresetOperationInFlight)
        }
        .sheet(item: $editor) { request in
            PresetNameSheet(preset: request.preset) { name in
                if let preset = request.preset { state.renamePreset(id: preset.id, name: name) }
                else { state.createPreset(name: name) }
            }
        }
        .alert("删除预设？", isPresented: $showDelete, presenting: deleting) { preset in
            Button("取消", role: .cancel) {}
            Button("删除", role: .destructive) { state.deletePreset(id: preset.id) }
        } message: { preset in
            Text("删除「\(preset.name)」及引用它的自动任务。正在使用时会先切回无预设；若它是任务结束后的返回目标，届时改回无预设。")
        }
    }

    private var baselineIsCurrent: Bool {
        state.isConnected && state.activePresetID == nil && !state.presetConfiguration.applicationIncomplete
    }

    private func summary(_ preset: PicturePreset) -> String {
        let mode = Int(preset.values["picture_mode"] ?? "") ?? -1
        let modeName = RegisterMap.sceneNames[mode] ?? "模式 \(mode)"
        return "\(modeName) · 背光 \(preset.values["picture_backlight"] ?? "—") · 对比度 \(preset.values["picture_contrast"] ?? "—") · FreeSync \(preset.values["freesync"] == "1" ? "开" : "关")"
    }
}

/// One layout for the ordinary state and saved presets. The primary action is always last.
private struct PresetCard<Actions: View>: View {
    let title: String
    let summary: String
    let isCurrent: Bool
    let applicationIncomplete: Bool
    @ViewBuilder let actions: Actions

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Text(title).font(.headline).lineLimit(1)
                if isCurrent {
                    Label("当前使用", systemImage: "checkmark.circle.fill")
                        .font(.callout).foregroundColor(.blue).fixedSize()
                } else if applicationIncomplete {
                    Label("应用未完成", systemImage: "exclamationmark.circle.fill")
                        .font(.callout).foregroundColor(.orange).fixedSize()
                }
                Spacer(minLength: 0)
            }
            Text(summary).font(.callout).foregroundColor(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 8) {
                Spacer(minLength: 0)
                actions
            }
            .buttonStyle(.bordered)
            .controlSize(.regular)
            .frame(minHeight: 28)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.stroke))
    }
}

private struct PresetNameRequest: Identifiable {
    let id = UUID()
    let preset: PicturePreset?
}

private struct PresetNameSheet: View {
    @Environment(\.dismiss) private var dismiss
    let preset: PicturePreset?
    let save: (String) -> Void
    @State private var name: String
    init(preset: PicturePreset?, save: @escaping (String) -> Void) {
        self.preset = preset; self.save = save
        _name = State(initialValue: preset?.name ?? "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(preset == nil ? "新建预设" : "重命名预设").font(.title2)
            TextField("预设名称", text: $name).textFieldStyle(.roundedBorder)
            Text(preset == nil ? "读取当前设备设置后创建，并进入画面页继续调整。" : "重名时会自动添加序号；已有任务引用不受影响。")
                .font(.callout).foregroundColor(.secondary)
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save(name); dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 420)
    }
}
