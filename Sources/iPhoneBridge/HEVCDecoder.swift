import Foundation
import VideoToolbox

// VideoToolbox owns the immutable pixel buffer; this wrapper retains it across queues.
final class DecodedFrame: @unchecked Sendable {
    let pixelBuffer: CVPixelBuffer
    let geometry: MirrorGeometry
    let pts: UInt64
    let decodedAt: Double
    let videoReceivedAt: Double?
    let decodeSubmittedAt: Double?
    let barcode: BarcodeObservation?
    init(_ pixelBuffer: CVPixelBuffer, geometry: MirrorGeometry, pts: UInt64, barcode: BarcodeObservation?, decodedAt: Double? = nil,
         videoReceivedAt: Double? = nil, decodeSubmittedAt: Double? = nil) {
        self.pixelBuffer = pixelBuffer; self.geometry = geometry; self.pts = pts; self.barcode = barcode
        self.decodedAt = decodedAt ?? ProcessInfo.processInfo.systemUptime * 1000
        self.videoReceivedAt = videoReceivedAt; self.decodeSubmittedAt = decodeSubmittedAt
    }
}

// All mutable decoder state is confined to queue. ingressLock bounds retained compressed
// frames before dispatch, including asynchronous VT work; it does not protect VT state.
final class HEVCDecoder: @unchecked Sendable {
    private let queue: DispatchQueue
    private let ingressLock = NSLock()
    private var inFlight = 0
    private var ingressRequestVersion: UInt64 = 0
    private var session: VTDecompressionSession?
    private var description: CMVideoFormatDescription?
    private var format: MirrorFormat?
    private var token: UInt64 = 0
    private var awaitingKeyframe = true
    private var recovery = DecoderRecovery()
    private var recoveryWork: DispatchWorkItem?
    private var recoveryEpoch: UInt64 = 0
    private var lastPTS: UInt64?
    private var scanner = BarcodeScanner()
    private var benchmarkEnabled = false
    private var diagnosticsEnabled = false
    private var healthDiagnostics = DecoderDiagnostics()
    private var benchmarkDiagnostics = DecoderDiagnostics()
    private var lastBarcodeSequence: UInt32?
    let onFrame: @Sendable (DecodedFrame) -> Void
    let onError: @Sendable (String) -> Void

    init(queue: DispatchQueue = DispatchQueue(label: "net.snix.iPhoneBridge.decode", qos: .userInteractive),
         onFrame: @escaping @Sendable (DecodedFrame) -> Void, onError: @escaping @Sendable (String) -> Void) {
        self.queue = queue; self.onFrame = onFrame; self.onError = onError
    }

    func reset() { queue.async {
        self.invalidate(); self.format = nil; self.resetRecovery(); self.scanner = BarcodeScanner()
        self.lastBarcodeSequence = nil
    } }
    func setBenchmarkEnabled(_ enabled: Bool) { queue.async {
        self.benchmarkEnabled = enabled
        if enabled { self.benchmarkDiagnostics = DecoderDiagnostics(); self.lastBarcodeSequence = nil }
    } }
    func setDiagnosticsEnabled(_ enabled: Bool) { queue.async {
        self.diagnosticsEnabled = enabled; self.healthDiagnostics = DecoderDiagnostics()
    } }
    func diagnostics(health: Bool) async -> DecoderDiagnostics {
        await withCheckedContinuation { continuation in queue.async {
            let snapshot = health ? self.healthDiagnostics : self.benchmarkDiagnostics
            if health { self.healthDiagnostics = DecoderDiagnostics() }
            continuation.resume(returning: snapshot)
        } }
    }

    func configure(_ newFormat: MirrorFormat) {
        queue.async {
            if self.session != nil, self.format?.geometry == newFormat.geometry,
               self.format?.parameters == newFormat.parameters { return }
            if self.format?.geometry != newFormat.geometry { self.resetRecovery() }
            self.invalidate(); self.format = newFormat
            self.scanner = BarcodeScanner()
            self.lastBarcodeSequence = nil
            do { try self.createSession(newFormat) }
            catch { self.failed(error.localizedDescription) }
        }
    }

