import SwiftUI
import AppKit

// MARK: - 主题色
//
// 不要用 Color(nsColor: .controlColor) 这类语义色。它们的求值会走 macOS 的 CoreUI
// 主题系统（CUICoreThemeRenderer::CopyMeasurementsForRendition），内部是字符串哈希
// 查找；卡片和按钮一多，每次重绘要查几十上百次，足以让切页卡好几秒。
// 用 Color.primary 叠加透明度代替：同样是浅灰/浅白，但完全由 SwiftUI 解析，开销可忽略。

enum Theme {
    /// 卡片背景（原 controlBackgroundColor）
    static let card = Color.primary.opacity(0.05)
    /// 未选中按钮背景（原 controlColor）
    static let control = Color.primary.opacity(0.08)
    // 不再提供 panel 色：浮层底色用 .regularMaterial。
    // 曾经写成 `Color.primary.opacity(0.92)` —— Color.primary 是**文字色**，
    // 浅色模式下它是黑色，于是卡片底色渲染成了近黑。
    /// 卡片描边
    static let stroke = Color.primary.opacity(0.08)
}

// MARK: - 页面刷新时的 loading 遮罩（对应原版 _show_loading_overlay）

struct LoadingOverlay: ViewModifier {
    let isLoading: Bool

    func body(content: Content) -> some View {
        content.overlay {
            if isLoading {
                ZStack {
                    Color.black.opacity(0.10)
                    VStack(spacing: 10) {
                        ProgressView().controlSize(.large)
                        Text("正在刷新数据...").font(.callout).foregroundColor(.secondary)
                    }
                    .padding(22)
                    // 用系统材质：自动适配浅色/深色模式，也是 macOS 上浮层的惯用做法。
                    // 卡片本身不大，模糊合成的开销可以接受（之前性能问题出在整页遮罩上）。
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(.primary.opacity(0.08), lineWidth: 1)
                    )
                }
            }
        }
    }
}

extension View {
    func loadingOverlay(_ isLoading: Bool) -> some View {
        modifier(LoadingOverlay(isLoading: isLoading))
    }
}

// MARK: - 卡片容器（对应 qfluentwidgets 的 SimpleCardWidget）

struct SectionCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.headline)
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.stroke))
    }
}

// MARK: - 可选中按钮（对应 ToggleButton）

struct SelectableButton: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.callout)
                .frame(minWidth: 56)
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
                .background(isSelected ? Color.accentColor : Theme.control)
                .foregroundColor(isSelected ? .white : .primary)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 选项按钮组（对应 _btn_section）

struct ButtonGroupSection: View {
    let title: String
    let options: [Option]
    let selectedValue: Int
    let onSelect: (Int) -> Void

    var body: some View {
        SectionCard(title: title) {
            if options.count == 2 {
                // 两项铺满整行会变成一根很宽的分段条，改回原来的两个按钮。
                HStack(spacing: 8) {
                    ForEach(options) { option in
                        SelectableButton(title: option.label,
                                         isSelected: option.value == selectedValue) {
                            onSelect(option.value)
                        }
                    }
                    Spacer(minLength: 0)
                }
            } else {
                // 三项及以上：用系统分段控件铺满卡片，和滑条一样占满整行。
                SegmentedOptionPicker(options: options,
                                      selectedValue: selectedValue,
                                      onSelect: onSelect)
                    .frame(maxWidth: .infinity)
            }
        }
    }
}

// MARK: - 系统分段控件（NSSegmentedControl）
//
// SwiftUI 的 `.segmented` Picker 底层也是它，但没有暴露 `segmentDistribution`；
// 放到卡片里时各段按内容宽度缩在中间，左右留一大片空白。
// 包一层 AppKit 控件，把 `segmentDistribution` 设为 `.fillEqually`，各段等宽铺满。

struct SegmentedOptionPicker: NSViewRepresentable {
    let options: [Option]
    let selectedValue: Int
    /// 选项组用等宽；设置页的两段式控件用按内容分配，长标签才不会被截断。
    var distribution: NSSegmentedControl.Distribution = .fillEqually
    var controlSize: NSControl.ControlSize = .large
    let onSelect: (Int) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSSegmentedControl {
        let control = NSSegmentedControl(
            labels: options.map(\.label),
            trackingMode: .selectOne,
            target: context.coordinator,
            action: #selector(Coordinator.selectionChanged(_:))
        )
        control.segmentDistribution = distribution
        control.controlSize = controlSize
        control.setContentHuggingPriority(.defaultLow, for: .horizontal)
        control.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        updateSelection(control)
        return control
    }

