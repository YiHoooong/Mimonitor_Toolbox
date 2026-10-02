import AppKit
import Foundation
import MimonitorPresetCore

extension AppState {
    var activePresetID: String? {
        presetConfiguration.activeDeviceIdentity == adb.ip ? presetConfiguration.activePresetID : nil
    }
    var activePresetName: String {
        presetConfiguration.presets.first { $0.id == activePresetID }?.name ?? "无预设"
    }
    var presetControlPolicy: PresetControlPolicy {
        PresetControlPolicy(configuration: presetConfiguration, deviceIdentity: adb.ip, isSwitching: isPresetBusy)
    }
    var memoriesSuspendedByPreset: Bool { presetControlPolicy.memoriesLocked }
    var effectiveHdrMemoryEnabled: Bool {
        presetControlPolicy.effectiveMemoryEnabled(savedPreference: hdrMemoryEnabled)
    }
    var effectiveFreesyncMemoryEnabled: Bool {
        presetControlPolicy.effectiveMemoryEnabled(savedPreference: freesyncMemoryEnabled)
    }
    var canRestoreBaseline: Bool {
        isConnected && presetConfiguration.baseline?.deviceIdentity == adb.ip
            && !(presetConfiguration.baseline?.values.isEmpty ?? true)
    }

    func startPresetFeatures() {
        presetConfiguration = presetEngine.configuration
        if let error = presetEngine.loadError { presetError = error; log(error) }
        presetTimer = Timer(timeInterval: 15, repeats: true) { [weak self] _ in
            self?.checkAutomaticTasks()
        }
        if let timer = presetTimer { RunLoop.main.add(timer, forMode: .common) }
        presetWakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.checkAutomaticTasks() }
        updateAutomaticTaskStatus()
    }

    private func currentMinute() -> Int {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: Date())
        return (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
    }

    func updateAutomaticTaskStatus() {
        let wanted = PresetSchedule.activeTask(presetConfiguration.tasks, minute: currentMinute())
        if let session = presetConfiguration.session, session.deviceIdentity != adb.ip, isConnected {
            automaticTaskStatus = "等待原显示器 \(session.deviceIdentity)，不会向当前设备还原"
        } else if let wanted {
            let name = presetConfiguration.presets.first { $0.id == wanted.presetID }?.name ?? "无预设"
            let waiting = isConnected ? "" : "，等待连接显示器"
            automaticTaskStatus = "当前时段：\(wanted.start)–\(wanted.end) · \(name)\(waiting)"
        } else if presetConfiguration.session != nil {
            automaticTaskStatus = isConnected ? "时段已结束，等待返回原预设" : "时段已结束，连接后返回原预设"
        } else { automaticTaskStatus = "当前没有任务生效" }
    }

    func checkAutomaticTasks() {
        updateAutomaticTaskStatus()
        guard isConnected, !isPresetOperationInFlight, presetEngine.loadError == nil else { return }
        if pendingPresetSave == nil, let id = pendingPresetSaveID, activePresetID == id,
           pendingPresetSaveDevice == adb.ip,
           !presetConfiguration.applicationIncomplete {
            schedulePresetSave(id: id, request: connectionIntent.generation, revision: pictureEditRevision, delay: 1.2)
        }
        if let session = presetConfiguration.session, session.deviceIdentity != adb.ip { return }
        let minute = currentMinute()
        let wanted = PresetSchedule.activeTask(presetConfiguration.tasks, minute: minute)
        if let wanted {
            if presetConfiguration.session?.taskID == wanted.id,
               presetConfiguration.session?.appliedTargetID == wanted.presetID,
               presetConfiguration.session?.occurrenceStart == PresetSchedule.occurrenceStart(wanted, minute: minute),
               !presetConfiguration.applicationIncomplete { return }
        } else if presetConfiguration.session == nil { return }
        runPresetOperation("自动任务切换", automatic: true, refresh: true) { engine, device in
            // The device queue may have been busy. Do not execute a window that already ended.
            try engine.reconcile(minute: self.currentMinute(), device: device)
        }
    }

    func createPreset(name: String) {
        runPresetOperation("新建预设", navigateToPicture: true) { engine, device in
            guard let device else { throw PresetError("请先连接显示器") }
            try engine.create(name: name, device: device)
        }
    }
    func applyPreset(id: String, edit: Bool = false) {
        if edit && activePresetID == id && !isPresetOperationInFlight && !presetConfiguration.applicationIncomplete {
            requestedPage = .picture
            return
        }
        runPresetOperation("应用预设", refresh: true, navigateToPicture: edit) { engine, device in
            guard let device else { throw PresetError("请先连接显示器") }
            try engine.apply(id: id, device: device)
        }
    }
    func restoreBaseline(edit: Bool = false) {
        if edit && isConnected && activePresetID == nil && !isPresetOperationInFlight
            && !presetConfiguration.applicationIncomplete {
            requestedPage = .picture
            return
        }
        runPresetOperation("应用无预设", refresh: true, navigateToPicture: edit) { engine, device in
            guard let device else { throw PresetError("请先连接显示器") }
            try engine.restore(device: device)
        }
    }
    func renamePreset(id: String, name: String) {
        runPresetOperation("重命名预设") { engine, _ in try engine.rename(id: id, name: name) }
    }
    func setPresetMenuBarVisibility(id: String, visible: Bool) {
        runPresetOperation("更新预设菜单栏显示") { engine, _ in
            try engine.setMenuBarVisibility(id: id, visible: visible)
        }
    }
    func deletePreset(id: String) {
        runPresetOperation("删除预设", refresh: true) { engine, device in try engine.delete(id: id, device: device) }
    }
    func saveAutomaticTask(_ task: ScheduledPresetTask) {
        runPresetOperation("保存自动任务") { engine, _ in try engine.saveTask(task) }
    }
    func deleteAutomaticTask(id: String) {
        runPresetOperation("删除自动任务") { engine, _ in try engine.deleteTask(id: id) }
    }
    func moveAutomaticTask(id: String, offset: Int) {
        runPresetOperation("调整任务顺序") { engine, _ in try engine.moveTask(id: id, offset: offset) }
    }

    /// Every picture write (including hotkeys and menu-bar controls) goes through this path.
    /// Capture the preset identity NOW, not when the delayed readback eventually runs.
    func runPictureChange(delay: TimeInterval = 1.2, _ work: @escaping () -> Void) {
        guard isConnected, !isPresetBusy, !presetConfiguration.applicationIncomplete else { return }
        let request = connectionIntent.generation
        let identity = adb.ip
        let presetID = activePresetID
        pictureEditRevision &+= 1
        let revision = pictureEditRevision
        if presetID != nil { pendingPresetSaveID = presetID; pendingPresetSaveDevice = identity }
        pendingPresetSave?.cancel()
        pendingPresetSave = nil
        deviceQueue.async {
            let current = DispatchQueue.main.sync {
                self.isConnected && self.connectionIntent.isCurrent(request) && self.adb.ip == identity
            }
            guard current else { return }
            self.adb.transaction(work)
            self.pictureSettleUntil = Date().addingTimeInterval(delay)
            DispatchQueue.main.async {
                guard self.connectionIntent.isCurrent(request), self.adb.ip == identity,
                      self.activePresetID == presetID, self.pictureEditRevision == revision,
                      !self.isPresetBusy, let presetID else { return }
                self.schedulePresetSave(id: presetID, request: request, revision: revision, delay: delay)
            }
        }
    }

    private func schedulePresetSave(id: String, request: Int, revision: Int, delay: TimeInterval) {
        let save = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.pendingPresetSave = nil
            guard self.isConnected,
                  self.connectionIntent.isCurrent(request), self.activePresetID == id,
                  self.pictureEditRevision == revision, !self.presetConfiguration.applicationIncomplete else { return }
            if self.hasPendingDeviceControls || self.isPresetOperationInFlight {
                self.schedulePresetSave(id: id, request: request, revision: revision, delay: 0.5)
                return
            }
            self.runPresetOperation("自动保存当前预设", automatic: true, flushAutosave: false) { engine, device in
                guard let device else { throw PresetError("显示器暂不可用，待连接恢复后保存") }
                try engine.synchronize(id: id, device: device)
            }
        }
        pendingPresetSave = save
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: save)
    }

    private func runPresetOperation(_ label: String, automatic: Bool = false, refresh: Bool = false,
                                    navigateToPicture: Bool = false, flushAutosave: Bool = true,
                                    operation: @escaping (PresetEngine, AdbPresetDevice?) throws -> Void) {
        guard !isPresetOperationInFlight else { return }
        let request = connectionIntent.generation
        let identity = adb.ip
        let connected = isConnected
        let saveID = pendingPresetSaveDevice == identity ? pendingPresetSaveID : nil
        pendingPresetSave?.cancel()
        pendingPresetSave = nil
        if saveID != nil { pendingPresetSaveID = nil; pendingPresetSaveDevice = nil }
        isPresetOperationInFlight = true
        isPresetBusy = refresh || navigateToPicture
        presetOperationText = label
        if refresh || navigateToPicture { invalidatePictureRefreshesForPreset() }
        updateHdrMemoryStatus()
        updateFreesyncMemoryStatus()

        deviceQueue.async {
            let current = DispatchQueue.main.sync {
                self.isConnected && self.connectionIntent.isCurrent(request) && self.adb.ip == identity
            }
            let device = connected && current ? AdbPresetDevice(adb: self.adb, identity: identity) : nil
            var reports: [PresetApplyReport] = []
            self.presetEngine.onReport = { reports.append($0) }
            var errorText: String?
            var savedEdits = saveID == nil
            do {
                try self.adb.transaction {
                    if device != nil, Date() < self.pictureSettleUntil {
                        Thread.sleep(until: self.pictureSettleUntil)
                    }
                    if flushAutosave, let saveID, let device {
                        // Never save mixed values from a failed application into the original preset.
                        if !self.presetEngine.configuration.applicationIncomplete {
                            try self.presetEngine.synchronize(id: saveID, device: device)
                        }
                        savedEdits = true
                    }
                    try operation(self.presetEngine, device)
                    if !flushAutosave { savedEdits = true }
                }
            } catch { errorText = error.localizedDescription }
            self.presetEngine.onReport = nil
            let configuration = self.presetEngine.configuration

            DispatchQueue.main.async {
                self.presetConfiguration = configuration
                self.isPresetOperationInFlight = false
                self.isPresetBusy = false
                self.presetOperationText = ""
                self.updateHdrMemoryStatus()
                self.updateFreesyncMemoryStatus()
                self.updateAutomaticTaskStatus()
                if !savedEdits, let saveID,
                   configuration.activePresetID == saveID, configuration.activeDeviceIdentity == identity,
                   self.pendingPresetSaveID == nil || (self.pendingPresetSaveID == saveID && self.pendingPresetSaveDevice == identity) {
                    self.pendingPresetSaveID = saveID
                    self.pendingPresetSaveDevice = identity
                    if self.pendingPresetSave == nil, self.isConnected, self.adb.ip == identity,
                       !configuration.applicationIncomplete {
                        self.schedulePresetSave(id: saveID, request: self.connectionIntent.generation, revision: self.pictureEditRevision, delay: 3)
                    }
                }
                if let errorText {
                    if !automatic || errorText != self.lastAutomaticTaskError {
                        self.log("\(label)失败：\(errorText)")
                    }
                    if automatic { self.lastAutomaticTaskError = errorText }
                    else { self.presetError = errorText }
                } else {
                    self.lastAutomaticTaskError = ""
                    self.log("\(label)完成")
                    for report in reports {
                        self.log(report.summary)
                        for skipped in report.skipped { self.log("跳过：\(skipped)") }
                    }
                    if navigateToPicture, self.connectionIntent.isCurrent(request), self.isConnected {
                        self.requestedPage = .picture
                    }
                }
                if refresh, self.isConnected, self.connectionIntent.isCurrent(request) {
                    self.forceRefreshPage("picture")
                    self.forceRefreshPage("game")
                }
                if !automatic { self.checkAutomaticTasks() }
            }
        }
    }
}