    func submit(_ video: MirrorVideo) {
        ingressLock.lock()
        guard inFlight < 8 else {
            let requestVersion = ingressRequestVersion
            ingressLock.unlock()
            let generation = video.generation, keyframe = video.keyframe
            queue.async {
                guard self.format?.geometry.generation == generation else { return }
                self.recordDiagnostics { counters in
                    counters.ingressDrops += 1
                    if keyframe { counters.keyframeIngressDrops += 1 }
                }
                self.failed("The video decoder fell behind; requesting a fresh keyframe.",
                            rejectedRequestVersion: keyframe ? requestVersion : nil)
            }
            return
        }
        inFlight += 1
        ingressLock.unlock()
        queue.async { self.decode(video) }
    }

    private func finished() {
        ingressLock.lock(); inFlight -= 1; ingressLock.unlock()
        scheduleRecovery()
    }

    private func invalidate() {
        token &+= 1
        if let session { VTDecompressionSessionInvalidate(session) }
        session = nil; description = nil; awaitingKeyframe = true; lastPTS = nil
    }

    private func failed(_ detail: String, rejectedRequestVersion: UInt64? = nil) {
        invalidate()
        recovery.failed(detail, rejectedRequestVersion: rejectedRequestVersion)
        scheduleRecovery()
    }

    private func resetRecovery(acceptedKeyframe: Bool = false) {
        recoveryEpoch &+= 1; recoveryWork?.cancel(); recoveryWork = nil
        if acceptedKeyframe { recovery.acceptedKeyframe() } else { recovery.reset() }
    }

    private func scheduleRecovery() {
        guard recoveryWork == nil, let generation = format?.geometry.generation else { return }
        let occupied = ingressLock.withLock { inFlight }
        guard let delay = recovery.retryDelay(now: ProcessInfo.processInfo.systemUptime, occupied: occupied) else { return }
        let epoch = recoveryEpoch
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.recoveryEpoch == epoch, self.format?.geometry.generation == generation else { return }
            self.recoveryWork = nil
            // Capacity can change while this work is queued. finished() will retry
            // once all older reservations drain; no timer spins while occupied.
            self.ingressLock.lock()
            let detail = self.recovery.takeRequest(now: ProcessInfo.processInfo.systemUptime, occupied: self.inFlight)
            self.ingressRequestVersion = self.recovery.requestVersion
            self.ingressLock.unlock()
            if let detail {
                self.recordDiagnostics { $0.recoveryRequests += 1 }
                self.onError(detail)
            } else { self.scheduleRecovery() }
        }
        recoveryWork = work
        queue.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func recordDiagnostics(_ update: (inout DecoderDiagnostics) -> Void) {
        if diagnosticsEnabled { update(&healthDiagnostics) }
        if benchmarkEnabled { update(&benchmarkDiagnostics) }
    }

