import AppKit
import SwiftUI

/// 悬浮提示的数据模型。用 ObservableObject 而不是每次重建视图，
/// 这样反复显示只走 SwiftUI 的差异更新，开销可忽略。
final class HUDModel: ObservableObject {
    @Published var title: String = ""
    @Published var value: String = ""
    @Published var operationPhase: HUDOperationPhase?
    @Published var showsCountdown: Bool = false
    /// 倒计时起点与时长。进度条不存"当前进度"，而是每帧按这两个值现算 ——
    /// 这样由显示链路驱动，跟屏幕刷新率同步，不会有定时器的台阶感。
    @Published var countdownStart: Date = .distantPast
    @Published var countdownDuration: TimeInterval = 1
}

/// 悬浮提示的界面。
///
/// 原生 macOS 风格：系统材质做底、语义色做前景，自动适配浅色/深色模式与
/// 「增强对比度」等辅助功能设置 —— 不再是照抄 Windows 版那套硬编码的深色 + 蓝色。
/// 数值用 rounded 字型 + 等宽数字，滚动时不会左右跳。
private struct HUDView: View {
    @ObservedObject var model: HUDModel

    private let barWidth: CGFloat = 288

    var body: some View {
        VStack(spacing: model.operationPhase == nil ? 2 : 6) {
            Text(model.title)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle)

            if let phase = model.operationPhase {
                switch phase {
                case .running:
                    ProgressView().progressViewStyle(.circular).controlSize(.large)
                        .frame(width: 34, height: 34)
                case .finished(.success):
                    resultSymbol("checkmark.circle.fill", color: .green, fallback: "完成")
                case .finished(.failure):
                    resultSymbol("exclamationmark.circle.fill", color: .red, fallback: "失败")
                case .finished(.cancelled):
                    resultSymbol("minus.circle", color: .secondary, fallback: "已取消")
                }
                Text(model.value).font(.callout).foregroundStyle(.secondary)
            } else {
                Text(model.value)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(.primary)
                    // 数值位数变化时不要带动画，否则会抽一下
                    .animation(nil, value: model.value)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .overlay(alignment: .bottom) {
            if model.showsCountdown {
                // TimelineView(.animation) 由显示链路驱动，每帧回调一次并按屏幕刷新率
                // （60/120Hz）重绘 —— 比定时器逐帧写值平滑得多。
                // 只在倒计时期间才挂上，平时不占用刷新。
                TimelineView(.animation) { ctx in
                    let elapsed = ctx.date.timeIntervalSince(model.countdownStart)
                    let remain = max(0, 1 - elapsed / max(model.countdownDuration, 0.01))
                    ZStack(alignment: .leading) {
                        Capsule().fill(.primary.opacity(0.12))
                        Capsule()
                            .fill(Color.accentColor)
                            .frame(width: remain * barWidth)
                    }
                }
                .frame(width: barWidth, height: 5)
                .padding(.bottom, 16)
            }
        }
        .frame(width: 360, height: 112)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(.primary.opacity(0.08), lineWidth: 1)
        )
        // 阴影交给窗口本身（panel.hasShadow）：边界窗口会按内容 alpha 自动算形状，
        // 比在 SwiftUI 里画更原生，也不会被 360×112 的面板边界裁掉。
    }

    @ViewBuilder
    private func resultSymbol(_ name: String, color: Color, fallback: String) -> some View {
        if let symbol = NSImage(systemSymbolName: name, accessibilityDescription: fallback) {
            Image(nsImage: symbol).renderingMode(.template).resizable().scaledToFit()
                .frame(width: 34, height: 34).foregroundStyle(color)
        }
        // If unavailable, the status text below already says 完成 / 切换失败 / 已取消.
    }
}

/// 悬浮窗本体。用 NSPanel 而不是 SwiftUI 的 Window：
///   - `.nonactivatingPanel` 保证不抢焦点（否则会把前台 app 踢下去）
///   - `.borderless` + 透明背景才能自绘圆角
///   - 复用同一个 panel，反复显示只改模型，没有创建开销
final class HUDWindow {
    static let shared = HUDWindow()

    private let model = HUDModel()
    private var panel: NSPanel?
    private var hideTimer: Timer?
    /// 每次 show/fadeOut 自增，用于判断淡出动画是否已被新的显示取代
    private var generation = 0
    private var operation = HUDOperationState()
    private var isFadingOut = false

