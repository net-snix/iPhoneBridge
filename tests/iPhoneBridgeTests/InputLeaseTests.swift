import XCTest
import AppKit
@testable import iPhoneBridge

@MainActor
private final class CommandRecorder: MirrorCommanding {
    var messages: [(MessageType, Data)] = []
    var acquisition: CheckedContinuation<Void, Error>?
    var keyAcknowledgement: CheckedContinuation<Void, Error>?
    var pauseKeyUsage: UInt32?
    var pointerAcknowledgement: CheckedContinuation<Void, Error>?
    var pausePointerAction: UInt32?
    var pauseAcquire = false
    var busy = false
    var onCommand: ((MessageType) -> Void)?
    func command(_ type: MessageType, payload: Data) async throws {
        messages.append((type, payload)); onCommand?(type)
        if type == .acquireInput {
            if busy { throw MirrorError.remote(2, "Input is in use by another client.") }
            if pauseAcquire { try await withCheckedThrowingContinuation { acquisition = $0 } }
        }
        if type == .key, payload.uint32(at: 4) == pauseKeyUsage, payload.uint32(at: 8) == 1 {
            try await withCheckedThrowingContinuation { keyAcknowledgement = $0 }
        }
        if type == .pointer, payload.uint32(at: 4) == pausePointerAction {
            try await withCheckedThrowingContinuation { pointerAcknowledgement = $0 }
        }
    }
}

