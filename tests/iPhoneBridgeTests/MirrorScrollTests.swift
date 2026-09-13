import XCTest
@testable import iPhoneBridge

final class MirrorScrollTests: XCTestCase {
    func testCancellationRequiresANewTrackpadContactAndRejectsOldMomentum() {
        var session = MirrorScrollSession()
        XCTAssertTrue(session.accepts(phase: .began, momentum: []))
        XCTAssertTrue(session.accepts(phase: .changed, momentum: []))
        session.cancel() // Focus/geometry change or a real mouse press.
        XCTAssertFalse(session.accepts(phase: .changed, momentum: []))
        XCTAssertFalse(session.accepts(phase: [], momentum: .began))
        XCTAssertTrue(session.accepts(phase: .began, momentum: []))
        XCTAssertTrue(session.accepts(phase: .ended, momentum: []))
        XCTAssertTrue(session.accepts(phase: [], momentum: .began))
        XCTAssertTrue(session.accepts(phase: [], momentum: .ended))
        XCTAssertFalse(session.accepts(phase: [], momentum: .changed))
        XCTAssertTrue(session.accepts(phase: [], momentum: [])) // Mouse wheel remains usable.
    }

    func testPreciseDeltasScaleToPhonePixelsAndWheelTicksUseLineDistance() throws {
        let geometry = try MirrorGeometry(width: 1000, height: 2000, turns: 0, generation: 1)
        let precise = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: -10, precise: true, pixelsPerPoint: 3))
        let wheel = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: 1, precise: false, pixelsPerPoint: 3))
        XCTAssertEqual(precise.endY, 970)
        XCTAssertEqual(wheel.endY, 1048)
        XCTAssertNil(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: .nan, precise: true, pixelsPerPoint: 3))
    }

    func testBurstIsBoundedAndOppositeDeltasCancelWithoutATap() throws {
        let geometry = try MirrorGeometry(width: 1000, height: 2000, turns: 0, generation: 1)
        let step = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: 1, precise: false, pixelsPerPoint: 1))
        var merged = step
        for _ in 0..<1000 { merged = merged.merged(with: step) }
        XCTAssertEqual(merged.distance, 480)
        let reverse = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: -1, precise: false, pixelsPerPoint: 1))
        XCTAssertFalse(step.merged(with: reverse).moves)
        let tiny = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: 2, precise: true, pixelsPerPoint: 1))
        XCTAssertFalse(tiny.moves)
        var accumulated = tiny
        for _ in 0..<15 { accumulated = accumulated.merged(with: tiny) }
        XCTAssertTrue(accumulated.moves)
    }

    func testAllOrientationsKeepPointsInsideDisplayedGeometry() throws {
        for turns in UInt32(0)...3 {
            let geometry = try MirrorGeometry(width: 100, height: 200, turns: turns, generation: 1)
            let gesture = try XCTUnwrap(MirrorScroll(geometry: geometry, x: geometry.width - 1, y: geometry.height - 2,
                deltaY: 1000, precise: false, pixelsPerPoint: 1))
            for step in 0...4 {
                let point = gesture.point(step: step, of: 4)
                XCTAssertLessThan(point.0, geometry.width); XCTAssertLessThan(point.1, geometry.height)
            }
            XCTAssertEqual(gesture.endY, geometry.height - 1)
        }
    }
}
