# Changelog

## Native HEVC — Unreleased development branch

- Pin the project's Codex baseline to GPT-6 Astra and document native MCP setup.
- Require screenshot generation on CLI/MCP input, rejecting stale coordinates
  even when a rotation keeps the same framebuffer dimensions.
- Replace phone JPEG/RFB streaming with a lean VideoToolbox HEVC daemon and a bounded
  framed protocol over the existing owned USB tunnel.
- Decode directly to smaller YUV buffers and display them in AppKit with
  VideoToolbox and AVFoundation.
- Render into leased encoder surfaces and preserve video references through
  ordinary idle gaps, reducing capture work and static-response traffic.
- Keep agent screenshots lossless, sourced directly from capture surfaces; report
  native 1170×2532 portrait coordinates and reject stale geometry.
- Arbitrate human and agent input with exclusive leases and release held input
  when connections, geometry or focus change.
- Measure fixture pixels after decode and record display submission separately,
  including window visibility and bounded stream/decoder recovery checks.

Qualification and release status are recorded in `VALIDATION.md`; historical
JPEG measurements below do not describe this architecture.

## 0.2.2 — Unreleased candidate

- Pace screen capture to the rate the phone's encoder actually sustains, ending the
  over-drive loop that discarded about a third of all captured frames and turned a
  60 fps mirror into an erratic 17-19 fps one after a few minutes of use.
- Hold a steady delivered rate instead of declining: 30.0 updates/s across five
  consecutive minutes, with inter-update gap p95 cut from 69.3 ms to 35.6 ms.
- Keep image quality, resolution, encoder settings and the wire format unchanged.
- Bound WebKit's retained image-load history by giving the decoder its own document with a limited lifetime.
- Keep the existing native JPEG decoder, image quality, framebuffer size and VNC connection.
- Record window visibility during performance tests so covered-window throttling cannot be mistaken for active mirror performance.

## 0.2.1 — 2026-09-09

- Deliver the newest screen after a busy encoder drops a capture, fixing stale final frames when motion stops.
- Release the exact acquired client locks during frame swaps, allowing disconnected clients to finish cleanup.
- Release decoded viewer images explicitly and recover from image failures instead of leaving rendering stalled.
- Avoid base64 image URLs and duplicate full-frame hashing in the normal capture path.
- Add source-pinned viewer preparation and regression coverage for capture races and image lifecycle failures.

- Daemon: remove LibVNCServer's default 5 ms delay before every update and drop frames behind an in-flight encode (`-Q 1`) for lower mirror latency under heavy motion.
- Faster stop and reconnect: the device session script polls every 0.2 s.
- Agent actions check the framebuffer size with a 1×1 probe instead of a full RAW frame.
- Benchmark fixture gains a heavy full-resolution background and runtime toggles; `measure-mirror` and the new `measure-agent-path` follow the runtime data directory.

## 0.2.0 — 2026-09-07

First public release.

- Standalone Apple silicon app with native icon and connection settings.
- Bundled Python runtime, USB tools, viewer, and source-built phone daemon.
- Live mirroring, orientation-aware layout, keyboard and single-finger input.
- Responsive Home and App Switcher controls, with ⌘1 and ⌘2 shortcuts.
- Local CLI and six MCP tools with screenshot-based input verification.
- Owned USB transport, explicit device selection, and recoverable cleanup.
- Complete component notices, pinned sources, and source archives with the release.

Supports macOS 26+ and jailbroken iOS 15.1.1. See the README for prerequisites
and validation limits.