    private func createSession(_ format: MirrorFormat) throws {
        let description = try Self.formatDescription(parameters: format.parameters)
        let dimensions = CMVideoFormatDescriptionGetDimensions(description)
        guard dimensions.width == format.geometry.portraitWidth, dimensions.height == format.geometry.portraitHeight else {
            throw MirrorError.invalid("HEVC dimensions disagree with the mirror geometry.")
        }
        let attributes: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: MirrorPixelBuffer.pixelFormat,
                                       kCVPixelBufferIOSurfacePropertiesKey as String: [:]]
        let specification = [kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder as String: true]
        let result = VTDecompressionSessionCreate(allocator: kCFAllocatorDefault, formatDescription: description,
            decoderSpecification: specification as CFDictionary, imageBufferAttributes: attributes as CFDictionary,
            outputCallback: nil, decompressionSessionOut: &session)
        guard result == noErr else { throw MirrorError.invalid("Hardware HEVC decoder creation failed (\(result)).") }
        self.description = description
        if let session { VTSessionSetProperty(session, key: kVTDecompressionPropertyKey_RealTime, value: kCFBooleanTrue) }
    }

    static func formatDescription(parameters: [Data]) throws -> CMVideoFormatDescription {
        let declared = try sourceFormatDescription(parameters: parameters)
        try MirrorColor.validate(MirrorColor.metadata(declared), context: "HEVC parameter sets")
        let canonical = try sourceFormatDescription(parameters: parameters, extensions: MirrorColor.extensions as CFDictionary)
        try MirrorColor.validate(MirrorColor.metadata(canonical), context: "HEVC format", requireComplete: true)
        return canonical
    }

    static func declaredColor(parameters: [Data]) throws -> [String: String] {
        MirrorColor.metadata(try sourceFormatDescription(parameters: parameters))
    }

    static func declaredFullRange(parameters: [Data]) throws -> Bool? {
        CMFormatDescriptionGetExtension(try sourceFormatDescription(parameters: parameters),
            extensionKey: kCMFormatDescriptionExtension_FullRangeVideo) as? Bool
    }

    // Queried only by the headless fixture command, after its frames complete.
    // Missing optional properties retain their status instead of implying support.
    func fixtureOutputProperties() -> [String: Any] {
        queue.sync {
            guard let session else { return ["sessionAvailable": false] }
            var report: [String: Any] = ["sessionAvailable": true]
            for (name, key) in [
                ("poolIsShared", kVTDecompressionPropertyKey_PixelBufferPoolIsShared),
                ("usingHardwareDecoder", kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder),
                ("formatsByPerformance", kVTDecompressionPropertyKey_SupportedPixelFormatsOrderedByPerformance),
                ("formatsByQuality", kVTDecompressionPropertyKey_SupportedPixelFormatsOrderedByQuality),
            ] {
                var copied: Unmanaged<CFTypeRef>?
                let status = VTSessionCopyProperty(session, key: key, allocator: kCFAllocatorDefault, valueOut: &copied)
                let value = copied?.takeRetainedValue()
                var entry: [String: Any] = ["status": status, "value": value as Any? ?? NSNull()]
                if status == noErr, let formats = value as? [NSNumber] {
                    entry["value"] = formats.map { MirrorPixelBuffer.name($0.uint32Value) }
                }
                report[name] = entry
            }
            return report
        }
    }

    private static func sourceFormatDescription(parameters: [Data], extensions: CFDictionary? = nil) throws -> CMVideoFormatDescription {
        let allocations = parameters.map { data -> UnsafeMutablePointer<UInt8> in
            let pointer = UnsafeMutablePointer<UInt8>.allocate(capacity: data.count)
            data.copyBytes(to: pointer, count: data.count)
            return pointer
        }
        defer { allocations.forEach { $0.deallocate() } }
        var pointers = allocations.map { UnsafePointer($0) }
        var sizes = parameters.map(\.count)
        var description: CMVideoFormatDescription?
        let status = CMVideoFormatDescriptionCreateFromHEVCParameterSets(allocator: kCFAllocatorDefault,
            parameterSetCount: pointers.count, parameterSetPointers: &pointers, parameterSetSizes: &sizes,
            nalUnitHeaderLength: 4, extensions: extensions, formatDescriptionOut: &description)
        guard status == noErr, let description else { throw MirrorError.invalid("HEVC format creation failed (\(status)).") }
        return description
    }

    private func decode(_ video: MirrorVideo) {
        guard let format, video.generation == format.geometry.generation else { finished(); return }
        if awaitingKeyframe && !video.keyframe { finished(); return }
        if session == nil {
            do { try createSession(format) }
            catch { finished(); failed(error.localizedDescription); return }
        }
        guard let session, let description else { finished(); return }
        let currentToken = token
        do {
            let sample = try compressedSample(video, description: description)
            let submittedAt = ProcessInfo.processInfo.systemUptime * 1000
            let status = VTDecompressionSessionDecodeFrame(session, sampleBuffer: sample,
                flags: [._EnableAsynchronousDecompression], infoFlagsOut: nil) { [weak self] status, flags, image, _, _ in
                guard let self else { return }
                // Retain before leaving the VT callback; its buffer must never be modified.
                let frame = image.map { DecodedFrame($0, geometry: format.geometry, pts: video.pts, barcode: nil,
                    videoReceivedAt: video.receivedAt, decodeSubmittedAt: submittedAt) }
                self.queue.async {
                    defer { self.finished() }
                    guard self.token == currentToken else { return }
                    guard status == noErr, !flags.contains(.frameDropped), let frame else {
                        self.failed("HEVC decode failed (\(status)); requesting a fresh keyframe."); return
                    }
                    do { try MirrorPixelBuffer.validate(frame.pixelBuffer, geometry: format.geometry) }
                    catch { self.failed(error.localizedDescription); return }
                    if self.lastPTS == nil {
                        do { try MirrorColor.validate(MirrorColor.metadata(frame.pixelBuffer), context: "Decoded image", requireComplete: true) }
                        catch { self.failed(error.localizedDescription); return }
                    }
                    guard self.lastPTS == nil || video.pts > self.lastPTS! else { return }
                    self.lastPTS = video.pts
                    var observation: BarcodeObservation?
                    if self.benchmarkEnabled {
                        let started = ProcessInfo.processInfo.systemUptime
                        observation = self.scanner.read(frame.pixelBuffer, geometry: format.geometry)
                        let durationMs = (ProcessInfo.processInfo.systemUptime - started) * 1000
                        let sequence = observation?.sequence
                        let duplicate = sequence != nil && sequence == self.lastBarcodeSequence
                        if let sequence { self.lastBarcodeSequence = sequence }
                        self.recordDiagnostics { $0.scanned(sequence: sequence, duplicate: duplicate, durationMs: durationMs) }
                    }
                    self.onFrame(DecodedFrame(frame.pixelBuffer, geometry: format.geometry, pts: video.pts,
                        barcode: observation, decodedAt: frame.decodedAt,
                        videoReceivedAt: frame.videoReceivedAt, decodeSubmittedAt: frame.decodeSubmittedAt))
                }
            }
            if status != noErr { finished(); failed("HEVC submission failed (\(status)).") }
            else if video.keyframe { awaitingKeyframe = false; resetRecovery(acceptedKeyframe: true) }
        } catch { finished(); failed(error.localizedDescription) }
    }

    private func compressedSample(_ video: MirrorVideo, description: CMVideoFormatDescription) throws -> CMSampleBuffer {
        var block: CMBlockBuffer?
        var status = CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil,
            blockLength: video.bytes.count, blockAllocator: kCFAllocatorDefault, customBlockSource: nil,
            offsetToData: 0, dataLength: video.bytes.count, flags: 0, blockBufferOut: &block)
        guard status == noErr, let block else { throw MirrorError.invalid("Could not allocate HEVC sample (\(status)).") }
        status = video.bytes.withUnsafeBytes { bytes in
            CMBlockBufferReplaceDataBytes(with: bytes.baseAddress!, blockBuffer: block, offsetIntoDestination: 0,
                                          dataLength: bytes.count)
        }
        guard status == noErr else { throw MirrorError.invalid("Could not copy HEVC sample (\(status)).") }
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: CMTime(value: Int64(clamping: video.pts), timescale: 1_000_000_000), decodeTimeStamp: .invalid)
        var size = video.bytes.count
        var sample: CMSampleBuffer?
        status = CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block,
            formatDescription: description, sampleCount: 1, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
            sampleSizeEntryCount: 1, sampleSizeArray: &size, sampleBufferOut: &sample)
        guard status == noErr, let sample else { throw MirrorError.invalid("Could not create HEVC sample (\(status)).") }
        return sample
    }
}

