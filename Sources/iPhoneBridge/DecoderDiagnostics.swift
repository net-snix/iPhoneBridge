import Foundation

// Scalar timing only: these bounded observations never retain decoded pixels.
// Phone PTS is an exact UInt64 nanosecond identity, not a Mac clock timestamp.
struct DecodedObservation: Sendable {
    let sequence: UInt32
    let phonePtsNs: UInt64
    let videoReceivedAt: Double?
    let decodeSubmittedAt: Double?
    let decodedAt: Double

    init?(_ frame: DecodedFrame) {
        guard let barcode = frame.barcode else { return nil }
        sequence = barcode.sequence; phonePtsNs = frame.pts
        videoReceivedAt = frame.videoReceivedAt; decodeSubmittedAt = frame.decodeSubmittedAt; decodedAt = frame.decodedAt
    }

    var report: [String: Any] {
        ["sequence": sequence, "phonePtsNs": phonePtsNs, "decodedAt": decodedAt,
         "videoReceivedAt": videoReceivedAt ?? NSNull() as Any,
         "decodeSubmittedAt": decodeSubmittedAt ?? NSNull() as Any]
    }
}

struct DecoderDiagnostics: Sendable {
    private(set) var scannedFrames: UInt64 = 0
    private(set) var barcodeMissing: UInt64 = 0
    private(set) var barcodeDuplicates: UInt64 = 0
    private(set) var scanTotalMs = 0.0
    private(set) var scanMaxMs = 0.0
    var ingressDrops: UInt64 = 0
    var keyframeIngressDrops: UInt64 = 0
    var recoveryRequests: UInt64 = 0

    mutating func scanned(sequence: UInt32?, duplicate: Bool, durationMs: Double) {
        scannedFrames += 1
        if sequence == nil { barcodeMissing += 1 }
        else if duplicate { barcodeDuplicates += 1 }
        scanTotalMs += durationMs; scanMaxMs = max(scanMaxMs, durationMs)
    }

    var report: [String: Any] {
        ["scannedFrames": scannedFrames, "barcodeMissing": barcodeMissing,
         "barcodeDuplicates": barcodeDuplicates,
         "scanMeanMs": scannedFrames > 0 ? scanTotalMs / Double(scannedFrames) : NSNull() as Any,
         "scanMaxMs": scannedFrames > 0 ? scanMaxMs : NSNull() as Any,
         "ingressDrops": ingressDrops, "keyframeIngressDrops": keyframeIngressDrops,
         "recoveryRequests": recoveryRequests]
    }
}
