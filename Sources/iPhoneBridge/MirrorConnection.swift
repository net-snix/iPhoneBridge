import Foundation
import Network

@MainActor
final class MirrorConnection {
    var onGeometry: ((MirrorGeometry) -> Void)?
    var onFormat: ((MirrorFormat) -> Void)?
    var onVideo: ((MirrorVideo) -> Void)?
    var onState: ((Bool, String) -> Void)?
    private var connection: NWConnection?
    private var parser = MessageParser()
    private var serial: UInt64 = 0
    private var requestID: UInt32 = 0
    private var pending: [UInt32: CheckedContinuation<MirrorMessage, Error>] = [:]
    private var timeouts: [UInt32: Task<Void, Never>] = [:]
    private var retry: Task<Void, Never>?
    private var attempts = 0
    private var stopped = true
    private var greeted = false
    private var ready = false
    private(set) var geometry: MirrorGeometry?

    func start() {
        stop()
        stopped = false; attempts = 0
        open()
    }

    func stop() {
        stopped = true
        retry?.cancel(); retry = nil
        retire()
    }

    private func retire() {
        serial &+= 1
        connection?.cancel(); connection = nil
        greeted = false; ready = false; geometry = nil
        parser = MessageParser()
        for timeout in timeouts.values { timeout.cancel() }
        timeouts.removeAll()
        let unfinished = pending; pending.removeAll()
        for continuation in unfinished.values { continuation.resume(throwing: MirrorError.disconnected) }
    }

    private func open() {
        guard !stopped else { return }
        retire()
        let generation = serial
        onState?(false, attempts == 0 ? "Connecting…" : "Reconnecting…")
        let parameters = NWParameters.tcp
        if let tcp = parameters.defaultProtocolStack.transportProtocol as? NWProtocolTCP.Options { tcp.noDelay = true }
        let current = NWConnection(host: "127.0.0.1", port: 15901, using: parameters)
        connection = current
        current.stateUpdateHandler = { [weak self] state in
            MainActor.assumeIsolated {
                guard let self, self.serial == generation else { return }
                switch state {
                case .ready: self.receive(current, generation: generation)
                case .failed(let error): self.failed(error.localizedDescription)
                case .waiting(let error): self.failed(error.localizedDescription)
                default: break
                }
            }
        }
        current.start(queue: .main)
        retry = Task { [weak self] in
            try? await Task.sleep(for: .seconds(12))
            guard !Task.isCancelled, let self, self.serial == generation, !self.ready else { return }
            self.failed("The native mirror did not connect in time.")
        }
    }

    private func receive(_ current: NWConnection, generation: UInt64) {
        current.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, complete, error in
            MainActor.assumeIsolated {
                guard let self, self.serial == generation, !self.stopped else { return }
                do {
                    if let data { for message in try self.parser.append(data) { try self.accept(message) } }
                    if let error { throw error }
                    if complete { throw MirrorError.disconnected }
                    self.receive(current, generation: generation)
                } catch { self.failed(error.localizedDescription) }
            }
        }
    }

    private func accept(_ message: MirrorMessage) throws {
        if message.requestID != 0 {
            guard let continuation = pending.removeValue(forKey: message.requestID) else { return }
            timeouts.removeValue(forKey: message.requestID)?.cancel()
            if message.type == .error {
                guard message.payload.count >= 4 else { continuation.resume(throwing: MirrorError.invalid("Invalid server error.")); return }
                let detail = String(data: message.payload.dropFirst(4), encoding: .utf8) ?? "The iPhone rejected this command."
                continuation.resume(throwing: MirrorError.remote(message.payload.uint32(at: 0), detail))
            } else { continuation.resume(returning: message) }
            return
        }
        if !greeted {
            guard message.type == .hello, message.payload.count == 24,
                  message.payload.prefix(4) == Data("IPBM".utf8),
                  message.payload[4] == 0, message.payload[5] == 1,
                  message.payload[6] == 0, message.payload[7] & 7 == 7 else {
                throw MirrorError.invalid("The iPhone is not running native mirror protocol v1.")
            }
            greeted = true
            updateGeometry(try MirrorGeometry(payload: message.payload.subdata(in: 8..<24)))
            let generation = serial
            Task { [weak self] in
                guard let self else { return }
                do {
                    try await self.command(.subscribe, payload: .words([1]))
                    guard self.serial == generation else { return }
                    self.ready = true; self.attempts = 0; self.retry?.cancel(); self.retry = nil
                    self.onState?(true, "USB")
                } catch { if self.serial == generation { self.failed(error.localizedDescription) } }
            }
            return
        }
        switch message.type {
        case .geometry: updateGeometry(try MirrorGeometry(payload: message.payload))
        case .format:
            let format = try MirrorFormat(payload: message.payload)
            updateGeometry(format.geometry); onFormat?(format)
        case .video:
            onVideo?(try MirrorVideo(payload: message.payload, receivedAt: ProcessInfo.processInfo.systemUptime * 1000))
        case .stats: break
        default: throw MirrorError.invalid("Unexpected native mirror message.")
        }
    }

    private func updateGeometry(_ value: MirrorGeometry) {
        if geometry != value { geometry = value; onGeometry?(value) }
    }

    func command(_ type: MessageType, payload: Data = Data()) async throws {
        let response = try await request(type, payload: payload)
        guard response.type == .ack, response.payload.isEmpty else { throw MirrorError.invalid("Expected command acknowledgement.") }
    }

    func request(_ type: MessageType, payload: Data = Data()) async throws -> MirrorMessage {
        guard !stopped, greeted, let current = connection else { throw MirrorError.disconnected }
        guard pending.count < 64 else { throw MirrorError.invalid("Too many pending mirror commands.") }
        requestID &+= 1
        if requestID == 0 { requestID = 1 }
        let id = requestID, generation = serial
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            timeouts[id] = Task { [weak self] in
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled, let self, self.serial == generation else { return }
                self.timeouts.removeValue(forKey: id)
                self.pending.removeValue(forKey: id)?.resume(throwing: MirrorError.timeout)
            }
            current.send(content: MirrorMessage(type: type, requestID: id, payload: payload).encoded(),
                         completion: .contentProcessed { [weak self] error in
                guard let error else { return }
                MainActor.assumeIsolated {
                    guard let self, self.serial == generation else { return }
                    self.failed(error.localizedDescription)
                }
            })
        }
    }

    private func failed(_ detail: String) {
        guard !stopped else { return }
        retry?.cancel(); retire()
        onState?(false, detail)
        let delay = min(1 << min(attempts, 4), 10)
        attempts += 1
        retry = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            self?.open()
        }
    }
}
