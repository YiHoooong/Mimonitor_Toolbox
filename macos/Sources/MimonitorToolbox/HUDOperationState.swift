import Foundation

enum HUDOperationResult: Equatable {
    case success, failure, cancelled

    static func completion(isCurrentConnection: Bool, hasError: Bool,
                           verifiedApplications: [Bool]) -> HUDOperationResult {
        guard isCurrentConnection else { return .cancelled }
        guard !hasError else { return .failure }
        guard !verifiedApplications.isEmpty else { return .cancelled }
        return verifiedApplications.allSatisfy { $0 } ? .success : .failure
    }
}

enum HUDOperationPhase: Equatable {
    case running
    case finished(HUDOperationResult)
}

/// Main-thread ownership and completion tokens keep an old operation from replacing a new HUD.
struct HUDOperationState {
    private(set) var phase: HUDOperationPhase?
    private var token: UUID?
    var keepsVisible: Bool { phase == .running }
    var acceptsValueHint: Bool { !keepsVisible }

    mutating func begin() -> UUID {
        let token = UUID()
        self.token = token
        phase = .running
        return token
    }
    mutating func finish(token: UUID, result: HUDOperationResult) -> Bool {
        guard self.token == token, phase == .running else { return false }
        phase = .finished(result)
        return true
    }
    mutating func showValueHint() {
        guard acceptsValueHint else { return }
        token = nil
        phase = nil
    }
}