// Exactly one MainActor delivery is scheduled. Display may coalesce decoded output,
// but never encoded dependencies. Observations are bounded independently of pixels.
final class FrameMailbox: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: DecodedFrame?
    private var observations: [DecodedObservation] = []
    private var scheduled = false
    private var decoded: UInt64 = 0
    let deliver: @MainActor @Sendable (DecodedFrame, [DecodedObservation], UInt64) -> Void
    init(deliver: @escaping @MainActor @Sendable (DecodedFrame, [DecodedObservation], UInt64) -> Void) { self.deliver = deliver }
    func reset() { lock.lock(); latest = nil; observations.removeAll(); decoded = 0; lock.unlock() }
    func put(_ frame: DecodedFrame) {
        lock.lock()
        latest = frame; decoded += 1
        if observations.count < 256, let observation = DecodedObservation(frame) { observations.append(observation) }
        guard !scheduled else { lock.unlock(); return }
        scheduled = true
        lock.unlock()
        Task { @MainActor in self.take() }
    }
    @MainActor private func take() {
        lock.lock()
        let frame = latest, observations = observations, decoded = decoded
        latest = nil; self.observations.removeAll(keepingCapacity: true); scheduled = false
        lock.unlock()
        if let frame { deliver(frame, observations, decoded) }
    }
}
