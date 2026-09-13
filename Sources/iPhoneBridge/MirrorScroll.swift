import AppKit

struct MirrorScrollSession {
    private var contactActive = false
    private var momentumAllowed = false

    mutating func cancel() { contactActive = false; momentumAllowed = false }

    mutating func accepts(phase: NSEvent.Phase, momentum: NSEvent.Phase) -> Bool {
        if phase.contains(.cancelled) || momentum.contains(.cancelled) { cancel(); return false }
        if !momentum.isEmpty {
            guard momentumAllowed else { return false }
            if momentum.contains(.ended) { momentumAllowed = false }
            return true
        }
        if phase.isEmpty { return true } // Ordinary mouse wheels have no contact phases.
        if phase.contains(.began) { contactActive = true; momentumAllowed = true }
        guard contactActive else { return false }
        if phase.contains(.ended) { contactActive = false }
        return true
    }
}

// Coordinates and deltas use the displayed phone orientation. The daemon owns
// inverse rotation, as it does for mouse drags. AppKit already applies the user's
// natural-scroll preference; positive Y moves the finger down the displayed image.
struct MirrorScroll: Equatable, Sendable {
    let geometry: MirrorGeometry
    let x: UInt32
    let y: UInt32
    private(set) var distance: Double

    init?(geometry: MirrorGeometry, x: UInt32, y: UInt32, deltaY: Double,
          precise: Bool, pixelsPerPoint: Double) {
        guard deltaY.isFinite, pixelsPerPoint.isFinite, pixelsPerPoint > 0,
              x < geometry.width, y < geometry.height, deltaY != 0 else { return nil }
        self.geometry = geometry; self.x = x; self.y = y
        let limit = min(480, Double(geometry.height) * 0.4)
        distance = max(-limit, min(limit, deltaY * (precise ? pixelsPerPoint : 48)))
    }

    func merged(with newer: MirrorScroll) -> MirrorScroll {
        guard geometry == newer.geometry else { return newer }
        var result = newer
        let limit = min(480, Double(geometry.height) * 0.4)
        result.distance = max(-limit, min(limit, distance + newer.distance))
        return result
    }

    // Accumulate small deltas before putting a finger down, so wheel input does
    // not become a series of near-stationary taps on controls under the cursor.
    var endY: UInt32 { UInt32(max(0, min(Double(geometry.height - 1), (Double(y) + distance).rounded()))) }
    var moves: Bool { abs(Double(endY) - Double(y)) >= min(32, Double(geometry.width) * 0.05) }
    func point(step: Int, of count: Int) -> (UInt32, UInt32) {
        let fraction = Double(max(0, min(count, step))) / Double(max(1, count))
        return (x, UInt32((Double(y) + (Double(endY) - Double(y)) * fraction).rounded()))
    }
}
