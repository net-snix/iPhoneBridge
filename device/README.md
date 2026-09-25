# Native phone daemon

The daemon serves IPBM v1 on loopback. SSH owns its session and authentication.
It captures native portrait IOSurfaces, encodes HEVC through VideoToolbox, and sends
lossless BGRA stills on separate control connections. Three owned capture surfaces remain
leased until VideoToolbox finishes consuming them. No non-platform libraries link
into the binary.

The intended encoder path uses platform hardware acceleration. The daemon keeps
VideoToolbox's default encoder selection; it does not require or verify hardware
selection at runtime. A probe on the qualified iOS 15.1.1 phone returned encoder ID
`com.apple.videotoolbox.videoencoder.hevc`, but its encoder-list entry did not
provide a Boolean hardware flag. That result leaves hardware selection unverified;
the experiment requiring that metadata was rejected and removed. Production
GET_STATS does not include the experiment's encoder-identity fields.

The renderer writes directly into a claimed BGRA destination IOSurface. Its
dimensions, aligned row stride and sRGB properties match the previous intermediate
render surface; the separate accelerator transfer is removed. Dirty-frame gating,
capture scheduling and the three surface leases remain unchanged. Before promotion,
compare exact static STILL pixels against the transfer-path baseline, then qualify
input timings and sustained video for coherent frames, color and throughput.

`include/MirrorProtocol.h` contains the framing, coordinate and input lease rules
shared with the host regression harness. `MirrorConnection` bounds parsing,
pending requests and outgoing bytes. Capture, orientation, encoder session state,
input ownership and timers run on main; each peer has a serial socket queue.
`CaptureSchedule` retains an early display request behind one deadline timer.
Each attempt is at least one frame interval after the previous attempt, so display
callback jitter does not discard requests and delayed timers cannot burst to catch
up. Stopping capture invalidates queued wakes before a new session starts.

IPBM v1 has a canonical SDR sRGB color contract. Tightly packed BGRA STILL pixels
use sRGB primaries and transfer. Capture pixel buffers carry an sRGB CGColorSpace,
709 primaries, the sRGB transfer function and a 709 YCbCr matrix. HEVC compression
uses the same tags (ISO 1/13/1); the daemon rejects output samples whose format
description omits or contradicts them. Existing FORMAT VPS/SPS/PPS bytes carry
the encoded color signaling, without adding protocol fields. STATS reports the
expected and last observed ISO triplets plus format-check/error counters; a zero
check count means no output format has been observed. The contract preserves the
capture mechanism's existing gamut; it does not claim pixel equality with an
independent Display P3 USB screenshot. Live qualification must inspect the encoded
stream's tags and compare images in a shared color space.

GET_STATS also includes bounded `timing` diagnostics: the most recent 40 successful
key-downs and 40 encoder completions, in oldest-to-newest order, plus lifetime
capture and post-idle submission counters. Normal processing uses fixed storage and
monotonic clock reads; only GET_STATS constructs JSON. There are no new requests,
flags, logs, capture wakes or codec changes. All `_ns` values use the phone's
`CLOCK_UPTIME_RAW`; subtract timestamps within this clock, not directly from a
Mac timestamp. Zero denotes a stage that has not occurred.

Input rows identify the connection and request, parser completion (`received`),
main-queue handler entry (`handled`), synchronous HID dispatch bounds, the first
subsequent scheduled capture attempt and first captured PTS. Frame rows identify
the latest successful key-down by `input_sequence`; this is temporal correlation,
not proof that the frame contains its visible response. They record the dirty
check, render, surface transfer, VT submission and return, callback entry,
main-queue completion, and video enqueue. `queued_ns` means transport enqueue,
not the last socket byte or physical display. `pts_ns` is stamped when the render
call returns. With direct rendering, `render_end_ns`, `transfer_end_ns` and `pts_ns` are equal,
so the retained transfer-duration diagnostic is zero. The callback may precede submission return; the timing
copy occurs on main after submission, so inline callbacks cannot race that copy.

`forced_keyframe` records the submitted option; `after_idle` identifies a >100 ms
submission gap, independently of whether a keyframe was requested.
`after_idle_submissions` counts successful submissions with that gap; these fields
replace `idle_forced` and `idle_forced_submissions`. `keyframe` records the actual
output. Elapsed idle time alone does not request an IDR or a capture. Subscription,
explicit keyframe requests, generation changes, congestion and encoder errors
retain their existing recovery behavior; the encoder's 120-frame maximum keyframe
interval is unchanged. `submit_gap_ns` is the decision-time age
of the preceding submitted frame's render-return PTS. Failed/unsent completions have zero
`queued_ns`; generation must match when comparing input and frame records. The
histories naturally overwrite old entries and contain request IDs, not key usage
or text. Continuous video retains roughly 0.67 seconds at 60 fps; read GET_STATS
promptly after an input trial when background animation is present.

The capture and input implementations derive from TrollVNC at the revision in
`device-sources.lock.json`. They retain its iOS 15 private API mechanisms with
explicit surface ownership and the supported single-finger/raw keyboard subset.
`OhMyJetsam.mm`, SPI headers and entitlements retain upstream notices. The trimmed
memorystatus header retains the declarations used by the original jetsam source.
The complete daemon is GPL-2.0-only; see `vendor/COPYING` and the notices in the
retained SPI headers. New files are copyright 2026 iPhoneBridge contributors.

Build using `scripts/build-device-deps` from the repository. It copies these exact
sources to an isolated build directory and records every source hash in the
manifest. It never contacts the phone. Source-built operation and physical-device
qualification are separate: follow `docs/native-hevc-implementation.md`.
