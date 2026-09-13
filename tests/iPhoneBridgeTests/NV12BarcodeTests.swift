import XCTest
import CoreVideo
import Accelerate
@testable import iPhoneBridge

final class NV12BarcodeTests: XCTestCase {
    private let palette = [(16, 16, 16), (240, 240, 240), (255, 48, 48), (48, 255, 48),
                           (48, 48, 255), (48, 255, 255), (255, 255, 48), (255, 48, 255), (255, 144, 48)]

    private func pixelRange() -> vImage_YpCbCrPixelRange {
        vImage_YpCbCrPixelRange(Yp_bias: 16, CbCr_bias: 128, YpRangeMax: 235, CbCrRangeMax: 240,
                              YpMax: 255, YpMin: 0, CbCrMax: 255, CbCrMin: 0)
    }

    private func buffer(width: Int, height: Int, format: OSType = MirrorPixelBuffer.pixelFormat) throws -> CVPixelBuffer {
        var value: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, width, height, format,
            [kCVPixelBufferBytesPerRowAlignmentKey: 256] as CFDictionary, &value), kCVReturnSuccess)
        return try XCTUnwrap(value)
    }

    private func marker(_ cell: Int, sequence: UInt32) -> Int {
        if let value = [0: 2, 1: 3, 2: 4, 3: 5, 36: 6, 37: 7, 38: 5, 39: 8][cell] { return value }
        return Int((sequence >> (35 - cell)) & 1)
    }

    private func fixture(width: Int, height: Int, turns: UInt32, x: Int, pitch: Int,
                         sequence: UInt32, corruptFooter: Bool = false) throws -> (CVPixelBuffer, MirrorGeometry) {
        let geometry = try MirrorGeometry(width: UInt32(width), height: UInt32(height), turns: turns, generation: 1)
        let pixels = try buffer(width: width, height: height)
        var argb = [UInt8](repeating: 0, count: width * height * 4)
        for dy in 0..<Int(geometry.height) { for dx in 0..<Int(geometry.width) {
            var index = 0
            if (12..<36).contains(dy), (x..<(x + 40 * pitch)).contains(dx) {
                let cell = (dx - x) / pitch
                index = corruptFooter && cell == 39 ? 0 : marker(cell, sequence: sequence)
            }
            let source: (Int, Int)
            switch turns {
            case 1: source = (dy, height - 1 - dx)
            case 2: source = (width - 1 - dx, height - 1 - dy)
            case 3: source = (width - 1 - dy, dx)
            default: source = (dx, dy)
            }
            let offset = (source.1 * width + source.0) * 4, rgb = palette[index]
            argb[offset] = 255; argb[offset + 1] = UInt8(rgb.0)
            argb[offset + 2] = UInt8(rgb.1); argb[offset + 3] = UInt8(rgb.2)
        } }
        var range = pixelRange(), conversion = vImage_ARGBToYpCbCr(), matrix = vImage_ARGBToYpCbCrMatrix.itu_R_709_2
        XCTAssertEqual(vImageConvert_ARGBToYpCbCr_GenerateConversion(&matrix,
            &range, &conversion, kvImageARGB8888, kvImage420Yp8_CbCr8, vImage_Flags(kvImageNoFlags)), kvImageNoError)
        XCTAssertEqual(CVPixelBufferLockBaseAddress(pixels, []), kCVReturnSuccess)
        defer { CVPixelBufferUnlockBaseAddress(pixels, []) }
        var luma = vImage_Buffer(data: CVPixelBufferGetBaseAddressOfPlane(pixels, 0), height: vImagePixelCount(height),
            width: vImagePixelCount(width), rowBytes: CVPixelBufferGetBytesPerRowOfPlane(pixels, 0))
        var chroma = vImage_Buffer(data: CVPixelBufferGetBaseAddressOfPlane(pixels, 1), height: vImagePixelCount(height / 2),
            width: vImagePixelCount(width / 2), rowBytes: CVPixelBufferGetBytesPerRowOfPlane(pixels, 1))
        argb.withUnsafeMutableBytes { bytes in
            var source = vImage_Buffer(data: bytes.baseAddress!, height: vImagePixelCount(height),
                width: vImagePixelCount(width), rowBytes: width * 4)
            XCTAssertEqual(vImageConvert_ARGB8888To420Yp8_CbCr8(&source, &luma, &chroma, &conversion,
                [0, 1, 2, 3], vImage_Flags(kvImageNoFlags)), kvImageNoError)
        }
        return (pixels, geometry)
    }

    func testPlanarReadbackPreservesAllRotationsAndPaddedStrides() throws {
        for turns: UInt32 in 0..<4 {
            let (pixels, geometry) = try fixture(width: 640, height: 400, turns: turns,
                x: 20, pitch: 8, sequence: 0x87654321)
            XCTAssertGreaterThan(CVPixelBufferGetBytesPerRowOfPlane(pixels, 0), 640)
            XCTAssertGreaterThan(CVPixelBufferGetBytesPerRowOfPlane(pixels, 1), 640)
            var scanner = BarcodeScanner()
            XCTAssertEqual(scanner.read(pixels, geometry: geometry)?.sequence, 0x87654321)
        }
    }

    func testOddPitchChromaEdgesKeepHeaderAndFooterValidation() throws {
        for corrupt in [false, true] {
            let (pixels, geometry) = try fixture(width: 1170, height: 60, turns: 0,
                x: 45, pitch: 27, sequence: 0x800013cd, corruptFooter: corrupt)
            var scanner = BarcodeScanner()
            if corrupt { XCTAssertNil(scanner.read(pixels, geometry: geometry)) }
            else { XCTAssertEqual(scanner.read(pixels, geometry: geometry)?.sequence, 0x800013cd) }
        }
    }

    func testUnexpectedFormatAndGeometryAreRejectedBeforeReadingPlanes() throws {
        let geometry = try MirrorGeometry(width: 32, height: 32, turns: 0, generation: 1)
        for format in [kCVPixelFormatType_32BGRA, kCVPixelFormatType_420YpCbCr8BiPlanarFullRange] {
            let image = try buffer(width: 32, height: 32, format: format)
            XCTAssertThrowsError(try MirrorPixelBuffer.validate(image, geometry: geometry))
            var scanner = BarcodeScanner()
            XCTAssertNil(scanner.read(image, geometry: geometry))
        }
        let image = try buffer(width: 34, height: 32)
        XCTAssertThrowsError(try MirrorPixelBuffer.validate(image, geometry: geometry))
    }

    func testSampledRGBMatchesAccelerate709VideoRangeReference() throws {
        var range = pixelRange(), conversion = vImage_YpCbCrToARGB()
        var matrix = vImage_YpCbCrToARGBMatrix(Yp: 1, Cr_R: 1.5748, Cr_G: -0.46812427, Cb_G: -0.18732427, Cb_B: 1.8556)
        XCTAssertEqual(vImageConvert_YpCbCrToARGB_GenerateConversion(&matrix,
            &range, &conversion, kvImage420Yp8_CbCr8, kvImageARGB8888, vImage_Flags(kvImageNoFlags)), kvImageNoError)
        for y: UInt8 in [0, 16, 30, 64, 128, 192, 222, 235, 255] {
            for cb: UInt8 in [16, 64, 128, 192, 240] { for cr: UInt8 in [16, 64, 128, 192, 240] {
                var luma = [UInt8](repeating: y, count: 4), chroma = [cb, cr], argb = [UInt8](repeating: 0, count: 16)
                luma.withUnsafeMutableBytes { yp in chroma.withUnsafeMutableBytes { uv in argb.withUnsafeMutableBytes { rgb in
                    var yBuffer = vImage_Buffer(data: yp.baseAddress!, height: 2, width: 2, rowBytes: 2)
                    var uvBuffer = vImage_Buffer(data: uv.baseAddress!, height: 1, width: 1, rowBytes: 2)
                    var output = vImage_Buffer(data: rgb.baseAddress!, height: 2, width: 2, rowBytes: 8)
                    XCTAssertEqual(vImageConvert_420Yp8_CbCr8ToARGB8888(&yBuffer, &uvBuffer, &output,
                        &conversion, [0, 1, 2, 3], 255, vImage_Flags(kvImageNoFlags)), kvImageNoError)
                } } }
                let actual = BarcodeScanner.videoRangeRGB(y: y, cb: cb, cr: cr)
                for (component, expected) in zip([actual.0, actual.1, actual.2], argb[1...3]) {
                    XCTAssertLessThanOrEqual(abs(component - Int(expected)), 1)
                }
            } }
        }
    }
}
