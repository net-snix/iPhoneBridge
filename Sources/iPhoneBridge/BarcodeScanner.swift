import Foundation
import CoreVideo
import Accelerate

struct BarcodeObservation: Sendable, Equatable {
    let sequence: UInt32
    let x: Int
    let y: Int
    let pitch: Double
    let width: Int
    let height: Int
}

struct BarcodeScanner {
    private static let colors = [(16, 16, 16), (240, 240, 240), (255, 48, 48), (48, 255, 48),
                                 (48, 48, 255), (48, 255, 255), (255, 255, 48), (255, 48, 255), (255, 144, 48)]
    private static let markers = [(0, 2), (1, 3), (2, 4), (3, 5), (36, 6), (37, 7), (38, 5), (39, 8)]
    private var location: BarcodeObservation?
    private var lastSearch = -Double.infinity
    private static let matrix: vImage_YpCbCrToARGBMatrix = {
        // Use the immutable Swift matrix instead of the legacy mutable C pointer.
        let forward = vImage_ARGBToYpCbCrMatrix.itu_R_709_2
        let red = 2 * (1 - forward.R_Yp), blue = 2 * (1 - forward.B_Yp)
        return vImage_YpCbCrToARGBMatrix(Yp: 1, Cr_R: red,
            Cr_G: -forward.R_Yp * red / forward.G_Yp,
            Cb_G: -forward.B_Yp * blue / forward.G_Yp, Cb_B: blue)
    }()

    static func videoRangeRGB(y: UInt8, cb: UInt8, cr: UInt8) -> (Int, Int, Int) {
        // CoreVideo 420v uses Y' 16...235 and Cb/Cr 16...240, neutral 128.
        // This yields nonlinear RGB code values; the sRGB transfer is not applied
        // again. Only barcode sample points are converted, never the displayed image.
        let luma = Double(Int(y) - 16) * (255.0 / 219.0)
        let blueDifference = Double(Int(cb) - 128) * (255.0 / 224.0)
        let redDifference = Double(Int(cr) - 128) * (255.0 / 224.0)
        func code(_ value: Double) -> Int { min(255, max(0, Int(value.rounded()))) }
        return (code(luma + Double(matrix.Cr_R) * redDifference),
                code(luma + Double(matrix.Cb_G) * blueDifference + Double(matrix.Cr_G) * redDifference),
                code(luma + Double(matrix.Cb_B) * blueDifference))
    }

    static func classify(_ red: Int, _ green: Int, _ blue: Int) -> Int {
        var best = -1, distance = 11026
        for (index, color) in colors.enumerated() {
            let score = (red - color.0) * (red - color.0) + (green - color.1) * (green - color.1) + (blue - color.2) * (blue - color.2)
            if score < distance { best = index; distance = score }
        }
        return best
    }

    static func decode(x: Int, pitch: Double, width: Int, color: (Int) -> Int) -> UInt32? {
        func sample(_ cell: Int) -> Int {
            let point = Int((Double(x) + (Double(cell) + 0.5) * pitch).rounded())
            return point >= 0 && point < width ? color(point) : -1
        }
        guard markers.allSatisfy({ sample($0.0) == $0.1 }) else { return nil }
        var sequence: UInt32 = 0
        for cell in 4..<36 {
            let bit = sample(cell)
            guard bit == 0 || bit == 1 else { return nil }
            sequence = (sequence << 1) | UInt32(bit)
        }
        return sequence
    }

    mutating func observe(width: Int, height: Int, now: Double, color: (Int, Int) -> Int) -> BarcodeObservation? {
        if let location, location.width == width, location.height == height,
           let sequence = Self.decode(x: location.x, pitch: location.pitch, width: width, color: { color($0, location.y) }) {
            return BarcodeObservation(sequence: sequence, x: location.x, y: location.y, pitch: location.pitch, width: width, height: height)
        }
        location = nil
        guard now - lastSearch >= 250 else { return nil }
        lastSearch = now
        for y in stride(from: 6, to: height, by: 12) {
            var runs: [(color: Int, start: Int, end: Int)] = []
            for x in 0..<width {
                let value = color(x, y)
                if runs.last?.color == value { runs[runs.count - 1].end = x + 1 }
                else { runs.append((value, x, x + 1)) }
            }
            guard runs.count >= 4 else { continue }
            for index in 0...(runs.count - 4) {
                guard runs[index].color == 2 else { continue }
                var header = [runs[index]], cursor = index + 1
                let maximumGap = min(2, max(1, (runs[index].end - runs[index].start) / 3))
                for expected in 3...5 {
                    let boundary = header.last!.end
                    // 4:2:0 chroma filtering can make a boundary pixel unknown
                    // or classify it as another colour. Skip only tiny edges;
                    // marker widths and all 40 cell centres remain mandatory.
                    while cursor < runs.count, runs[cursor].color != expected,
                          runs[cursor].end - boundary <= maximumGap { cursor += 1 }
                    guard cursor < runs.count, runs[cursor].color == expected,
                          runs[cursor].start - boundary <= maximumGap else { break }
                    header.append(runs[cursor]); cursor += 1
                }
                guard header.count == 4 else { continue }
                let pitch = Double(header[3].end - header[0].start) / 4
                guard pitch >= 3, header.allSatisfy({ abs(Double($0.end - $0.start) - pitch) <= pitch * 0.35 }),
                      let sequence = Self.decode(x: header[0].start, pitch: pitch, width: width, color: { color($0, y) }) else { continue }
                let observation = BarcodeObservation(sequence: sequence, x: header[0].start, y: y, pitch: pitch, width: width, height: height)
                location = observation
                return observation
            }
        }
        return nil
    }

    mutating func read(_ buffer: CVPixelBuffer, geometry: MirrorGeometry) -> BarcodeObservation? {
        guard (try? MirrorPixelBuffer.validate(buffer, geometry: geometry)) != nil,
              CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let luma = CVPixelBufferGetBaseAddressOfPlane(buffer, 0)?.assumingMemoryBound(to: UInt8.self),
              let chroma = CVPixelBufferGetBaseAddressOfPlane(buffer, 1)?.assumingMemoryBound(to: UInt8.self) else { return nil }
        let lumaStride = CVPixelBufferGetBytesPerRowOfPlane(buffer, 0), chromaStride = CVPixelBufferGetBytesPerRowOfPlane(buffer, 1)
        let sourceWidth = Int(geometry.portraitWidth), sourceHeight = Int(geometry.portraitHeight)
        return observe(width: Int(geometry.width), height: Int(geometry.height), now: ProcessInfo.processInfo.systemUptime * 1000) { x, y in
            let source: (Int, Int)
            switch geometry.quarterTurns {
            case 1: source = (y, sourceHeight - 1 - x)
            case 2: source = (sourceWidth - 1 - x, sourceHeight - 1 - y)
            case 3: source = (sourceWidth - 1 - y, x)
            default: source = (x, y)
            }
            let uvOffset = (source.1 / 2) * chromaStride + (source.0 / 2) * 2
            let rgb = Self.videoRangeRGB(y: luma[source.1 * lumaStride + source.0],
                                        cb: chroma[uvOffset], cr: chroma[uvOffset + 1])
            return Self.classify(rgb.0, rgb.1, rgb.2)
        }
    }
}
