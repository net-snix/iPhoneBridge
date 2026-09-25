import XCTest
import CoreVideo
@testable import iPhoneBridge

final class FrameMailboxTests: XCTestCase {
    private func frame(_ pts: UInt64, generation: UInt32 = 1, sequence: UInt32? = nil) throws -> DecodedFrame {
        var buffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 8, 8, kCVPixelFormatType_32BGRA, nil, &buffer), kCVReturnSuccess)
        return DecodedFrame(try XCTUnwrap(buffer),
            geometry: try MirrorGeometry(width: 8, height: 8, turns: 0, generation: generation), pts: pts,
            barcode: sequence.map { BarcodeObservation(sequence: $0, x: 1, y: 2, pitch: 3, width: 8, height: 8) },
            decodedAt: 30, videoReceivedAt: 10, decodeSubmittedAt: 20)
    }

    @MainActor func testPresentationCoalescesDecodedPixelsAndKeepsFullCount() async throws {
        var received: [(UInt64, UInt64)] = []
        let mailbox = FrameMailbox { frame, _, count in received.append((frame.pts, count)) }
        for pts in 1...100 { mailbox.put(try frame(UInt64(pts))) }
        for _ in 0..<20 where received.isEmpty { await Task.yield() }
        XCTAssertEqual(received.count, 1)
        XCTAssertEqual(received.first?.0, 100)
        XCTAssertEqual(received.first?.1, 100)
    }

    @MainActor func testResetRetiresPendingPresentation() async throws {
        var received: [UInt64] = []
        let mailbox = FrameMailbox { frame, _, _ in received.append(frame.pts) }
        mailbox.put(try frame(1))
        mailbox.reset()
        mailbox.put(try frame(2, generation: 2))
        for _ in 0..<20 where received.isEmpty { await Task.yield() }
        XCTAssertEqual(received, [2])
    }

    @MainActor func testCoalescedPresentationPreservesEachBarcodeFramesExactPTSAndTiming() async throws {
        let firstPTS: UInt64 = 9_007_199_254_740_993 // Beyond exact IEEE754 integer range.
        var observations: [DecodedObservation] = []
        var displayedPTS: UInt64?
        let mailbox = FrameMailbox { frame, received, _ in
            displayedPTS = frame.pts; observations = received
        }
        mailbox.put(try frame(firstPTS, sequence: 99))
        mailbox.put(try frame(firstPTS + 1, sequence: 99))
        for _ in 0..<20 where observations.isEmpty { await Task.yield() }
        XCTAssertEqual(displayedPTS, firstPTS + 1)
        XCTAssertEqual(observations.map(\.phonePtsNs), [firstPTS, firstPTS + 1])
        let first = try XCTUnwrap(observations.first)
        XCTAssertEqual(first.videoReceivedAt, 10); XCTAssertEqual(first.decodeSubmittedAt, 20); XCTAssertEqual(first.decodedAt, 30)
        let data = try JSONSerialization.data(withJSONObject: first.report)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual((object["phonePtsNs"] as? NSNumber)?.uint64Value, firstPTS)
    }

    @MainActor func testTimingObservationsKeepExistingBoundWhileLatestFrameAdvances() async throws {
        var received: [DecodedObservation] = [], latest: UInt64 = 0, count: UInt64 = 0
        let mailbox = FrameMailbox { frame, observations, decoded in
            latest = frame.pts; received = observations; count = decoded
        }
        for pts in 1...300 { mailbox.put(try frame(UInt64(pts), sequence: UInt32(pts))) }
        for _ in 0..<20 where received.isEmpty { await Task.yield() }
        XCTAssertEqual(received.count, 256)
        XCTAssertEqual(received.last?.phonePtsNs, 256)
        XCTAssertEqual(latest, 300); XCTAssertEqual(count, 300)
    }
}
