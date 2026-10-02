import Foundation

enum MenuBarPanelLayout {
    // The 320-point panel has 14-point horizontal insets on each side.
    static let contentWidth: CGFloat = 292
    static func presetWidth(count: Int) -> CGFloat { max(contentWidth, CGFloat(count) * 80) }
    static func scrollHeight(contentHeight: CGFloat) -> CGFloat { min(max(contentHeight, 1), 420) }
}