    private let size = NSSize(width: 360, height: 112)
    private let bottomInset: CGFloat = 150
    private let visibleSeconds: TimeInterval = 1.8
    private let fadeSeconds: TimeInterval = 0.25

    private init() {}

    // MARK: - 对外接口

    /// 显示一条提示（不带倒计时）。
    func show(title: String, value: String) {
        guard operation.acceptsValueHint else { return }
        operation.showValueHint()
        model.operationPhase = nil
        present()
        model.title = title
        model.value = value
        stopCountdown()
    }

    /// 显示提示并开始倒计时，走完才真正下发。
    /// 重复调用会重置倒计时 —— 对应"松手后才生效"。
    ///
    /// 这里只记录「起点 + 时长」，具体进度由界面里的 `TimelineView(.animation)` 每帧现算。
    /// 曾经试过两种画法都不行：
    ///   - `withAnimation`：在无边框 NSPanel 的 NSHostingView 里根本不驱动，条子纹丝不动
    ///   - 30Hz 定时器逐帧写值：能动，但只有 24 个台阶，肉眼能看出跳跃
    func show(title: String, value: String, countdown seconds: TimeInterval) {
        guard operation.acceptsValueHint else { return }
        operation.showValueHint()
        model.operationPhase = nil
        present()
        model.title = title
        model.value = value
        model.countdownDuration = seconds
        model.countdownStart = Date()
        model.showsCountdown = true
    }

    /// 倒计时结束（值已下发）：隐藏进度条，并重新计时展示
    func endCountdown() {
        guard !operation.keepsVisible else { return }
        stopCountdown()
        restartHideTimer()
    }

    /// Progress uses the same non-activating panel, and has no auto-hide timer while running.
    func beginOperation(title: String) -> UUID {
        let token = operation.begin()
        model.title = title
        model.value = "切换中…"
        model.operationPhase = operation.phase
        stopCountdown()
        present(autoHide: false)
        return token
    }

    func finishOperation(token: UUID, result: HUDOperationResult, title: String? = nil) {
        guard operation.finish(token: token, result: result) else { return }
        if let title { model.title = title }
        model.operationPhase = operation.phase
        switch result {
        case .success: model.value = "完成"
        case .failure: model.value = "切换失败"
        case .cancelled: model.value = "已取消"
        }
        present()
    }

    // MARK: - 内部

    private func stopCountdown() {
        model.showsCountdown = false
    }

    private func present(autoHide: Bool = true) {
        let panel = ensurePanel()
        hideTimer?.invalidate()
        if autoHide { restartHideTimer() }
        generation &+= 1

        // 已经在显示中就完全不碰窗口层级：连按时每 80ms 做一次 orderFront
        //（窗口服务器往返）是明显的开销。
        if isFadingOut || !panel.isVisible {
            isFadingOut = false
            NSAnimationContext.beginGrouping()
            NSAnimationContext.current.duration = 0   // 打断可能正在进行的淡出
            panel.animator().alphaValue = 1
            NSAnimationContext.endGrouping()
        }
        if !panel.isVisible {
            position(panel)
            panel.orderFrontRegardless()
        }
    }

    private func restartHideTimer() {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: visibleSeconds, repeats: false) { [weak self] _ in
            self?.fadeOut()
        }
    }

    private func ensurePanel() -> NSPanel {
        if let panel { return panel }

        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered,
                            defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .statusBar
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.alphaValue = 0

        let host = NSHostingView(rootView: HUDView(model: model))
        host.frame = NSRect(origin: .zero, size: size)
        panel.contentView = host

        self.panel = panel
        return panel
    }

    private func position(_ panel: NSPanel) {
        // 跟随鼠标所在的屏幕，多屏下提示出现在正在用的那块屏上
        let screen = NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) }
            ?? NSScreen.main
        guard let screen else { return }
        let area = screen.visibleFrame
        panel.setFrameOrigin(NSPoint(x: area.midX - size.width / 2,
                                     y: area.minY + bottomInset))
    }

    private func fadeOut() {
        guard !operation.keepsVisible else { return }
        guard let panel else { return }
        isFadingOut = true
        generation &+= 1
        stopCountdown()
        let thisGeneration = generation
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = fadeSeconds
            ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().alphaValue = 0
        } completionHandler: { [weak self] in
            // 淡出期间又显示过就不要再隐藏
            guard let self, self.generation == thisGeneration else { return }
            self.isFadingOut = false
            panel.orderOut(nil)
        }
    }
}
