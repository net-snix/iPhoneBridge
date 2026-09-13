import XCTest
@testable import iPhoneBridge

final class MirrorProtocolTests: XCTestCase {
    private func data(_ hex: String) -> Data {
        Data(stride(from: 0, to: hex.count, by: 2).map { offset in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!
        })
    }

    func testSharedGoldenVectorsAtEveryFragmentBoundary() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let bytes = try Data(contentsOf: root.appendingPathComponent("docs/mirror-protocol-vectors.json"))
        let vectors = try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [[String: Any]])
        for vector in vectors {
            let wire = data(vector["frame_hex"] as! String)
            let expected = MirrorMessage(type: MessageType(rawValue: UInt8(vector["type"] as! Int))!,
                requestID: UInt32(vector["request_id"] as! Int), payload: data(vector["payload_hex"] as! String))
            XCTAssertEqual(expected.encoded(), wire)
            for split in 0...wire.count {
                var parser = MessageParser()
                let first = try parser.append(Data(wire.prefix(split)))
                let second = try parser.append(Data(wire.dropFirst(split)))
                XCTAssertEqual(first + second, [expected], vector["name"] as! String)
            }
        }
    }

    func testMultipleMessagesAndOneByteFragments() throws {
        let messages = (1...8).map { MirrorMessage(type: .ack, requestID: UInt32($0), payload: Data()) }
        let wire = messages.reduce(into: Data()) { $0.append($1.encoded()) }
        var parser = MessageParser(), results: [MirrorMessage] = []
        for byte in wire { results += try parser.append(Data([byte])) }
        XCTAssertEqual(results, messages)
        var batched = MessageParser()
        XCTAssertEqual(try batched.append(wire), messages)
    }

    func testRejectsLengthBeforeReceivingBody() {
        for invalid: UInt32 in [0, 7, 16_777_217, .max] {
            var parser = MessageParser()
            XCTAssertThrowsError(try parser.append(.words([invalid])))
        }
    }

    func testRejectsFlagsReservedAndUnknownType() {
        for offset in [4, 5, 6, 7] {
            var bytes = MirrorMessage(type: .ack, requestID: 1, payload: Data()).encoded()
            bytes[offset] = 255
            var parser = MessageParser()
            XCTAssertThrowsError(try parser.append(bytes))
        }
    }

    func testGeometryRejectsInvalidAndExcessiveStillAllocation() throws {
        XCTAssertThrowsError(try MirrorGeometry(width: 0, height: 2532, turns: 0, generation: 1))
        XCTAssertThrowsError(try MirrorGeometry(width: 8192, height: 8192, turns: 0, generation: 1))
        XCTAssertThrowsError(try MirrorGeometry(width: 1170, height: 2532, turns: 4, generation: 1))
        XCTAssertThrowsError(try MirrorGeometry(width: 1170, height: 2532, turns: 0, generation: 0))
        let portrait = try MirrorGeometry(width: 1170, height: 2532, turns: 0, generation: 1)
        let landscape = try MirrorGeometry(width: 1170, height: 2532, turns: 1, generation: 2)
        XCTAssertEqual(portrait.width, 1170); XCTAssertEqual(landscape.width, 2532)
        XCTAssertEqual(portrait.height, 2532); XCTAssertEqual(landscape.height, 1170)
    }

    func testVideoRejectsPartialNALAndInvalidKeyframeFlag() throws {
        let header = Data.words([1, 0, 12, 1])
        let valid = try MirrorVideo(payload: header + .words([3]) + Data([38, 1, 0x80]))
        XCTAssertEqual(valid.pts, 12); XCTAssertTrue(valid.keyframe)
        for tail in [Data([0]), Data.words([500]) + Data([38, 1]), Data.words([0])] {
            XCTAssertThrowsError(try MirrorVideo(payload: header + tail))
        }
        XCTAssertThrowsError(try MirrorVideo(payload: .words([1, 0, 12, 2, 2]) + Data([38, 1])))
    }

    func testVideoBoundsNALCountWithoutChangingByteOrMinimumLengthLimits() {
        var payload = Data.words([1, 0, 12, 1])
        let nal = Data.words([2]) + Data([38, 1])
        for _ in 0..<1024 { payload.append(nal) }
        XCTAssertNoThrow(try MirrorVideo(payload: payload))
        payload.append(nal)
        XCTAssertThrowsError(try MirrorVideo(payload: payload))
    }

    func testFormatRejectsWrongParameterOrderingAndTrailingData() {
        var payload = Data.words([1, 0x68766331, 1170, 2532, 0, 3])
        for byte: UInt8 in [64, 66, 68] { payload.append(.words([2])); payload.append(contentsOf: [byte, 1]) }
        XCTAssertNoThrow(try MirrorFormat(payload: payload))
        XCTAssertThrowsError(try MirrorFormat(payload: payload + Data([0])))
        payload[28] = 68
        XCTAssertThrowsError(try MirrorFormat(payload: payload))
    }

    func testAnnexBRecognizesThreeAndFourByteStartCodes() throws {
        XCTAssertEqual(try HEVCFixture.units(Data([0, 0, 0, 1, 64, 1, 0, 0, 1, 66, 1])),
                       [Data([64, 1]), Data([66, 1])])
        XCTAssertThrowsError(try HEVCFixture.units(Data([1, 2, 3])))
        XCTAssertThrowsError(try HEVCFixture.units(Data([0, 0, 1, 64])))
    }
}
