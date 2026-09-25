import AppKit

enum BenchmarkGate {
    static func response(_ sequence: UInt32?, baseline: UInt32) throws -> Bool {
        guard let sequence else { return false }
        if sequence >= 0x8000_0000 { throw MirrorError.invalid("Animation fixture detected; no further input sent.") }
        if sequence == baseline { return false }
        guard sequence == baseline &+ 1 else { throw MirrorError.invalid("Unexpected sequence; no further input sent.") }
        return true
    }
    static func percentile(_ values: [Double], _ fraction: Double) -> Double? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted(), position = Double(values.count - 1) * fraction
        let low = Int(floor(position)), high = Int(ceil(position))
        return sorted[low] + (sorted[high] - sorted[low]) * (position - Double(low))
    }
}

@MainActor
final class MirrorBenchmark {
    private let connection: MirrorConnection
    private weak var window: NSWindow?
    private let decoder: HEVCDecoder
    private let setActive: (Bool) -> Void
    private let prepareInput: () async -> Void
    private var coordinator: Task<Void, Never>?
    private var health: Task<Void, Never>?
    private var active = false
    private var stopped = false
    private var latest: BarcodeObservation?
    private var decodedEvents: [DecodedObservation] = []
    private var displayEvents: [(sequence: UInt32, submittedAt: Double, phonePtsNs: UInt64)] = []
    private var decodedCount: UInt64 = 0
    private var submittedCount: UInt64 = 0
    private var hadHidden = false
    private var hiddenMs = 0.0
    private var lastVisibility = 0.0
    private var previouslyHidden = false
    private let base = URL(string: "http://127.0.0.1:15802/")!
    private var now: Double { ProcessInfo.processInfo.systemUptime * 1000 }

    init(connection: MirrorConnection, window: NSWindow, decoder: HEVCDecoder,
         prepareInput: @escaping () async -> Void, setActive: @escaping (Bool) -> Void) {
        self.connection = connection; self.window = window; self.decoder = decoder
        self.prepareInput = prepareInput; self.setActive = setActive
    }