    func updateNSView(_ control: NSSegmentedControl, context: Context) {
        context.coordinator.parent = self
        control.segmentDistribution = distribution
        control.controlSize = controlSize
        if control.segmentCount != options.count {
            control.segmentCount = options.count
        }
        for (index, option) in options.enumerated()
        where control.label(forSegment: index) != option.label {
            control.setLabel(option.label, forSegment: index)
        }
        updateSelection(control)
    }

    private func updateSelection(_ control: NSSegmentedControl) {
        control.selectedSegment = options.firstIndex { $0.value == selectedValue } ?? -1
    }

    final class Coordinator: NSObject {
        var parent: SegmentedOptionPicker

        init(_ parent: SegmentedOptionPicker) {
            self.parent = parent
        }

        @objc func selectionChanged(_ sender: NSSegmentedControl) {
            let index = sender.selectedSegment
            guard index >= 0, index < parent.options.count else { return }
            parent.onSelect(parent.options[index].value)
        }
    }
}

// MARK: - 轻量滑条
//
// 不用 SwiftUI 的 Slider：它底层是 AppKit 的 NSSlider，每创建一个都要向 CoreUI
// 主题系统解析 rendition（字符串哈希查找）。实测画面页 6 个滑条要 500ms，
// 切页时肉眼可见地卡。自绘一个纯 SwiftUI 的滑条，创建开销可以忽略。

struct FastSlider: View {
    @Binding var value: Double
    let range: ClosedRange<Double>
    let step: Double
    let onCommit: (Double) -> Void

    private let trackHeight: CGFloat = 4
    private let knobSize: CGFloat = 15

    var body: some View {
        GeometryReader { geo in
            let width = max(geo.size.width, 1)
            let height = geo.size.height
            let span = range.upperBound - range.lowerBound
            let ratio = span > 0 ? (value - range.lowerBound) / span : 0
            let clamped = min(max(ratio, 0), 1)
            let knobX = clamped * (width - knobSize) + knobSize / 2

            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.15))
                    .frame(height: trackHeight)
                Capsule()
                    .fill(Color.accentColor)
                    .frame(width: clamped * width, height: trackHeight)
                Circle()
                    .fill(Color.white)
                    .frame(width: knobSize, height: knobSize)
                    .shadow(color: .black.opacity(0.25), radius: 1.5, y: 0.5)
                    .position(x: knobX, y: height / 2)
            }
            .frame(height: height)
            // 整条轨道都要能接收拖拽，而不只是滑块本身
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        let x = min(max(g.location.x, 0), width)
                        var v = range.lowerBound + (x / width) * span
                        if step > 0 { v = (v / step).rounded() * step }
                        let clampedV = min(max(v, range.lowerBound), range.upperBound)
                        if clampedV != value { value = clampedV }
                    }
                    .onEnded { _ in onCommit(value) }
            )
        }
        .frame(height: 22)
    }
}

// MARK: - 滑条行（对应 _add_slider，松手提交）

struct SliderRow: View {
    let title: String
    let range: ClosedRange<Double>
    let externalValue: Int
    let onCommit: (Int) -> Void

    @State private var value: Double

    init(title: String,
         range: ClosedRange<Double>,
         externalValue: Int,
         onCommit: @escaping (Int) -> Void) {
        self.title = title
        self.range = range
        self.externalValue = externalValue
        self.onCommit = onCommit
        self._value = State(initialValue: Double(externalValue))
    }

    var body: some View {
        HStack(spacing: 12) {
            Text(title).frame(width: 96, alignment: .leading)
            FastSlider(value: $value, range: range, step: 1) { v in
                onCommit(Int(v))
            }
            Text("\(Int(value))")
                .monospacedDigit()
                .frame(width: 44, alignment: .trailing)
                .foregroundColor(.secondary)
        }
        // 和按钮组一样套一层卡片：原版每个滑条也是独立的 SimpleCardWidget
        // （pages.py:_add_slider）。这样滑条标签才能和「画面模式」「色温」的
        // 标题左边对齐，视觉上落在同一条竖线上。
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.stroke))
        .onChange(of: externalValue) { newValue in
            value = Double(newValue)
        }
    }
}

// MARK: - 页面标题栏 + 刷新按钮

struct PageHeader: View {
    let title: String
    let onRefresh: () -> Void

    var body: some View {
        HStack {
            Text(title).font(.title2.bold())
            Spacer()
            Button(action: onRefresh) {
                Label("刷新数据", systemImage: "arrow.clockwise")
            }
        }
    }
}
