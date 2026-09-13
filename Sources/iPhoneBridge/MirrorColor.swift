import Foundation
import CoreMedia
import CoreVideo

// IPBM/1 captures SDR sRGB BGRA. Its HEVC representation uses ISO colour
// primaries/transfer/matrix code points 1/13/1. Missing tags in old fixtures
// inherit this contract; an explicit different tag must never be reinterpreted.
enum MirrorColor {
    static var extensions: [String: String] {
        [kCMFormatDescriptionExtension_ColorPrimaries as String: kCVImageBufferColorPrimaries_ITU_R_709_2 as String,
         kCMFormatDescriptionExtension_TransferFunction as String: kCVImageBufferTransferFunction_sRGB as String,
         kCMFormatDescriptionExtension_YCbCrMatrix as String: kCVImageBufferYCbCrMatrix_ITU_R_709_2 as String]
    }

    static var fields: [(name: String, key: CFString)] {
        [("primaries", kCMFormatDescriptionExtension_ColorPrimaries),
         ("transfer", kCMFormatDescriptionExtension_TransferFunction),
         ("matrix", kCMFormatDescriptionExtension_YCbCrMatrix)]
    }

    static func metadata(_ description: CMFormatDescription) -> [String: String] {
        Dictionary(uniqueKeysWithValues: fields.compactMap { name, key in
            CMFormatDescriptionGetExtension(description, extensionKey: key).map { (name, String(describing: $0)) }
        })
    }

    static func metadata(_ buffer: CVPixelBuffer) -> [String: String] {
        Dictionary(uniqueKeysWithValues: fields.compactMap { name, key in
            CVBufferCopyAttachment(buffer, key, nil).map { (name, String(describing: $0)) }
        })
    }

    static func validate(_ metadata: [String: String], context: String, requireComplete: Bool = false) throws {
        let expected = extensions
        for (name, key) in fields {
            guard let value = metadata[name] else {
                if requireComplete { throw MirrorError.invalid("\(context) is missing the sRGB \(name) tag.") }
                continue
            }
            guard value == expected[key as String] else {
                throw MirrorError.invalid("\(context) has conflicting \(name) \(value); IPBM/1 requires sRGB (1/13/1).")
            }
        }
    }

    static func hasResolvableColorSpace(_ buffer: CVPixelBuffer) -> Bool {
        guard let attachments = CVBufferCopyAttachments(buffer, .shouldPropagate),
              let space = CVImageBufferCreateColorSpaceFromAttachments(attachments)?.takeRetainedValue() else { return false }
        return space.model == .rgb
    }

    static var report: [String: Any] {
        ["space": "sRGB", "primariesCodePoint": 1, "transferCodePoint": 13, "matrixCodePoint": 1]
    }
}
