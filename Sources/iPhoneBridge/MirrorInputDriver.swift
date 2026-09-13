import Foundation

@MainActor
protocol MirrorCommanding: AnyObject {
    func command(_ type: MessageType, payload: Data) async throws
}

extension MirrorConnection: MirrorCommanding {}

@MainActor
final class MirrorInputDriver {
    private struct CancelledSequence: Error {}
    enum Operation {
        case pointer(UInt32, UInt32, UInt32, UInt32)
        case stroke(UInt32, HIDStroke, UInt32)
        case button(UInt32, UInt32)
        case scroll(MirrorScroll)
        case release
    }
    private let connection: any MirrorCommanding
    private let pause: (Duration) async throws -> Void
    private var operations: [Operation] = []
    private var drain: Task<Void, Never>?
    private var leased = false
    private var pointerHeld = false
    private var scrolling = false
    private var acquisitionUncertain = false
    private var epoch: UInt64 = 0
    var onError: ((String) -> Void)?

    init(connection: any MirrorCommanding, pause: @escaping (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) {
        self.connection = connection; self.pause = pause
    }

    func enqueue(_ operation: Operation) {
        if case .pointer(_, 1, _, _) = operation,
           scrolling || operations.contains(where: { if case .scroll = $0 { return true }; return false }) {
            cancel() // A real mouse press takes priority over an emulated wheel finger.
        }
        if case .scroll(let gesture) = operation {
            guard !pointerHeld,
                  !operations.contains(where: { if case .pointer = $0 { return true }; return false }) else { return }
            if let index = operations.firstIndex(where: { if case .scroll = $0 { return true }; return false }),
               case .scroll(let pending) = operations[index] {
                operations[index] = .scroll(pending.merged(with: gesture))
                return
            }
        }
        if case .pointer(_, 2, _, _) = operation, let last = operations.last,
           case .pointer(_, 2, _, _) = last { operations[operations.count - 1] = operation }
        else {
            guard operations.count < 128 else { cancel(); onError?("Input queue is full. Try again."); return }
            operations.append(operation)
        }
        if drain == nil { drain = Task { await process() } }
    }

    func cancel() {
        epoch &+= 1
        operations = [.release]
        if drain == nil { drain = Task { await process() } }
    }

    func cancelAndWait() async {
        cancel()
        while let task = drain { await task.value }
    }

    private func process() async {
        while !operations.isEmpty {
            if case .scroll = operations[0] {
                let current = epoch
                // Coalesce into the queued operation before taking a lease. There
                // is at most one further wheel gesture while a drag is executing.
                try? await pause(.milliseconds(30))
                guard current == epoch else { continue }
            }
            let operation = operations.removeFirst(), current = epoch
            if case .scroll(let gesture) = operation {
                guard !pointerHeld, gesture.moves else { continue }
                scrolling = true
            }
            do {
                defer { scrolling = false }
                if case .release = operation { try await release(); continue }
                if !leased {
                    acquisitionUncertain = true
                    do { try await connection.command(.acquireInput, payload: Data()) }
                    catch {
                        // An explicit server error proves no lease was granted.
                        // Timeout or transport uncertainty still requires cleanup.
                        if case MirrorError.remote = error { acquisitionUncertain = false }
                        throw error
                    }
                    acquisitionUncertain = false
                    leased = true
                }
                guard current == epoch else { continue }
                switch operation {
                case .pointer(let generation, let action, let x, let y):
                    try await connection.command(.pointer, payload: .words([generation, action, x, y]))
                    pointerHeld = action != 0
                    if action == 0 { try await release() }
                case .stroke(let generation, let stroke, let modifiers):
                    let usages: [UInt32] = (stroke.shift ? [225] : []) +
                        (modifiers & 1 != 0 ? [224] : []) + (modifiers & 2 != 0 ? [226] : []) + (modifiers & 4 != 0 ? [227] : [])
                    for usage in usages { try await key(generation, usage, true, epoch: current) }
                    try await key(generation, stroke.usage, true, epoch: current)
                    try await key(generation, stroke.usage, false, epoch: current)
                    for usage in usages.reversed() { try await key(generation, usage, false, epoch: current) }
                    if operations.isEmpty && !pointerHeld { try await release() }
                case .button(let generation, let button):
                    try await connection.command(.button, payload: .words([generation, button]))
                    try await release()
                case .scroll(let gesture):
                    try await scroll(gesture, epoch: current)
                case .release: break
                }
            } catch is CancelledSequence {
                // Focus or geometry changed while an ACK was in flight. Release
                // all held HID usages without continuing the retired sequence.
                try? await release()
            } catch {
                operations.removeAll()
                try? await release()
                onError?(error.localizedDescription)
            }
        }
        drain = nil
    }

    private func scroll(_ gesture: MirrorScroll, epoch: UInt64) async throws {
        let generation = gesture.geometry.generation
        try await scrollPointer(generation, action: 1, point: (gesture.x, gesture.y), epoch: epoch)
        for step in 1...4 {
            try await pause(.milliseconds(15))
            try await scrollPointer(generation, action: 2, point: gesture.point(step: step, of: 4), epoch: epoch)
        }
        try await scrollPointer(generation, action: 0, point: (gesture.x, gesture.endY), epoch: epoch)
        try await release()
    }

    private func scrollPointer(_ generation: UInt32, action: UInt32, point: (UInt32, UInt32), epoch: UInt64) async throws {
        guard epoch == self.epoch else { throw CancelledSequence() }
        try await connection.command(.pointer, payload: .words([generation, action, point.0, point.1]))
        guard epoch == self.epoch else { throw CancelledSequence() }
    }

    private func key(_ generation: UInt32, _ usage: UInt32, _ down: Bool, epoch: UInt64) async throws {
        guard epoch == self.epoch else { throw CancelledSequence() }
        try await connection.command(.key, payload: .words([generation, usage, down ? 1 : 0]))
        guard epoch == self.epoch else { throw CancelledSequence() }
    }

    private func release() async throws {
        guard leased || acquisitionUncertain else { return }
        leased = false; acquisitionUncertain = false; pointerHeld = false
        try await connection.command(.releaseInput, payload: Data())
    }
}
