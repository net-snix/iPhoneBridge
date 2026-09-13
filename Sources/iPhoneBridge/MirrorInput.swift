import AppKit

struct ImagePlacement {
    let rect: CGRect
    let geometry: MirrorGeometry

    init(bounds: CGRect, geometry: MirrorGeometry) {
        self.geometry = geometry
        let scale = min(bounds.width / CGFloat(geometry.width), bounds.height / CGFloat(geometry.height))
        let size = CGSize(width: CGFloat(geometry.width) * scale, height: CGFloat(geometry.height) * scale)
        rect = CGRect(x: bounds.midX - size.width / 2, y: bounds.midY - size.height / 2,
                      width: size.width, height: size.height)
    }

    // View coordinates are top-left based. The daemon owns inverse rotation.
    func pixel(_ point: CGPoint, clamp: Bool) -> (UInt32, UInt32)? {
        guard rect.width > 0, rect.height > 0, clamp || rect.contains(point) else { return nil }
        let x = max(0, min(CGFloat(geometry.width - 1), floor((point.x - rect.minX) * CGFloat(geometry.width) / rect.width)))
        let y = max(0, min(CGFloat(geometry.height - 1), floor((point.y - rect.minY) * CGFloat(geometry.height) / rect.height)))
        return (UInt32(x), UInt32(y))
    }
}

struct HIDStroke: Equatable, Sendable {
    let usage: UInt32
    let shift: Bool
}

enum HIDKeyboard {
    static func character(_ value: Character) -> HIDStroke? {
        guard let ascii = value.asciiValue else { return nil }
        if ascii >= 97 && ascii <= 122 { return HIDStroke(usage: UInt32(ascii - 97 + 4), shift: false) }
        if ascii >= 65 && ascii <= 90 { return HIDStroke(usage: UInt32(ascii - 65 + 4), shift: true) }
        if ascii >= 49 && ascii <= 57 { return HIDStroke(usage: UInt32(ascii - 49 + 30), shift: false) }
        let plain: [Character: UInt32] = ["0": 39, "\r": 40, "\n": 40, "\t": 43, " ": 44,
            "-": 45, "=": 46, "[": 47, "]": 48, "\\": 49, ";": 51, "'": 52, "`": 53, ",": 54, ".": 55, "/": 56]
        if let usage = plain[value] { return HIDStroke(usage: usage, shift: false) }
        let shifted = Dictionary(uniqueKeysWithValues: zip(Array("!@#$%^&*()_+{}|:\"~<>?"), Array("1234567890-=[]\\;'`,./")))
        if let base = shifted[value], let stroke = character(base) { return HIDStroke(usage: stroke.usage, shift: true) }
        return nil
    }

    static func special(_ keyCode: UInt16) -> UInt32? {
        [36: 40, 76: 88, 48: 43, 53: 41, 51: 42, 117: 76, 123: 80, 124: 79,
         125: 81, 126: 82, 115: 74, 119: 77, 116: 75, 121: 78,
         122: 58, 120: 59, 99: 60, 118: 61, 96: 62, 97: 63, 98: 64, 100: 65,
         101: 66, 109: 67, 103: 68, 111: 69][keyCode]
    }
}
