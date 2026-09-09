# Changelog

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
