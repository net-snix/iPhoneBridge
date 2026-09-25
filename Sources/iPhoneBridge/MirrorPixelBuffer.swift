import Foundation
import CoreVideo

enum MirrorPixelBuffer {
    // The HEVC stream is decoded directly to uncompressed, video-range NV12.
    // Unexpected formats are errors, rather than an implicit RGB conversion.
    static let pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange

    static func name(_ value: OSType) -> String {
        let bytes = (0..<4).map { UInt8(truncatingIfNeeded: value >> (24 - $0 * 8)) }
        return bytes.allSatisfy { (32...126).contains($0) }
            ? String(bytes: bytes, encoding: .ascii)! : "0x" + String(value, radix: 16)
    }

    static func validate(_ buffer: CVPixelBuffer, geometry: MirrorGeometry) throws {
        let width = Int(geometry.portraitWidth), height = Int(geometry.portraitHeight)
        guard CVPixelBufferGetPixelFormatType(buffer) == pixelFormat,
              CVPixelBufferGetWidth(buffer) == width, CVPixelBufferGetHeight(buffer) == height,
              CVPixelBufferGetPlaneCount(buffer) == 2,
              CVPixelBufferGetWidthOfPlane(buffer, 0) == width,
              CVPixelBufferGetHeightOfPlane(buffer, 0) == height,
              CVPixelBufferGetWidthOfPlane(buffer, 1) == (width + 1) / 2,
              CVPixelBufferGetHeightOfPlane(buffer, 1) == (height + 1) / 2,
              CVPixelBufferGetBytesPerRowOfPlane(buffer, 0) >= width,
              CVPixelBufferGetBytesPerRowOfPlane(buffer, 1) >= ((width + 1) / 2) * 2 else {
            throw MirrorError.invalid("The decoder did not produce the required video-range NV12 image layout.")
        }
    }
}
