import Foundation
import CoreMedia
import CoreImage
import ImageIO
import UniformTypeIdentifiers

enum HEVCFixture {
    static func units(_ data: Data) throws -> [Data] {
        var starts: [(Int, Int)] = [], offset = 0
        while offset + 3 <= data.count {
            if data[offset] == 0 && data[offset + 1] == 0 {
                if data[offset + 2] == 1 { starts.append((offset, 3)); offset += 3; continue }
                if offset + 4 <= data.count, data[offset + 2] == 0, data[offset + 3] == 1 {
                    starts.append((offset, 4)); offset += 4; continue
                }
            }
            offset += 1
        }
        guard starts.first?.0 == 0 else { throw MirrorError.invalid("Expected an Annex B HEVC fixture.") }
        return try starts.enumerated().map { index, start in
            let end = index + 1 < starts.count ? starts[index + 1].0 : data.count
            let unit = data.subdata(in: start.0 + start.1..<end)
            guard unit.count >= 2 else { throw MirrorError.invalid("Truncated HEVC fixture NAL.") }
            return unit
        }
    }

    static func parse(_ data: Data) throws -> (MirrorFormat, [MirrorVideo]) {
        let units = try units(data)
        let parameters = try [32, 33, 34].map { type in
            guard let unit = units.first(where: { Int(($0[0] >> 1) & 63) == type }) else {
                throw MirrorError.invalid("HEVC fixture is missing parameter sets.")
            }
            return unit
        }
        let description = try HEVCDecoder.formatDescription(parameters: parameters)
        let dimensions = CMVideoFormatDescriptionGetDimensions(description)
        var payload = Data.words([1, 0x68766331, UInt32(dimensions.width), UInt32(dimensions.height), 0, 3])
        for parameter in parameters { payload.append(.words([UInt32(parameter.count)])); payload.append(parameter) }
        let format = try MirrorFormat(payload: payload)
        var videos: [MirrorVideo] = [], current = Data(), hasVCL = false, keyframe = false
        func appendFrame() throws {
            guard hasVCL else { return }
            let pts = UInt64(videos.count + 1) * 16_666_667
            var payload = Data.words([1, UInt32(pts >> 32), UInt32(truncatingIfNeeded: pts), keyframe ? 1 : 0])
            payload.append(current)
            videos.append(try MirrorVideo(payload: payload))
            current.removeAll(keepingCapacity: true); hasVCL = false; keyframe = false
        }
        for unit in units {
            let type = (unit[0] >> 1) & 63
            if type >= 32 && type <= 34 { continue }
            if type <= 31 {
                guard unit.count >= 3 else { throw MirrorError.invalid("Truncated HEVC slice.") }
                if unit[2] & 0x80 != 0 && hasVCL { try appendFrame() }
                hasVCL = true
                keyframe = keyframe || (16...23).contains(type)
            } else if type == 35 && hasVCL { try appendFrame() }
            current.append(.words([UInt32(unit.count)])); current.append(unit)
        }
        try appendFrame()
        guard !videos.isEmpty else { throw MirrorError.invalid("HEVC fixture has no frames.") }
        return (format, videos)
    }

