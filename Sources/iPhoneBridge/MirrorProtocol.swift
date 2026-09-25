import Foundation

enum MirrorError: LocalizedError, Equatable {
    case invalid(String)
    case remote(UInt32, String)
    case disconnected
    case timeout

    var errorDescription: String? {
        switch self {
        case .invalid(let detail): detail
        case .remote(_, let detail): detail
        case .disconnected: "The iPhone connection closed."
        case .timeout: "The iPhone did not respond in time."
        }
    }
}

enum MessageType: UInt8 {
    case hello = 1, format, video, still, geometry, ack, error, pong, stats
    case subscribe = 16, requestKeyframe, requestStill, getGeometry, acquireInput, releaseInput, pointer, key, button, ping, getStats
}

struct MirrorMessage: Sendable, Equatable {
    let type: MessageType
    let requestID: UInt32
    let payload: Data

    func encoded() -> Data {
        var result = Data.words([UInt32(payload.count + 8)])
        result.append(contentsOf: [type.rawValue, 0, 0, 0])
        result.append(.words([requestID]))
        result.append(payload)
        return result
    }
}

struct MessageParser {
    static let maximumBody = 16_777_216
    private var buffer = Data()

    mutating func append(_ bytes: Data) throws -> [MirrorMessage] {
        guard buffer.count + bytes.count <= Self.maximumBody + 4 + 65_536 else {
            throw MirrorError.invalid("The mirror receive buffer exceeded its limit.")
        }
        buffer.append(bytes)
        var messages: [MirrorMessage] = []
        var offset = 0
        while buffer.count - offset >= 4 {
            let length = Int(buffer.uint32(at: offset))
            guard length >= 8 && length <= Self.maximumBody else {
                throw MirrorError.invalid("Invalid mirror message length.")
            }
            guard buffer.count - offset >= length + 4 else { break }
            guard let type = MessageType(rawValue: buffer[offset + 4]),
                  buffer[offset + 5] == 0, buffer[offset + 6] == 0, buffer[offset + 7] == 0 else {
                throw MirrorError.invalid("Unsupported mirror message header.")
            }
            messages.append(MirrorMessage(type: type, requestID: buffer.uint32(at: offset + 8),
                                          payload: buffer.subdata(in: offset + 12..<offset + 4 + length)))
            offset += length + 4
        }
        if offset > 0 { buffer = Data(buffer.dropFirst(offset)) }
        return messages
    }
}

struct MirrorGeometry: Sendable, Equatable {
    let portraitWidth: UInt32
    let portraitHeight: UInt32
    let quarterTurns: UInt32
    let generation: UInt32
    var width: UInt32 { quarterTurns % 2 == 0 ? portraitWidth : portraitHeight }
    var height: UInt32 { quarterTurns % 2 == 0 ? portraitHeight : portraitWidth }

    init(width: UInt32, height: UInt32, turns: UInt32, generation: UInt32) throws {
        guard width > 0, height > 0, width <= 8192, height <= 8192,
              UInt64(width) * UInt64(height) * 4 + 32 <= MessageParser.maximumBody,
              turns < 4, generation > 0 else { throw MirrorError.invalid("Invalid iPhone geometry.") }
        portraitWidth = width; portraitHeight = height; quarterTurns = turns; self.generation = generation
    }

    init(payload: Data) throws {
        guard payload.count == 16 else { throw MirrorError.invalid("Invalid geometry message.") }
        try self.init(width: payload.uint32(at: 0), height: payload.uint32(at: 4),
                      turns: payload.uint32(at: 8), generation: payload.uint32(at: 12))
    }
}

struct MirrorFormat: Sendable {
    let geometry: MirrorGeometry
    let parameters: [Data]

    init(payload: Data) throws {
        guard payload.count >= 24, payload.uint32(at: 4) == 0x68766331,
              payload.uint32(at: 20) == 3 else { throw MirrorError.invalid("Expected HEVC VPS, SPS and PPS.") }
        geometry = try MirrorGeometry(width: payload.uint32(at: 8), height: payload.uint32(at: 12),
                                      turns: payload.uint32(at: 16), generation: payload.uint32(at: 0))
        var offset = 24
        var sets: [Data] = []
        for _ in 0..<3 {
            guard offset + 4 <= payload.count else { throw MirrorError.invalid("Truncated HEVC parameter set.") }
            let length = Int(payload.uint32(at: offset)); offset += 4
            guard length >= 2, length <= 65_536, length <= payload.count - offset else {
                throw MirrorError.invalid("Invalid HEVC parameter set length.")
            }
            sets.append(payload.subdata(in: offset..<offset + length)); offset += length
        }
        guard offset == payload.count, zip(sets, [32, 33, 34]).allSatisfy({ Int(($0.0[0] >> 1) & 63) == $0.1 }) else {
            throw MirrorError.invalid("Invalid HEVC parameter set types.")
        }
        parameters = sets
    }
}

struct MirrorVideo: Sendable {
    let generation: UInt32
    let pts: UInt64
    let keyframe: Bool
    let bytes: Data
    let receivedAt: Double?

    init(payload: Data, receivedAt: Double? = nil) throws {
        guard payload.count >= 22, payload.uint32(at: 12) <= 1 else { throw MirrorError.invalid("Invalid HEVC frame.") }
        generation = payload.uint32(at: 0); pts = payload.uint64(at: 4)
        keyframe = payload.uint32(at: 12) == 1
        self.receivedAt = receivedAt
        bytes = payload.subdata(in: 16..<payload.count)
        var offset = 0, nalCount = 0
        while offset < bytes.count {
            nalCount += 1
            guard nalCount <= 1024 else { throw MirrorError.invalid("HEVC access unit exceeds 1024 NAL units.") }
            guard bytes.count - offset >= 4 else { throw MirrorError.invalid("Truncated HEVC NAL length.") }
            let length = Int(bytes.uint32(at: offset)); offset += 4
            guard length >= 2, length <= bytes.count - offset else { throw MirrorError.invalid("Truncated HEVC NAL unit.") }
            offset += length
        }
    }
}

extension Data {
    static func words(_ words: [UInt32]) -> Data {
        Data(words.flatMap { value in [UInt8(value >> 24), UInt8(truncatingIfNeeded: value >> 16),
                                      UInt8(truncatingIfNeeded: value >> 8), UInt8(truncatingIfNeeded: value)] })
    }
    func uint32(at offset: Int) -> UInt32 {
        (0..<4).reduce(0) { ($0 << 8) | UInt32(self[startIndex + offset + $1]) }
    }
    func uint64(at offset: Int) -> UInt64 {
        UInt64(uint32(at: offset)) << 32 | UInt64(uint32(at: offset + 4))
    }
}
