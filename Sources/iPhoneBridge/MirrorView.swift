import AppKit
import AVFoundation

@MainActor
final class MirrorView: NSView {
    let displayLayer = AVSampleBufferDisplayLayer()
    var geometry: MirrorGeometry? {
        didSet { if oldValue != geometry { cancelInput(); needsLayout = true } }
    }
    var input: MirrorInputDriver?
    var inputEnabled = true { didSet { if !inputEnabled { cancelInput() } } }
    var onDisplayError: ((String) -> Void)?
    var onSubmitted: ((DecodedFrame) -> Void)?
    private var dragging = false
    private var scrollSession = MirrorScrollSession()
    private var scrollResidue: MirrorScroll?
    private var pendingFrame: DecodedFrame?
    private var waitingForRenderer = false
    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        displayLayer.videoGravity = .resizeAspect
        layer?.addSublayer(displayLayer)
        setAccessibilityRole(.image)
        setAccessibilityLabel("Live iPhone mirror. Click, drag, or scroll to interact; type with a US keyboard.")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func layout() {
        super.layout()
        guard let geometry else { displayLayer.frame = bounds; return }
        let placement = ImagePlacement(bounds: bounds, geometry: geometry)
        CATransaction.begin(); CATransaction.setDisableActions(true)
        displayLayer.setAffineTransform(.identity)
        let size = geometry.quarterTurns % 2 == 0 ? placement.rect.size : CGSize(width: placement.rect.height, height: placement.rect.width)
        displayLayer.bounds = CGRect(origin: .zero, size: size)
        displayLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        displayLayer.setAffineTransform(CGAffineTransform(rotationAngle: CGFloat(geometry.quarterTurns) * .pi / 2))
        CATransaction.commit()
    }

    func present(_ frame: DecodedFrame) {
        guard frame.geometry == geometry else { return }
        pendingFrame = frame
        drainPresentation()
    }

    private func drainPresentation() {
        let renderer = displayLayer.sampleBufferRenderer
        if waitingForRenderer { renderer.stopRequestingMediaData(); waitingForRenderer = false }
        guard let frame = pendingFrame, frame.geometry == geometry else { pendingFrame = nil; return }
        if renderer.status == .failed {
            onDisplayError?(renderer.error?.localizedDescription ?? "The video renderer failed.")
            renderer.flush()
        }
        guard renderer.isReadyForMoreMediaData else {
            waitingForRenderer = true
            renderer.requestMediaDataWhenReady(on: .main) { [weak self] in
                MainActor.assumeIsolated { self?.drainPresentation() }
            }
            return
        }
        pendingFrame = nil
        var description: CMVideoFormatDescription?
        var status = CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault,
            imageBuffer: frame.pixelBuffer, formatDescriptionOut: &description)
        guard status == noErr, let description else {
            onDisplayError?("Could not describe decoded image (\(status))."); return
        }
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: .zero, decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        status = CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: frame.pixelBuffer,
            formatDescription: description, sampleTiming: &timing, sampleBufferOut: &sample)
        guard status == noErr, let sample,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true) else {
            onDisplayError?("Could not prepare decoded image (\(status))."); return
        }
        let dictionary = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
        CFDictionarySetValue(dictionary, Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                             Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
        renderer.enqueue(sample)
        onSubmitted?(frame)
    }

    func clear() {
        cancelInput()
        pendingFrame = nil
        if waitingForRenderer { displayLayer.sampleBufferRenderer.stopRequestingMediaData(); waitingForRenderer = false }
        geometry = nil
        displayLayer.sampleBufferRenderer.flush(removingDisplayedImage: true)
    }

    func cancelInput() { dragging = false; scrollSession.cancel(); scrollResidue = nil; input?.cancel() }

    func navigate(button: UInt32) {
        guard inputEnabled, let geometry else { return }
        // Navigation ends a held finger before sending the phone button, and
        // subsequent mouse-drag events must not revive that retired gesture.
        cancelInput()
        input?.enqueue(.button(geometry.generation, button))
    }

    override func resignFirstResponder() -> Bool { cancelInput(); return super.resignFirstResponder() }

    private func pointer(_ event: NSEvent, action: UInt32) {
        guard inputEnabled, let geometry else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard let (x, y) = ImagePlacement(bounds: bounds, geometry: geometry).pixel(point, clamp: action != 1) else { return }
        input?.enqueue(.pointer(geometry.generation, action, x, y))
    }

    override func mouseDown(with event: NSEvent) {
        guard inputEnabled, let geometry,
              ImagePlacement(bounds: bounds, geometry: geometry).pixel(convert(event.locationInWindow, from: nil), clamp: false) != nil else { return }
        window?.makeFirstResponder(self)
        scrollSession.cancel(); scrollResidue = nil
        dragging = true; pointer(event, action: 1)
    }
    override func mouseDragged(with event: NSEvent) { if dragging { pointer(event, action: 2) } }
    override func mouseUp(with event: NSEvent) { if dragging { pointer(event, action: 0); dragging = false } }

    override func scrollWheel(with event: NSEvent) {
        guard inputEnabled, !dragging, let geometry, window?.isKeyWindow == true else { return }
        if event.phase.contains(.cancelled) || event.momentumPhase.contains(.cancelled) { cancelInput(); return }
        guard scrollSession.accepts(phase: event.phase, momentum: event.momentumPhase) else { return }
        if event.phase.contains(.began) { scrollResidue = nil }
        let placement = ImagePlacement(bounds: bounds, geometry: geometry)
        guard let (x, y) = placement.pixel(convert(event.locationInWindow, from: nil), clamp: false),
              let gesture = MirrorScroll(geometry: geometry, x: x, y: y, deltaY: Double(event.scrollingDeltaY),
                  precise: event.hasPreciseScrollingDeltas, pixelsPerPoint: Double(geometry.height) / placement.rect.height) else { return }
        let accumulated = scrollResidue?.merged(with: gesture) ?? gesture
        scrollResidue = accumulated
        guard accumulated.moves else { return }
        scrollResidue = nil
        input?.enqueue(.scroll(accumulated))
    }

    override func keyDown(with event: NSEvent) {
        guard inputEnabled, let geometry else { return }
        var modifiers: UInt32 = 0
        if event.modifierFlags.contains(.control) { modifiers |= 1 }
        if event.modifierFlags.contains(.option) { modifiers |= 2 }
        if event.modifierFlags.contains(.command) { modifiers |= 4 }
        if let usage = HIDKeyboard.special(event.keyCode) {
            input?.enqueue(.stroke(geometry.generation, HIDStroke(usage: usage, shift: event.modifierFlags.contains(.shift)), modifiers))
        } else if let text = modifiers == 0 ? event.characters : event.charactersIgnoringModifiers {
            for character in text {
                guard let stroke = HIDKeyboard.character(character) else { NSSound.beep(); continue }
                input?.enqueue(.stroke(geometry.generation, stroke, modifiers))
            }
        }
    }
}
