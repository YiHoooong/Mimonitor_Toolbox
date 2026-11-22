import Foundation

struct BrightnessWritePlan: Equatable {
    let useRamp: Bool
    let step: Int
    let stepMs: Int
}

/// 亮度键的 JNI 写入节奏。
///
/// 原生 OSD 只有 16 格，一格约 6.25%。直接一次写目标值在高端背光上跳变明显，
/// 所以单格变化拆成 1% 的微步，每步间隔 8ms；1–2% 的小变化仍然直接写，
/// 更大的合并跳变继续用最多 16 步的快速渐变，避免长按被拖慢。
enum BrightnessRampPlanner {
    static let oneChiclet = Int(ceil(100.0 / 16.0))
    static let directMaxDelta = 2
    static let smoothStep = 1
    static let smoothStepMs = 8
    static let maxRampSteps = 16

    static func plan(from: Int, to: Int) -> BrightnessWritePlan {
        let delta = abs(to - from)
        if delta <= directMaxDelta {
            return BrightnessWritePlan(useRamp: false, step: max(1, delta), stepMs: 0)
        }
        if delta <= oneChiclet {
            return BrightnessWritePlan(useRamp: true, step: smoothStep, stepMs: smoothStepMs)
        }
        let step = max(1, Int(ceil(Double(delta) / Double(maxRampSteps))))
        return BrightnessWritePlan(useRamp: true, step: step, stepMs: 0)
    }
}