    // Headless diagnostics use the same hardware decoder as the native view and
    // never connect to, start, stop or send input to a phone.
    static func verify(path: String, output: String?, scanBarcode: Bool = false) -> Int32 {
        do {
            let (format, videos) = try parse(Data(contentsOf: URL(fileURLWithPath: path)))
            let declaredColor = try HEVCDecoder.declaredColor(parameters: format.parameters)
            let declaredFullRange = try HEVCDecoder.declaredFullRange(parameters: format.parameters)
            let result = FixtureResult()
            let decoder = HEVCDecoder(onFrame: { frame in result.received(frame) }, onError: { error in result.failed(error) })
            decoder.setBenchmarkEnabled(scanBarcode)
            decoder.configure(format)
            for video in videos {
                decoder.submit(video)
                guard result.completed.wait(timeout: .now() + 5) == .success else { throw MirrorError.timeout }
                if let error = result.error { throw MirrorError.invalid(error) }
            }
            guard let lastFrame = result.latest else { throw MirrorError.invalid("The fixture produced no decoded image.") }
            let decodedColor = MirrorColor.metadata(lastFrame.pixelBuffer)
            try MirrorColor.validate(decodedColor, context: "Decoded fixture", requireComplete: true)
            if let output, let frame = result.latest {
                let image = CIImage(cvPixelBuffer: frame.pixelBuffer)
                guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
                      let cgImage = CIContext().createCGImage(image, from: image.extent, format: .RGBA8, colorSpace: colorSpace),
                      let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL,
                          UTType.png.identifier as CFString, 1, nil) else { throw MirrorError.invalid("Could not create decoded fixture PNG.") }
                CGImageDestinationAddImage(destination, cgImage, nil)
                guard CGImageDestinationFinalize(destination) else { throw MirrorError.invalid("Could not write decoded fixture PNG.") }
            }
            let outputProperties = decoder.fixtureOutputProperties()
            decoder.reset()
            let pixels = lastFrame.pixelBuffer
            var report: [String: Any] = ["decodedFrames": result.count, "inputFrames": videos.count,
                "width": format.geometry.portraitWidth, "height": format.geometry.portraitHeight,
                "hardwareDecoderRequired": true, "errors": 0, "colorContract": MirrorColor.report,
                "declaredColor": declaredColor,
                "assumedColorFields": MirrorColor.fields.map(\.name).filter { declaredColor[$0] == nil },
                "decodedColor": decodedColor,
                "decodedColorSpaceResolved": MirrorColor.hasResolvableColorSpace(pixels),
                "declaredFullRange": declaredFullRange as Any? ?? NSNull(),
                "decodedPixelFormat": MirrorPixelBuffer.name(CVPixelBufferGetPixelFormatType(pixels)),
                "decodedDataSize": CVPixelBufferGetDataSize(pixels),
                "decodedPlanes": (0..<CVPixelBufferGetPlaneCount(pixels)).map { plane in
                    ["width": CVPixelBufferGetWidthOfPlane(pixels, plane), "height": CVPixelBufferGetHeightOfPlane(pixels, plane),
                     "bytesPerRow": CVPixelBufferGetBytesPerRowOfPlane(pixels, plane)]
                }, "decoderOutputProperties": outputProperties]
            if scanBarcode { report["barcodeScan"] = result.barcodeReport }
            print(String(decoding: try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), as: UTF8.self))
            return result.count == videos.count ? 0 : 1
        } catch { FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8)); return 1 }
    }
}

private final class FixtureResult: @unchecked Sendable {
    let completed = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var storedError: String?
    private var storedFrame: DecodedFrame?
    private var storedCount = 0
    private var barcodeFrames = 0
    private var barcodeChanges = 0
    private var firstBarcode: UInt32?
    private var lastBarcode: BarcodeObservation?
    var error: String? { lock.withLock { storedError } }
    var latest: DecodedFrame? { lock.withLock { storedFrame } }
    var count: Int { lock.withLock { storedCount } }
    var barcodeReport: [String: Any] { lock.withLock {
        var report: [String: Any] = ["observedFrames": barcodeFrames, "changedSequences": barcodeChanges,
            "firstSequence": firstBarcode as Any? ?? NSNull(), "lastSequence": lastBarcode?.sequence as Any? ?? NSNull()]
        if let lastBarcode { report["location"] = ["x": lastBarcode.x, "y": lastBarcode.y, "pitch": lastBarcode.pitch] }
        return report
    } }
    func received(_ frame: DecodedFrame) {
        lock.withLock {
            storedFrame = frame; storedCount += 1
            if let barcode = frame.barcode {
                barcodeFrames += 1
                if barcode.sequence != lastBarcode?.sequence { barcodeChanges += 1 }
                if firstBarcode == nil { firstBarcode = barcode.sequence }
                lastBarcode = barcode
            }
        }
        completed.signal()
    }
    func failed(_ error: String) { lock.withLock { storedError = error }; completed.signal() }
}