final class InputLeaseTests: XCTestCase {
    @MainActor func testGestureAcquiresOnceAndReleasesAfterUp() async throws {
        let recorder = CommandRecorder()
        let actual = MirrorInputDriver(connection: recorder)
        actual.enqueue(.pointer(1, 1, 5, 6))
        actual.enqueue(.pointer(1, 2, 7, 8))
        actual.enqueue(.pointer(1, 2, 9, 10))
        actual.enqueue(.pointer(1, 0, 11, 12))
        for _ in 0..<20 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .pointer, .pointer, .releaseInput])
        XCTAssertEqual(recorder.messages[2].1, .words([1, 2, 9, 10]))
    }

    @MainActor func testFocusCancellationDuringAcquireSendsNoTouch() async throws {
        let recorder = CommandRecorder(); recorder.pauseAcquire = true
        let driver = MirrorInputDriver(connection: recorder)
        driver.enqueue(.pointer(1, 1, 5, 6))
        for _ in 0..<20 where recorder.acquisition == nil { await Task.yield() }
        driver.cancel()
        recorder.acquisition?.resume(); recorder.acquisition = nil
        for _ in 0..<20 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .releaseInput])
    }

    @MainActor func testBusyLeaseNeverSendsOrRetriesInput() async {
        let recorder = CommandRecorder(); recorder.busy = true
        let driver = MirrorInputDriver(connection: recorder)
        var error: String?
        driver.onError = { error = $0 }
        driver.enqueue(.stroke(1, HIDStroke(usage: 4, shift: true), 0))
        driver.enqueue(.button(1, 1))
        for _ in 0..<20 where error == nil { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput])
        XCTAssertEqual(error, "Input is in use by another client.")
    }

    @MainActor func testUppercaseStrokeReleasesShiftAndLease() async {
        let recorder = CommandRecorder()
        let actual = MirrorInputDriver(connection: recorder)
        actual.enqueue(.stroke(3, HIDStroke(usage: 4, shift: true), 0))
        for _ in 0..<20 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .key, .key, .key, .key, .releaseInput])
        XCTAssertEqual(recorder.messages.filter { $0.0 == .key }.map(\.1),
                       [.words([3, 225, 1]), .words([3, 4, 1]), .words([3, 4, 0]), .words([3, 225, 0])])
    }

    @MainActor func testCancellationDuringModifierAckNeverSendsCharacter() async {
        let recorder = CommandRecorder(); recorder.pauseKeyUsage = 225
        let driver = MirrorInputDriver(connection: recorder)
        driver.enqueue(.stroke(1, HIDStroke(usage: 4, shift: true), 0))
        for _ in 0..<20 where recorder.keyAcknowledgement == nil { await Task.yield() }
        XCTAssertNotNil(recorder.keyAcknowledgement)
        driver.cancel()
        recorder.keyAcknowledgement?.resume(); recorder.keyAcknowledgement = nil
        await driver.cancelAndWait()
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .key, .releaseInput])
        XCTAssertEqual(recorder.messages[1].1, .words([1, 225, 1]))
    }

    @MainActor func testCancellationDuringCharacterAckReleasesHeldKeysWithoutContinuing() async {
        let recorder = CommandRecorder(); recorder.pauseKeyUsage = 4
        let driver = MirrorInputDriver(connection: recorder)
        driver.enqueue(.stroke(1, HIDStroke(usage: 4, shift: true), 0))
        for _ in 0..<20 where recorder.keyAcknowledgement == nil { await Task.yield() }
        XCTAssertNotNil(recorder.keyAcknowledgement)
        driver.cancel()
        recorder.keyAcknowledgement?.resume(); recorder.keyAcknowledgement = nil
        await driver.cancelAndWait()
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .key, .key, .releaseInput])
    }

    @MainActor func testUncertainAcquisitionReleasesPossibleServerLease() async {
        let recorder = CommandRecorder(); recorder.pauseAcquire = true
        let driver = MirrorInputDriver(connection: recorder)
        driver.enqueue(.pointer(1, 1, 5, 6))
        for _ in 0..<20 where recorder.acquisition == nil { await Task.yield() }
        XCTAssertNotNil(recorder.acquisition)
        recorder.acquisition?.resume(throwing: MirrorError.timeout); recorder.acquisition = nil
        await driver.cancelAndWait()
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .releaseInput])
    }

    @MainActor func testTypingDuringHeldDragPreservesTouchLeaseUntilMouseUp() async {
        let recorder = CommandRecorder()
        let driver = MirrorInputDriver(connection: recorder)
        driver.enqueue(.pointer(1, 1, 5, 6))
        for _ in 0..<20 { await Task.yield() }
        driver.enqueue(.stroke(1, HIDStroke(usage: 4, shift: false), 0))
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .key, .key])
        driver.enqueue(.pointer(1, 2, 7, 8))
        driver.enqueue(.pointer(1, 0, 7, 8))
        for _ in 0..<20 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .key, .key, .pointer, .pointer, .releaseInput])
    }

    @MainActor func testNavigationEndsDragAndIgnoresItsRemainingMouseEvents() async throws {
        let recorder = CommandRecorder()
        let driver = MirrorInputDriver(connection: recorder)
        let view = MirrorView(frame: NSRect(x: 0, y: 0, width: 100, height: 200))
        view.input = driver
        view.geometry = try MirrorGeometry(width: 100, height: 200, turns: 0, generation: 1)
        func event(_ type: NSEvent.EventType) throws -> NSEvent {
            try XCTUnwrap(NSEvent.mouseEvent(with: type, location: NSPoint(x: 50, y: 100), modifierFlags: [],
                timestamp: 0, windowNumber: 0, context: nil, eventNumber: 1, clickCount: 1, pressure: 1))
        }
        view.mouseDown(with: try event(.leftMouseDown))
        for _ in 0..<20 { await Task.yield() }
        view.navigate(button: 1)
        view.mouseDragged(with: try event(.leftMouseDragged))
        view.mouseUp(with: try event(.leftMouseUp))
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .releaseInput, .acquireInput, .button, .releaseInput])
        XCTAssertEqual(recorder.messages.filter { $0.0 == .pointer }.map(\.1), [.words([1, 1, 50, 100])])
    }

    @MainActor func testWheelBurstCoalescesToOneBoundedTouchGesture() async throws {
        let recorder = CommandRecorder()
        let driver = MirrorInputDriver(connection: recorder, pause: { _ in })
        let geometry = try MirrorGeometry(width: 1000, height: 2000, turns: 0, generation: 1)
        let step = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 500, y: 1000, deltaY: 1, precise: false, pixelsPerPoint: 1))
        for _ in 0..<1000 { driver.enqueue(.scroll(step)) }
        for _ in 0..<30 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput] + Array(repeating: .pointer, count: 6) + [.releaseInput])
        XCTAssertEqual(recorder.messages.filter { $0.0 == .pointer }.map { $0.1.uint32(at: 4) }, [1, 2, 2, 2, 2, 0])
        XCTAssertEqual(recorder.messages[6].1, .words([1, 0, 500, 1480]))
    }

    @MainActor func testMousePressCancelsWheelBeforeStartingRealDrag() async throws {
        let recorder = CommandRecorder(); recorder.pausePointerAction = 1
        let driver = MirrorInputDriver(connection: recorder, pause: { _ in })
        let geometry = try MirrorGeometry(width: 100, height: 200, turns: 0, generation: 1)
        let gesture = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 50, y: 100, deltaY: 1, precise: false, pixelsPerPoint: 1))
        driver.enqueue(.scroll(gesture))
        for _ in 0..<30 where recorder.pointerAcknowledgement == nil { await Task.yield() }
        XCTAssertNotNil(recorder.pointerAcknowledgement)
        driver.enqueue(.pointer(1, 1, 10, 20)); driver.enqueue(.pointer(1, 0, 10, 20))
        recorder.pausePointerAction = nil
        recorder.pointerAcknowledgement?.resume(); recorder.pointerAcknowledgement = nil
        for _ in 0..<30 where recorder.messages.count < 7 { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .releaseInput, .acquireInput, .pointer, .pointer, .releaseInput])
        XCTAssertEqual(recorder.messages.filter { $0.0 == .pointer }.map { $0.1.uint32(at: 4) }, [1, 1, 0])
    }

    @MainActor func testWheelCannotInterfereWithHeldMouseDrag() async throws {
        let recorder = CommandRecorder()
        let driver = MirrorInputDriver(connection: recorder, pause: { _ in })
        let geometry = try MirrorGeometry(width: 100, height: 200, turns: 0, generation: 1)
        let gesture = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 50, y: 100, deltaY: 1, precise: false, pixelsPerPoint: 1))
        driver.enqueue(.pointer(1, 1, 10, 20))
        driver.enqueue(.scroll(gesture)) // A queued mouse press owns the next touch too.
        for _ in 0..<30 { await Task.yield() }
        driver.enqueue(.scroll(gesture))
        driver.enqueue(.pointer(1, 0, 10, 20))
        for _ in 0..<30 where recorder.messages.last?.0 != .releaseInput { await Task.yield() }
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .pointer, .pointer, .releaseInput])
    }

    @MainActor func testGeometryChangeCancelsWheelDuringAcquisition() async throws {
        let recorder = CommandRecorder(); recorder.pauseAcquire = true
        let driver = MirrorInputDriver(connection: recorder, pause: { _ in })
        let view = MirrorView(frame: .zero); view.input = driver
        let geometry = try MirrorGeometry(width: 100, height: 200, turns: 0, generation: 1)
        view.geometry = geometry
        let gesture = try XCTUnwrap(MirrorScroll(geometry: geometry, x: 50, y: 100, deltaY: 1, precise: false, pixelsPerPoint: 1))
        driver.enqueue(.scroll(gesture))
        for _ in 0..<30 where recorder.acquisition == nil { await Task.yield() }
        XCTAssertNotNil(recorder.acquisition)
        view.geometry = try MirrorGeometry(width: 100, height: 200, turns: 1, generation: 2)
        recorder.acquisition?.resume(); recorder.acquisition = nil
        await driver.cancelAndWait()
        XCTAssertEqual(recorder.messages.map(\.0), [.acquireInput, .releaseInput])
    }
}
