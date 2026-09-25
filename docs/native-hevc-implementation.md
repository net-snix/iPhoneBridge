# Native mirror implementation and qualification

Implementation branch: `refactor/native-hevc-mirror`, based on `86276b4` including
the capture governor fix. The original checkout and working governor artifacts
are retained as a rollback and baseline; its bridge is stopped while the native
candidate owns the phone connection. No merge to main is required to stage this work.

Staging adjustment: the isolated branch removes the retired path after native
video/window/control functionality is proven, while the original checkout and
working governor remain intact. The 22-minute, physical recovery, colour-parity
and packaged-release gates remain explicit qualification checks; source cleanup
does not turn an unpassed release gate into a pass.

## Architecture decision

Use a lean GPL phone daemon retaining the verified TrollVNC capture, HID and
jetsam components. VideoToolbox HEVC replaces JPEG/RFB, targeting platform hardware
acceleration. Bounded owned capture
surfaces prevent asynchronous encoder input from being overwritten. One video
subscriber and separate control connections share the framed USB protocol;
input ownership is explicit. Native portrait coordinates remove CPU rotation
from the capture loop. Lossless agent stills use independent packed BGRA data.

The phone daemon requests HEVC with default VideoToolbox encoder selection and
does not enforce a hardware selector. The isolated `8a15` identity probe read
`com.apple.videotoolbox.videoencoder.hevc` on iOS 15.1.1, but the matching HEVC
encoder-list entry did not provide a Boolean hardware flag. Mandatory
verification therefore prevented encoding; the experiment was rejected and the
qualified `f54` behavior restored. The missing flag means unverified, not software.
Sustained throughput and low thermal state establish performance, not hardware
identity. No encoder-identity diagnostic or metadata requirement remains in the
daemon. Apple's direct
[RequireHardware](https://developer.apple.com/documentation/videotoolbox/kvtvideoencoderspecification_requirehardwareacceleratedvideoencoder)
and [UsingHardware](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_usinghardwareacceleratedvideoencoder)
encoder keys require iOS 17.4 and are excluded from the iOS declarations in the
pinned SDK.

On the Mac, Network.framework feeds VideoToolbox decompression, then an
AVSampleBufferDisplayLayer displays decoded buffers immediately. Decoding
explicitly gives the benchmark access to real CVPixelBuffer pixels without a
second decoder. Custom Metal rendering needs measured evidence before adding
its complexity. The protocol is defined in [mirror-protocol.md](mirror-protocol.md).

## Work packets

| Packet | Ownership | Dependencies | Checks |
| --- | --- | --- | --- |
| P1 phone | `device/`, native daemon build script and device-specific tests | protocol contract | source build; framing/geometry/ownership tests; real stream decode |
| P2 Mac | `Sources/iPhoneBridge/`, `Package.swift`, Swift tests | protocol contract | Swift build/tests; known-good HEVC; visible native mirror |
| P3 control | `iphonebridge/`, Python control/protocol tests, CLI receiver | protocol contract | bounded/fragmented wire tests; input cleanup; MCP; live still |
| P4 integration | packaging/setup/CI/locks, docs, legacy removal | P1-P3 | full suite, build provenance, live warm/latency/rotation/recovery gates |

Workers own separate files, preserve unrelated work, and do not deploy or sign
Mac bundles. The parent integrates and performs device operations. Keep the old
path until replacement qualification; clean up dependencies only after that gate.

## Required live gates

Record exact artifact hashes and warm thermal state. Reproduce baseline/probe,
then require a 300-second warm stream at >=58 fps, thermal <=1, clean ffmpeg
decode and approximately zero static-screen video bytes. Verify the visible
native window at >=55 fps for 22 minutes with bounded memory. Measure 30 input
latency trials against the current path. Compare lossless stills to independent
USB capture. Exercise rotation, reconnect, cable pull, lock/unlock, encoder
invalidation and human/agent arbitration. Packaging/release checks follow
`docs/RELEASING.md`; signing and publication retain their explicit authorization
requirements. No performance or release gate is considered passed from a build.

Apple API references checked during implementation:
[low-latency encoding](https://developer.apple.com/documentation/videotoolbox/encoding-video-for-low-latency-conferencing)
and [display-immediately semantics](https://developer.apple.com/documentation/avfoundation/avsamplebufferdisplaylayer/enqueue(_:)).
The pinned iOS 16.5 SDK, iOS 15.0 deployment target and qualified phone's iOS 15.1.1
determine which encoder settings are available; newer documentation does not
change support.
