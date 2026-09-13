import Foundation

// Queue-confined. The version observed at ingress distinguishes loss of a requested
// IDR from older overflow notices that the current request already covers.
struct DecoderRecovery {
    private(set) var requestVersion: UInt64 = 0
    private var outstanding = false
    private var pending: String?
    private var nextRequestAt = 0.0
    static let retryInterval = 0.25

    mutating func failed(_ detail: String, rejectedRequestVersion: UInt64? = nil) {
        if !outstanding || rejectedRequestVersion == requestVersion { pending = detail }
    }

    func retryDelay(now: Double, occupied: Int) -> Double? {
        guard pending != nil, occupied == 0 else { return nil }
        return max(0, nextRequestAt - now)
    }

    mutating func takeRequest(now: Double, occupied: Int) -> String? {
        guard retryDelay(now: now, occupied: occupied) == 0, let detail = pending else { return nil }
        pending = nil; outstanding = true; requestVersion &+= 1
        nextRequestAt = now + Self.retryInterval
        return detail
    }

    mutating func acceptedKeyframe() { pending = nil; outstanding = false }
    mutating func reset() { acceptedKeyframe(); nextRequestAt = 0 }
}

// MainActor ACK serialization retains one renewal arriving while a request is in
// flight. Cancellation epochs prevent an old completion from clearing a new task.
@MainActor
final class KeyframeRequester {
    private let send: () async throws -> Void
    private let completed: (String?) -> Void
    private var task: Task<Void, Never>?
    private var pending = false
    private var epoch: UInt64 = 0

    init(send: @escaping () async throws -> Void, completed: @escaping (String?) -> Void) {
        self.send = send; self.completed = completed
    }

    func request() {
        pending = true
        guard task == nil else { return }
        let currentEpoch = epoch
        task = Task { [weak self] in
            guard let self else { return }
            defer { if self.epoch == currentEpoch { self.task = nil } }
            while self.epoch == currentEpoch, self.pending, !Task.isCancelled {
                self.pending = false
                var errorDetail: String?
                do { try await self.send() } catch { errorDetail = error.localizedDescription }
                guard self.epoch == currentEpoch, !Task.isCancelled else { return }
                self.completed(errorDetail)
            }
        }
    }

    func cancel() { epoch &+= 1; pending = false; task?.cancel(); task = nil }
}
