import XCTest
import CoreVideo
@testable import iPhoneBridge

final class BarcodeTests: XCTestCase {
    private func color(sequence: UInt32, cell: Int) -> Int {
        let markers = [0: 2, 1: 3, 2: 4, 3: 5, 36: 6, 37: 7, 38: 5, 39: 8]
        if let marker = markers[cell] { return marker }
        return cell >= 4 && cell < 36 ? Int((sequence >> (35 - cell)) & 1) : -1
    }

    func testBarcodeRoundTripsAllBitsAndMotionFlag() throws {
        for sequence: UInt32 in [0, 1, 0x12345678, 0x80000001, .max] {
            var scanner = BarcodeScanner()
            let observation = try XCTUnwrap(scanner.observe(width: 400, height: 60, now: 0) { x, y in
                y >= 12 && y < 36 && x >= 20 && x < 340 ? self.color(sequence: sequence, cell: (x - 20) / 8) : -1
            })
            XCTAssertEqual(observation.sequence, sequence)
            XCTAssertEqual(observation.pitch, 8)
        }
    }

    func testHeaderAndFooterRequiredAndSearchThrottled() {
        var scanner = BarcodeScanner()
        XCTAssertNil(scanner.observe(width: 400, height: 60, now: 0) { _, _ in 0 })
        var reads = 0
        XCTAssertNil(scanner.observe(width: 400, height: 60, now: 100) { _, _ in reads += 1; return 0 })
        XCTAssertEqual(reads, 0)
        XCTAssertNil(BarcodeScanner.decode(x: 0, pitch: 8, width: 320) { x in
            x / 8 == 39 ? 0 : self.color(sequence: 5, cell: x / 8)
        })
        XCTAssertEqual(BarcodeScanner.classify(230, 60, 55), 2)
        XCTAssertEqual(BarcodeScanner.classify(110, 110, 110), -1)
    }

    func testAcquiresHEVCHeaderWithUnknownAndMisclassifiedBoundaryPixels() throws {
        // Recorded final sRGB HEVC row: red[45,71), unknown[71,72),
        // green[72,99), black[99,100), blue[100,126), cyan[126,153).
        for gap in 1...2 {
            var scanner = BarcodeScanner()
            let sequence: UInt32 = 0x800013cd
            let observation = try XCTUnwrap(scanner.observe(width: 1170, height: 60, now: 0) { x, y in
                guard y >= 12 && y < 36, x >= 45 && x < 1125 else { return -1 }
                if (71..<(71 + gap)).contains(x) { return -1 }
                if (99..<(99 + gap)).contains(x) { return 0 }
                return self.color(sequence: sequence, cell: (x - 45) / 27)
            })
            XCTAssertEqual(observation.sequence, sequence)
            XCTAssertEqual(observation.x, 45)
            XCTAssertEqual(observation.pitch, 27)
            let next = scanner.observe(width: 1170, height: 60, now: 16) { x, _ in
                self.color(sequence: sequence + 1, cell: (x - 45) / 27)
            }
            XCTAssertEqual(next?.sequence, sequence + 1)
        }
    }

    func testHeaderDoesNotBridgeGapsLargerThanChromaBoundaryTolerance() {
        var scanner = BarcodeScanner()
        XCTAssertNil(scanner.observe(width: 1170, height: 60, now: 0) { x, y in
            guard y >= 12 && y < 36, x >= 45 && x < 1125 else { return -1 }
            if (71...73).contains(x) { return -1 }
            return self.color(sequence: 0x800013cd, cell: (x - 45) / 27)
        })
    }

    func testTolerantHeaderStillRejectsInvalidFooter() {
        var scanner = BarcodeScanner()
        XCTAssertNil(scanner.observe(width: 1170, height: 60, now: 0) { x, y in
            guard y >= 12 && y < 36, x >= 45 && x < 1125 else { return -1 }
            if x == 71 { return -1 }
            if x == 99 { return 0 }
            let cell = (x - 45) / 27
            return cell == 39 ? 0 : self.color(sequence: 0x800013cd, cell: cell)
        })
    }

    func testInputGuardsRejectAnimationOrUnexpectedChangesWithoutRetry() throws {
        XCTAssertFalse(try BenchmarkGate.response(nil, baseline: 1))
        XCTAssertFalse(try BenchmarkGate.response(1, baseline: 1))
        XCTAssertTrue(try BenchmarkGate.response(2, baseline: 1))
        XCTAssertThrowsError(try BenchmarkGate.response(3, baseline: 1))
        XCTAssertThrowsError(try BenchmarkGate.response(0x80000001, baseline: 1))
    }

    func testPercentileInterpolatesAndEmptyIsUnavailable() {
        XCTAssertNil(BenchmarkGate.percentile([], 0.5))
        XCTAssertEqual(BenchmarkGate.percentile([80, 20, 40, 60], 0.5), 50)
        XCTAssertEqual(BenchmarkGate.percentile([20, 40], 0.95), 39)
    }
}
