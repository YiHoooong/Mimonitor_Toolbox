import SwiftUI
import MimonitorPresetCore

struct AutomaticTasksView: View {
    @EnvironmentObject var state: AppState
    @State private var editing: ScheduledPresetTask?
    @State private var deleting: ScheduledPresetTask?
    @State private var showDelete = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("自动任务").font(.title2.bold())
                Text("每天按本地时间切换预设，时段结束返回任务开始前的预设（使用其最新值）。支持跨午夜；时段重叠时靠下的任务优先。")
                    .foregroundColor(.secondary)
                Label("应用需保持运行，关闭窗口驻留菜单栏也可执行；睡眠期间不执行，唤醒或重连后重新判断。", systemImage: "info.circle")
                    .font(.callout).foregroundColor(.secondary)
                SectionCard(title: "调度状态") {
                    Text(state.automaticTaskStatus).foregroundColor(.secondary)
                    if let session = state.presetConfiguration.session {
                        let previous = state.presetConfiguration.presets.first { $0.id == session.previousPresetID }?.name ?? "无预设"
                        Text("本轮任务结束后返回：\(previous)").font(.callout)
                    }
                }
                ForEach(Array(state.presetConfiguration.tasks.enumerated()), id: \.element.id) { index, task in
                    SectionCard(title: "\(task.start) – \(task.end)\(isOvernight(task) ? " · 跨午夜" : "")") {
                        HStack(spacing: 12) {
                            Toggle("启用", isOn: Binding(
                                get: { task.enabled },
                                set: { enabled in var updated = task; updated.enabled = enabled; state.saveAutomaticTask(updated) }
                            ))
                            .toggleStyle(.switch).fixedSize()
                            Text(presetName(task.presetID)).font(.headline)
                            if state.presetConfiguration.session?.taskID == task.id,
                               state.presetConfiguration.session?.appliedTargetID == task.presetID {
                                Text("已执行").font(.callout).foregroundColor(.accentColor)
                            }
                            Spacer()
                            Button { state.moveAutomaticTask(id: task.id, offset: -1) } label: {
                                Image(systemName: "arrow.up")
                            }
                            .help("上移，降低重叠时的优先级").disabled(index == 0)
                            Button { state.moveAutomaticTask(id: task.id, offset: 1) } label: {
                                Image(systemName: "arrow.down")
                            }
                            .help("下移，提高重叠时的优先级").disabled(index == state.presetConfiguration.tasks.count - 1)
                            Button("编辑") { editing = task }
                            Button("删除…", role: .destructive) { deleting = task; showDelete = true }
                        }
                    }
                }
                Button {
                    editing = ScheduledPresetTask(presetID: state.presetConfiguration.presets.first?.id ?? PicturePreset.baselineID,
                                                   start: "18:00", end: "20:00")
                } label: {
                    Label("新增自动任务", systemImage: "plus").frame(maxWidth: .infinity).padding(14)
                }
            }
            .padding(24)
            .disabled(state.isPresetOperationInFlight)
        }
        .onAppear { state.checkAutomaticTasks() }
        .sheet(item: $editing) { task in
            AutomaticTaskSheet(task: task, presets: state.presetConfiguration.presets) { state.saveAutomaticTask($0) }
        }
        .alert("删除自动任务？", isPresented: $showDelete, presenting: deleting) { task in
            Button("取消", role: .cancel) {}
            Button("删除", role: .destructive) { state.deleteAutomaticTask(id: task.id) }
        } message: { _ in
            Text("若它正在生效，会重新判断剩余任务；没有其他命中任务时返回本轮开始前的预设。")
        }
    }
    private func presetName(_ id: String) -> String {
        id == PicturePreset.baselineID ? "无预设" : state.presetConfiguration.presets.first { $0.id == id }?.name ?? "预设已删除"
    }
    private func isOvernight(_ task: ScheduledPresetTask) -> Bool {
        guard let start = PresetSchedule.minute(task.start), let end = PresetSchedule.minute(task.end) else { return false }
        return start > end
    }
}

private struct AutomaticTaskSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var task: ScheduledPresetTask
    let presets: [PicturePreset]
    let save: (ScheduledPresetTask) -> Void
    init(task: ScheduledPresetTask, presets: [PicturePreset], save: @escaping (ScheduledPresetTask) -> Void) {
        _task = State(initialValue: task); self.presets = presets; self.save = save
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("自动任务").font(.title2)
            Form {
                Picker("时段内使用", selection: $task.presetID) {
                    Text("无预设").tag(PicturePreset.baselineID)
                    ForEach(presets) { preset in Text(preset.name).tag(preset.id) }
                }
                TextField("开始时间", text: $task.start).textFieldStyle(.roundedBorder)
                TextField("结束时间", text: $task.end).textFieldStyle(.roundedBorder)
                Toggle("启用任务", isOn: $task.enabled)
            }
            Text("使用 24 小时制 HH:mm，例如 23:00–02:00。开始时间包含在时段内，结束时间不包含；两者不能相同。")
                .font(.callout).foregroundColor(.secondary)
            if !task.isValid { Text("请填写有效且不同的开始、结束时间。") .font(.callout).foregroundColor(.orange) }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save(task); dismiss() }.keyboardShortcut(.defaultAction)
                    .disabled(!task.isValid)
            }
        }
        .padding(24).frame(width: 440)
    }
}