    func start() {
        decoder.setDiagnosticsEnabled(true)
        coordinator = Task { [weak self] in
            var previous = ""
            while let self, !self.stopped, !Task.isCancelled {
                do {
                    var request = URLRequest(url: self.base.appendingPathComponent("benchmark-run"))
                    request.cachePolicy = .reloadIgnoringLocalCacheData; request.timeoutInterval = 3
                    let (data, _) = try await URLSession.shared.data(for: request)
                    if let options = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let id = options["id"] as? String, !id.isEmpty, id != previous,
                       self.connection.geometry != nil || options["mode"] as? String == "image-quality" {
                        previous = id
                        await self.run(options)
                    }
                } catch { /* The opt-in fixture server can stop independently. */ }
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
        health = Task { [weak self] in
            var previousAt = self?.now ?? 0, previousDecoded: UInt64 = 0, previousSubmitted: UInt64 = 0
            while let self, !self.stopped, !Task.isCancelled {
                let diagnostics = await self.decoder.diagnostics(health: true)
                guard !self.stopped, !Task.isCancelled else { return }
                let current = self.now, elapsed = current - previousAt
                let decoded = self.decodedCount, submitted = self.submittedCount
                let report: [String: Any] = ["mode": "renderer-health", "renderer": "native-hevc",
                    "processID": ProcessInfo.processInfo.processIdentifier,
                    "id": "native-renderer", "recordedAt": Date().timeIntervalSince1970 * 1000,
                    "connected": self.connection.geometry != nil, "elapsedMs": elapsed,
                    "decodedFrames": decoded, "displaySubmittedFrames": submitted,
                    "decodeFps": elapsed > 0 && decoded >= previousDecoded ? Double(decoded - previousDecoded) * 1000 / elapsed : 0,
                    "displaySubmittedFps": elapsed > 0 && submitted >= previousSubmitted ? Double(submitted - previousSubmitted) * 1000 / elapsed : 0,
                    "visibility": ["available": true, "hidden": self.isHidden],
                    "decoderDiagnostics": diagnostics.report]
                previousAt = current; previousDecoded = decoded; previousSubmitted = submitted
                await self.post(report)
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    func received(_ frame: DecodedFrame, observations: [DecodedObservation], decodedCount: UInt64) {
        self.decodedCount = decodedCount
        guard active else { return }
        latest = frame.barcode
        for observation in observations where decodedEvents.count < 4096 {
            if decodedEvents.last?.sequence != observation.sequence { decodedEvents.append(observation) }
        }
        observeVisibility()
    }

    func submitted(_ frame: DecodedFrame) {
        submittedCount += 1
        guard active else { return }
        if let sequence = frame.barcode?.sequence, displayEvents.last?.sequence != sequence, displayEvents.count < 4096 {
            displayEvents.append((sequence, now, frame.pts))
        }
        observeVisibility()
    }

    private var isHidden: Bool {
        guard let window else { return true }
        return NSApp.isHidden || !window.isVisible || window.isMiniaturized || !window.occlusionState.contains(.visible)
    }
    private func observeVisibility() {
        let current = now
        if previouslyHidden { hiddenMs += current - lastVisibility }
        previouslyHidden = isHidden; hadHidden = hadHidden || previouslyHidden; lastVisibility = current
    }

    private func run(_ options: [String: Any]) async {
        guard !active, !stopped else { return }
        let mode = options["mode"] as? String ?? ""
        var report: [String: Any] = ["mode": mode, "renderer": "native-hevc", "codec": "hevc",
            "processID": ProcessInfo.processInfo.processIdentifier,
            "id": options["id"] ?? NSNull(), "label": options["label"] ?? "", "startedAt": now,
            "timeOrigin": Date().timeIntervalSince1970 * 1000 - now,
            "timing": "Mac timestamps are uptime milliseconds; videoReceivedAt is complete VIDEO acceptance; phonePtsNs is phone nanoseconds, not a synchronized Mac timestamp",
            "measurement": "decoded pixels and display submission; physical scanout is not measured"]
        active = true; setActive(true)
        await prepareInput()
        latest = nil; decodedEvents.removeAll(); displayEvents.removeAll()
        hadHidden = isHidden; previouslyHidden = isHidden; hiddenMs = 0; lastVisibility = now
        decoder.setBenchmarkEnabled(true)
        do {
            guard mode != "image-quality" else { throw MirrorError.invalid("image-quality measured JPEG and is unsupported by the native HEVC mirror.") }
            guard options["compressionLevel"] == nil else { throw MirrorError.invalid("JPEG compressionLevel is unsupported by the native HEVC mirror.") }
            guard mode == "input" || mode == "animation" else { throw MirrorError.invalid("Unsupported benchmark mode.") }
            try await connection.command(.requestKeyframe)
            if mode == "input" { try await runInput(options, report: &report) }
            else { try await runAnimation(options, report: &report) }
        } catch { report["failure"] = ["reason": error.localizedDescription] }
        observeVisibility()
        report["finishedAt"] = now
        report["visibility"] = ["available": true, "hidden": isHidden, "hadHidden": hadHidden, "hiddenMs": hiddenMs]
        report["visibleQualification"] = !hadHidden
        if hadHidden && report["failure"] == nil {
            report["failure"] = ["reason": "Mirror was hidden during the benchmark; visible performance is unqualified."]
        }
        if let latest {
            report["barcode"] = ["x": latest.x, "y": latest.y, "pitch": latest.pitch, "width": latest.width, "height": latest.height]
        }
        decoder.setBenchmarkEnabled(false); active = false; setActive(false)
        report["decoderDiagnostics"] = await decoder.diagnostics(health: false).report
        await post(report)
    }

    private func tick() async throws {
        try Task.checkCancellation()
        guard !stopped, connection.geometry != nil else { throw MirrorError.disconnected }
        observeVisibility()
        try await Task.sleep(for: .milliseconds(5))
    }

    private func calibrate() async throws -> UInt32 {
        let deadline = now + 2000
        var candidate: UInt32?, stableSince = now
        while now < deadline {
            let sequence = latest?.sequence
            if let sequence, sequence >= 0x8000_0000 { throw MirrorError.invalid("Animation fixture detected; no input sent.") }
            if sequence != candidate { candidate = sequence; stableSince = now }
            if let candidate, now - stableSince >= 100 { return candidate }
            try await tick()
        }
        throw MirrorError.invalid("No stable input fixture barcode within 2s; no input sent.")
    }

    private func runInput(_ options: [String: Any], report: inout [String: Any]) async throws {
        let trials = options["trials"] as? Int ?? 30
        guard (1...100).contains(trials) else { throw MirrorError.invalid("trials must be 1–100.") }
        report["requestedTrials"] = trials
        let baseline = try await calibrate()
        report["calibration"] = ["sequence": baseline, "calibratedAt": now]
        var samples: [Double] = [], displaySamples: [Double] = [], events: [[String: Any]] = []
        do {
            // A timed-out ACK can still mean the daemon granted ownership.
            // The catch below releases that uncertain acquisition as well.
            try await connection.command(.acquireInput)
            for trial in 0..<trials {
                guard let baseline = latest?.sequence, baseline < 0x8000_0000, let geometry = connection.geometry else {
                    throw MirrorError.invalid("Live input fixture disappeared; no further input sent.")
                }
                let started = now
                try await connection.command(.key, payload: .words([geometry.generation, 44, 1]))
                // A held key is released even if cancellation or response timeout occurs.
                do {
                    let remaining = max(0, started + 20 - now)
                    try await Task.sleep(for: .milliseconds(remaining))
                    try await connection.command(.key, payload: .words([geometry.generation, 44, 0]))
                } catch {
                    try? await connection.command(.key, payload: .words([geometry.generation, 44, 0]))
                    throw error
                }
                let deadline = started + 2000
                while now < deadline {
                    if try BenchmarkGate.response(latest?.sequence, baseline: baseline),
                       let decoded = decodedEvents.first(where: { $0.sequence == baseline &+ 1 && $0.decodedAt >= started }),
                       let displayed = displayEvents.first(where: { $0.sequence == baseline &+ 1 && $0.submittedAt >= started }) {
                        samples.append(decoded.decodedAt - started); displaySamples.append(displayed.submittedAt - started)
                        var event = decoded.report
                        event["baseline"] = baseline; event["sentAt"] = started
                        event["displaySubmittedAt"] = displayed.submittedAt
                        event["displaySubmittedPhonePtsNs"] = displayed.phonePtsNs
                        events.append(event)
                        break
                    }
                    try await tick()
                }
                if samples.count != trial + 1 { throw MirrorError.invalid("Sequence response timed out; no retry.") }
                report["completedTrials"] = samples.count; report["events"] = events
                report["samples"] = samples; report["displaySubmittedSamples"] = displaySamples
                if trial + 1 < trials { try await Task.sleep(for: .milliseconds(100)) }
            }
            try await connection.command(.releaseInput)
        } catch {
            try? await connection.command(.releaseInput)
            report["completedTrials"] = samples.count; report["events"] = events
            report["samples"] = samples; report["displaySubmittedSamples"] = displaySamples
            summarize(samples, display: displaySamples, report: &report)
            throw error
        }
        summarize(samples, display: displaySamples, report: &report)
    }

    private func runAnimation(_ options: [String: Any], report: inout [String: Any]) async throws {
        let duration = options["durationMs"] as? Double ?? 10_000
        guard duration.isFinite, (100...30_000).contains(duration) else { throw MirrorError.invalid("durationMs must be 100–30000.") }
        report["requestedDurationMs"] = duration
        let deadline = now + duration
        while now < deadline { try await tick() }
        guard !decodedEvents.isEmpty else { throw MirrorError.invalid("No live fixture barcode observed.") }
        var skipped: UInt64 = 0, gaps: [Double] = []
        for (previous, next) in zip(decodedEvents, decodedEvents.dropFirst()) {
            let delta = next.sequence &- previous.sequence
            guard delta < 0x8000_0000 else { throw MirrorError.invalid("Sequence moved backwards or fixture changed.") }
            skipped += UInt64(delta - 1); gaps.append(next.decodedAt - previous.decodedAt)
        }
        let displayGaps = zip(displayEvents, displayEvents.dropFirst()).map { $1.submittedAt - $0.submittedAt }
        let elapsed = (decodedEvents.last?.decodedAt ?? 0) - (decodedEvents.first?.decodedAt ?? 0)
        let displayElapsed = (displayEvents.last?.submittedAt ?? 0) - (displayEvents.first?.submittedAt ?? 0)
        report["uniqueSequences"] = decodedEvents.count; report["sourceFramesSkipped"] = skipped
        report["receivedFps"] = elapsed > 0 ? Double(decodedEvents.count - 1) * 1000 / elapsed : 0
        report["displaySubmittedFps"] = displayElapsed > 0 ? Double(displayEvents.count - 1) * 1000 / displayElapsed : 0
        report["events"] = decodedEvents.map(\.report)
        report["displaySubmittedEvents"] = displayEvents.map {
            ["sequence": $0.sequence, "submittedAt": $0.submittedAt, "phonePtsNs": $0.phonePtsNs] as [String: Any]
        }
        report["samples"] = gaps
        summarize(gaps, display: displayGaps, report: &report)
    }

    private func summarize(_ samples: [Double], display: [Double], report: inout [String: Any]) {
        report["p50"] = BenchmarkGate.percentile(samples, 0.5) ?? NSNull() as Any
        report["p95"] = BenchmarkGate.percentile(samples, 0.95) ?? NSNull() as Any
        report["displaySubmittedP50"] = BenchmarkGate.percentile(display, 0.5) ?? NSNull() as Any
        report["displaySubmittedP95"] = BenchmarkGate.percentile(display, 0.95) ?? NSNull() as Any
        report["sampleUnit"] = "milliseconds"
    }

    private func post(_ report: [String: Any]) async {
        guard let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]) else { return }
        var request = URLRequest(url: base.appendingPathComponent("metrics"))
        request.httpMethod = "POST"; request.timeoutInterval = 3
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("IPBM/1", forHTTPHeaderField: "X-iPhoneBridge-Protocol")
        request.httpBody = data
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let response = response as? HTTPURLResponse, response.statusCode == 204 else {
                throw MirrorError.invalid("Metrics endpoint returned HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0).")
            }
        } catch { NSLog("Native benchmark report %@ could not be saved: %@", report["id"] as? String ?? "unknown", error.localizedDescription) }
    }

    func stop() {
        stopped = true; coordinator?.cancel(); health?.cancel()
        decoder.setBenchmarkEnabled(false); decoder.setDiagnosticsEnabled(false)
    }
}
