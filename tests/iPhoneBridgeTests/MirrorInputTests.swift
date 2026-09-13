import XCTest
@testable import iPhoneBridge

final class MirrorInputTests: XCTestCase {
    func testAspectFitAndEdgeClampingInEveryOrientation() throws {
        for turns: UInt32 in 0..<4 {
            let geometry = try MirrorGeometry(width: 1170, height: 2532, turns: turns, generation: 1)
            let placement = ImagePlacement(bounds: CGRect(x: 0, y: 0, width: 800, height: 800), geometry: geometry)
            let topLeft = try XCTUnwrap(placement.pixel(placement.rect.origin, clamp: false))
            XCTAssertEqual(topLeft.0, 0); XCTAssertEqual(topLeft.1, 0)
            let bottomRight = try XCTUnwrap(placement.pixel(CGPoint(x: placement.rect.maxX + 100, y: placement.rect.maxY + 100), clamp: true))
            XCTAssertEqual(bottomRight.0, geometry.width - 1); XCTAssertEqual(bottomRight.1, geometry.height - 1)
            XCTAssertNil(placement.pixel(CGPoint(x: -1, y: -1), clamp: false))
            XCTAssertEqual(placement.rect.width / placement.rect.height, CGFloat(geometry.width) / CGFloat(geometry.height), accuracy: 0.0001)
        }
    }

    func testEveryPrintableASCIIHasHIDMapping() {
        for code in UInt8(32)...126 {
            XCTAssertNotNil(HIDKeyboard.character(Character(UnicodeScalar(code))), "ASCII \(code)")
        }
        XCTAssertNil(HIDKeyboard.character("ø"))
        XCTAssertNil(HIDKeyboard.character("😀"))
    }

    func testUSShiftMappingPreservesBaseUsage() {
        let plain = Array("abcdefghijklmnopqrstuvwxyz1234567890-=[]\\;'`,./")
        let shifted = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()_+{}|:\"~<>?")
        for (base, upper) in zip(plain, shifted) {
            XCTAssertEqual(HIDKeyboard.character(base)?.usage, HIDKeyboard.character(upper)?.usage)
            XCTAssertEqual(HIDKeyboard.character(base)?.shift, false)
            XCTAssertEqual(HIDKeyboard.character(upper)?.shift, true)
        }
    }

    func testNavigationKeyboardHomeIsDifferentFromPhoneButton() {
        XCTAssertEqual(HIDKeyboard.special(115), 74)
        XCTAssertEqual(HIDKeyboard.special(117), 76)
        XCTAssertEqual(HIDKeyboard.special(123), 80)
        XCTAssertEqual(HIDKeyboard.special(36), 40)
    }
}
