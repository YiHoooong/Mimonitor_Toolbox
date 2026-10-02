/// 只在主线程修改。后台任务带着开始时的序号返回，过期结果不得改写连接状态。
struct ConnectionIntent {
    private(set) var generation = 0
    private(set) var allowsAutomaticRecovery = true

    var allowsStartupAttempt: Bool {
        generation == 0 && allowsAutomaticRecovery
    }

    mutating func beginConnection() -> Int {
        allowsAutomaticRecovery = true
        generation &+= 1
        return generation
    }

    mutating func beginScan() -> Int {
        allowsAutomaticRecovery = false
        generation &+= 1
        return generation
    }

    mutating func disconnect() {
        allowsAutomaticRecovery = false
        generation &+= 1
    }

    func isCurrent(_ request: Int) -> Bool {
        request == generation
    }
}
