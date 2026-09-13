import XCTest
import CoreMedia
@testable import iPhoneBridge

final class MirrorColorTests: XCTestCase {
    // Original phone probe parameter sets; the tagged SPS variants were generated
    // with FFmpeg's hevc_metadata filter, without encoding or changing picture data.
    private let untagged = "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fe880"
    private let srgb = "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fea6a021a0201"

    private func parameters(_ sps: String) -> [Data] {
        ["40010c01ffff016000000300b00000030000030099170240", sps, "4401c072f05324"].map { hex in
            Data(stride(from: 0, to: hex.count, by: 2).map { offset in
                let start = hex.index(hex.startIndex, offsetBy: offset)
                return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!
            })
        }
    }

    func testUntaggedHistoricalFixtureUsesContractWithoutClaimingTagsWerePresent() throws {
        let parameters = parameters(untagged)
        XCTAssertTrue(try HEVCDecoder.declaredColor(parameters: parameters).isEmpty)
        let description = try HEVCDecoder.formatDescription(parameters: parameters)
        XCTAssertEqual(MirrorColor.metadata(description),
                       ["primaries": "ITU_R_709_2", "transfer": "IEC_sRGB", "matrix": "ITU_R_709_2"])
    }

    func testCanonicalTaggedParameterSetsKeepTheirColourDescription() throws {
        let parameters = parameters(srgb)
        let declared = try HEVCDecoder.declaredColor(parameters: parameters)
        XCTAssertEqual(declared, MirrorColor.metadata(try HEVCDecoder.formatDescription(parameters: parameters)))
        XCTAssertNoThrow(try MirrorColor.validate(declared, context: "Test", requireComplete: true))
    }

    func testExplicitConflictingAndUnknownSPSTagsAreRejectedBeforeCanonicalOverride() throws {
        let variants = [
            ("primaries", "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fea6a181a0201"),
            ("transfer", "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fea6a02020201"),
            ("matrix", "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fea6a021a0c01"),
            ("primaries", "420101016000000300b00000030000030099a002508009f1c44f8817b916452ffcb9fc4fea6bfe1a0201")]
        for (field, sps) in variants {
            XCTAssertThrowsError(try HEVCDecoder.formatDescription(parameters: parameters(sps))) { error in
                XCTAssertTrue(error.localizedDescription.contains("conflicting \(field)"), error.localizedDescription)
            }
        }
    }

    func testDecodedMetadataRequiresAllContractFields() throws {
        let canonical = try HEVCDecoder.declaredColor(parameters: parameters(srgb))
        for field in ["primaries", "transfer", "matrix"] {
            var missing = canonical; missing.removeValue(forKey: field)
            XCTAssertThrowsError(try MirrorColor.validate(missing, context: "Decoded", requireComplete: true))
        }
    }
}
