import XCTest
@testable import iPhoneBridge

final class DecoderRecoveryTests: XCTestCase {
    private func bytes(_ hex: String) -> Data {
        Data(stride(from: 0, to: hex.count, by: 2).map { offset in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!
        })
    }

    func testNewGenerationIDRDroppedByOldIngressStillRequestsRecoveryOnce() throws {
        // VPS/SPS/PPS from the original 1170x2532 hardware probe. The dropped
        // synthetic IDR never reaches VT; this tests actual ingress scheduling.
        let parameters = ["40010c01ffff016000000300b00000030000030099170240",
            "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fe880", "4401c072f05324"].map(bytes)
        var payload = Data.words([2, 0x68766331, 1170, 2532, 0, 3])
        for parameter in parameters { payload.append(.words([UInt32(parameter.count)])); payload.append(parameter) }
        let format = try MirrorFormat(payload: payload)
        let old = try MirrorVideo(payload: .words([1, 0, 1, 1, 3]) + Data([38, 1, 0x80]))
        let fresh = try MirrorVideo(payload: .words([2, 0, 2, 1, 3]) + Data([38, 1, 0x80]))
        let queue = DispatchQueue(label: "decoder-recovery-test")
        let errors = DecoderErrors()
        let firstRequest = expectation(description: "First keyframe requested after old ingress drains")
        let renewedRequest = expectation(description: "A dropped requested IDR renews recovery")
        let decoder = HEVCDecoder(queue: queue, onFrame: { _ in }, onError: {
            errors.append($0)
            if errors.values.count == 1 { firstRequest.fulfill() }
            else if errors.values.count == 2 { renewedRequest.fulfill() }
        })
        queue.suspend()
        for _ in 0..<8 { decoder.submit(old) }
        decoder.configure(format)
        decoder.submit(fresh) // All eight reservations still belong to the old generation.
        decoder.submit(fresh) // Coalesce repeated loss while recovery is already requested.
        decoder.submit(old)   // A stale-generation overflow cannot invalidate the new one.
        queue.resume()
        wait(for: [firstRequest], timeout: 2)
        XCTAssertEqual(errors.values.filter { $0.contains("decoder fell behind") }.count, 1)

        // The requested IDR itself arrives while eight old reservations occupy
        // ingress. It must cause one renewal after drain, not latch until periodic IDR.
        queue.suspend()
        for _ in 0..<8 { decoder.submit(old) }
        decoder.submit(fresh); decoder.submit(fresh); decoder.submit(old)
        XCTAssertEqual(errors.values.count, 1)
        queue.resume()
        wait(for: [renewedRequest], timeout: 2)
        XCTAssertEqual(errors.values.count, 2)
        decoder.reset(); queue.sync {}
    }

    func testCapacityRequestVersionsAndCooldownBoundRenewals() {
        var recovery = DecoderRecovery()
        recovery.failed("overflow", rejectedRequestVersion: 0)
        XCTAssertNil(recovery.takeRequest(now: 1, occupied: 8))
        XCTAssertEqual(recovery.takeRequest(now: 1, occupied: 0), "overflow")
        recovery.failed("older queued IDR", rejectedRequestVersion: 0)
        XCTAssertNil(recovery.retryDelay(now: 2, occupied: 0))
        recovery.failed("requested IDR dropped", rejectedRequestVersion: 1)
        recovery.failed("same burst", rejectedRequestVersion: 1)
        XCTAssertNil(recovery.takeRequest(now: 2, occupied: 8))
        XCTAssertNil(recovery.takeRequest(now: 1.1, occupied: 0))
        XCTAssertEqual(recovery.retryDelay(now: 1.1, occupied: 0)!, 0.15, accuracy: 0.000001)
        XCTAssertEqual(recovery.takeRequest(now: 1.25, occupied: 0), "same burst")
        XCTAssertEqual(recovery.requestVersion, 2)
        XCTAssertNil(recovery.takeRequest(now: 3, occupied: 0))
    }

    func testAcceptedKeyframeAndNewGenerationDiscardPendingRecovery() {
        var recovery = DecoderRecovery()
        recovery.failed("first"); _ = recovery.takeRequest(now: 1, occupied: 0)
        recovery.failed("requested IDR dropped", rejectedRequestVersion: 1)
        recovery.acceptedKeyframe()
        XCTAssertNil(recovery.takeRequest(now: 2, occupied: 0))
        recovery.failed("new failure"); recovery.reset()
        XCTAssertNil(recovery.takeRequest(now: 2, occupied: 0))
        recovery.failed("new generation")
        XCTAssertEqual(recovery.takeRequest(now: 1.1, occupied: 0), "new generation")
        XCTAssertEqual(recovery.requestVersion, 2) // Never reuse ingress request identities.
    }
}

@MainActor
final class KeyframeRequesterTests: XCTestCase {
    func testRenewalWhileACKPendingIsCoalescedAndSentAfterACK() async {
        let sends = SuspendedRequests()
        var completions = 0
        let requester = KeyframeRequester(send: { await sends.send() }, completed: { _ in completions += 1 })
        requester.request()
        await sends.waitForSend(1)
        requester.request(); requester.request()
        XCTAssertEqual(sends.count, 1)
        sends.finish(0)
        await sends.waitForSend(2)
        XCTAssertEqual(completions, 1)
        sends.finish(1)
        requester.cancel()
    }

    func testCancelledACKCannotClearNewConnectionRequest() async {
        let sends = SuspendedRequests()
        var completions = 0
        let requester = KeyframeRequester(send: { await sends.send() }, completed: { _ in completions += 1 })
        requester.request(); await sends.waitForSend(1)
        requester.request(); requester.cancel()
        requester.request(); await sends.waitForSend(2)
        sends.finish(0)
        requester.request()
        sends.finish(1)
        await sends.waitForSend(3)
        XCTAssertEqual(completions, 1)
        sends.finish(2); requester.cancel()
    }
}

@MainActor
private final class SuspendedRequests {
    private var requests: [CheckedContinuation<Void, Never>?] = []
    private var waiter: (Int, CheckedContinuation<Void, Never>)?
    var count: Int { requests.count }
    func send() async {
        await withCheckedContinuation { continuation in
            requests.append(continuation)
            if let waiter, requests.count >= waiter.0 { self.waiter = nil; waiter.1.resume() }
        }
    }
    func waitForSend(_ count: Int) async {
        if requests.count >= count { return }
        await withCheckedContinuation { waiter = (count, $0) }
    }
    func finish(_ index: Int) { let request = requests[index]; requests[index] = nil; request?.resume() }
}

private final class DecoderErrors: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [String] = []
    var values: [String] { lock.withLock { stored } }
    func append(_ value: String) { lock.withLock { stored.append(value) } }
}
