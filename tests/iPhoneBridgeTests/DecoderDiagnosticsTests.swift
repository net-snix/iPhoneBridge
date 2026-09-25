import XCTest
@testable import iPhoneBridge

final class DecoderDiagnosticsTests: XCTestCase {
    func testVideoReceiptTimestampIsLocalMetadataAndDoesNotChangeWirePTS() throws {
        let video = try MirrorVideo(payload: .words([1, 2, 3, 1, 3]) + Data([38, 1, 0x80]), receivedAt: 123.5)
        XCTAssertEqual(video.pts, UInt64(2) << 32 | 3)
        XCTAssertEqual(video.receivedAt, 123.5)
        XCTAssertEqual(video.bytes, .words([3]) + Data([38, 1, 0x80]))
    }

    func testBarcodeFailuresAreSeparateFromDuplicateFramesAndDurationsAggregate() {
        var counters = DecoderDiagnostics()
        counters.scanned(sequence: 1, duplicate: false, durationMs: 2)
        counters.scanned(sequence: nil, duplicate: false, durationMs: 10)
        counters.scanned(sequence: 1, duplicate: true, durationMs: 3)
        counters.scanned(sequence: 2, duplicate: false, durationMs: 1)
        XCTAssertEqual(counters.scannedFrames, 4)
        XCTAssertEqual(counters.barcodeMissing, 1)
        XCTAssertEqual(counters.barcodeDuplicates, 1)
        XCTAssertEqual(counters.report["scanMeanMs"] as? Double, 4)
        XCTAssertEqual(counters.scanMaxMs, 10)
        XCTAssertNoThrow(try JSONSerialization.data(withJSONObject: counters.report))
        XCTAssertTrue(DecoderDiagnostics().report["scanMeanMs"] is NSNull)
    }
}
