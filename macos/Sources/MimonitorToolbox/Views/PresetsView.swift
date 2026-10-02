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
                Text("预设模式").font(.largeTitle).fontWeight(.semibold)
                Text("从当前显示器设置创建预设。应用后，在画面页、快捷键或菜单栏中的调整会自动保存到当前预设。")
                    .foregroundColor(.secondary)
                if !state.isConnected {
                    Label("未连接显示器，可以管理名称；创建、应用和编辑需要先连接。", systemImage: "wifi.slash")
                        .foregroundColor(.secondary)
                }
                SectionCard(title: "无预设") {
                    HStack {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(state.isConnected && state.activePresetID == nil && !state.presetConfiguration.applicationIncomplete ? "当前使用" : "普通画面设置")
                                .font(.headline)
                            Text("返回第一次进入预设前的设置；预设之间切换不会覆盖它。")
                                .font(.callout).foregroundColor(.secondary)
                        }
                        Spacer()
                        Button("切回无预设") { state.restoreBaseline() }
                            .disabled(!state.canRestoreBaseline || (state.activePresetID == nil && !state.presetConfiguration.applicationIncomplete))
                    }
                }
                ForEach(state.presetConfiguration.presets) { preset in
                    SectionCard(title: preset.name) {
                        HStack {
                            VStack(alignment: .leading, spacing: 6) {
                                if state.activePresetID == preset.id {
                                    Label(state.presetConfiguration.applicationIncomplete ? "上次应用未完成，请重新应用" : "当前使用 · 调整会自动保存", systemImage: "checkmark.circle.fill")
                                        .foregroundColor(.accentColor)
                                }
                                Text(summary(preset)).font(.callout).foregroundColor(.secondary)
                            }
                            Spacer()
                            Button("应用") { state.applyPreset(id: preset.id) }
                                .disabled(!state.isConnected || (state.activePresetID == preset.id && !state.presetConfiguration.applicationIncomplete))
                            Button("编辑画面") { state.applyPreset(id: preset.id, edit: true) }
                                .disabled(!state.isConnected)
                            Menu {
                                Button("重命名") { editor = PresetNameRequest(preset: preset) }
                                Button("删除…", role: .destructive) { deleting = preset; showDelete = true }
                            } label: { Image(systemName: "ellipsis") }
                        }
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
            .padding(30)
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

    private func summary(_ preset: PicturePreset) -> String {
        let mode = Int(preset.values["picture_mode"] ?? "") ?? -1
        let modeName = RegisterMap.sceneNames[mode] ?? "模式 \(mode)"
        return "\(modeName) · 背光 \(preset.values["picture_backlight"] ?? "—") · 对比度 \(preset.values["picture_contrast"] ?? "—") · FreeSync \(preset.values["freesync"] == "1" ? "开" : "关")"
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
