# Handoff: native HEVC mirror, full replacement

Implementation follow-up: this document preserves the original proposal and
baseline. The native replacement is staged on `refactor/native-hevc-mirror`;
see [implementation decisions](native-hevc-implementation.md),
[current qualification evidence](native-hevc-2026-09-13.md) and
[validation status](../VALIDATION.md). Historical paths, build commands and
running-artifact identities below are not the current native release procedure.

Status 2026-09-13. Written for the agent picking this up cold. Read this first,
then the evidence it points to. Everything below the "Findings" section is a
starting plan, not a settled design: measure, and change it where the data says so.

## Mission

Replace the whole video path — LibVNCServer RFB/Tight JPEG on the phone,
websockify, and noVNC in a WKWebView on the Mac — with:

- **Phone:** production capture → VideoToolbox **hardware HEVC** encode.
- **Wire:** a small original framed protocol over the existing USB SSH forward.
- **Mac:** native AppKit decode and display (VideoToolbox /
  `AVSampleBufferDisplayLayer`).
- **Input and agent control** move onto the same protocol. RFB, noVNC and
  websockify are gone at the end.

Goal: sustained ~60 fps at full resolution with lower input-to-visible latency
than today, on a warm phone, indefinitely.

## Current state

- Branch `fix/capture-rate-governor`, three commits, **not merged, not pushed**:
  - `f29b722` fix: capture-rate governor (the shipped improvement below)
  - `7a34a18` docs: hardware encode feasibility
  - `c4dec16` docs: hardware output decodes as valid video
- Deployed phone daemon: `a3fd78d1…` (governor build). Bridge healthy.
- Tests green: 78 Python (`.venv/bin/python -m unittest discover -s tests`),
  74 Node (`node --test viewer/*.test.mjs`). CI also runs `swift build -c release`.
- **Decision for Espen before starting:** merge the governor branch to `main`.
  It helps the current path regardless of this project and is independent of it.

## Findings (why this project exists)

Full detail: [capture-pacing-2026-09-12.md](capture-pacing-2026-09-12.md),
[hardware-encode-probe-2026-09-12.json](hardware-encode-probe-2026-09-12.json).

### 1. The long-session slowdown was CPU throttling driven by our own waste

- Existing PMU counters, reduced to effective clock and IPC, show JPEG worker
  execution efficiency falling ~2.9× while work per update stays flat (~43 JPEG
  jobs). Final warm window: **2.016 GHz, IPC 1.94** — the A15 efficiency core's
  max clock and a 4-wide core. Threads lose performance-core residency.
- Control: `IOSurfaceAcceleratorTransferSurface` (fixed-function hardware) stays
  flat (4.44 → 4.63 ms) while every CPU stage slows 2–3×. Not memory, I/O,
  contention or leaked state.
- Capture ran at 60 fps regardless of encoder capacity; ~20 captures/s were
  rendered and prepared only to be discarded. Pure heat → throttling → slower
  encode → more waste. More workers and higher QoS all failed for this reason:
  the constraint is energy, not scheduling.

### 2. Governor fix (shipped on the branch) stabilises but caps at ~30 fps

`CaptureGovernor.h` paces capture to measured delivery (fast back-off, slow
probe-up; a symmetric controller oscillated and was rejected).

| Phone-only sink, heavy motion | min 1 | 2 | 3 | 4 | 5 | 6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before (`ae83019c`) | 32.9 | 16.5 | 17.5 | 17.2 | 18.3 | 18.7 |
| Governor | 37.3 | 30.1 | 30.0 | 30.0 | 30.0 | 29.9 |

Gap p95 69.3 → 35.6 ms. **60 fps is not reachable with full-resolution CPU
JPEG on this device.** That ceiling is the reason for this project.

### 3. Hardware HEVC encode is feasible, cool, and cheap (measured)

Probe: production `ScreenCapturer` → `VTCompressionSession`, output discarded.

- **Hardware HEVC session created and driven from the jailbroken root daemon.**
  This was the main risk (iOS gates hardware encode to foreground apps).
- **300 s at 59.1–59.3 fps, zero encode failures, thermal state 0 throughout.**
  Matched cold JPEG run (same fixture/rate) reached state 2 (serious) by minute 4.
- Submit CPU **0.073 ms/frame** vs JPEG 7.9–19.3 ms/update.
- **4.8 MB/s** (≈38 Mbit/s under the heavy-motion fixture) vs ~46 MB/s for JPEG.
- **Encoder latency 20.4–20.6 ms mean, 24.6 ms max** (submit → callback,
  default rate control). About one frame. This is the main cost of the approach.
- **Valid video:** 8 s Annex B dump decoded by ffmpeg — HEVC Main, 1170×2532,
  yuv420p, level 5.1, **475 frames, 0 errors**; decoded frame visually correct.

Not yet measured: transport, Mac decode/display, glass-to-glass latency, input,
warm-start hardware runs, static-screen behaviour.

## Current architecture (what gets replaced)

```
PHONE (iOS 15.1.1, A15)                         MAC (macOS 26, Apple silicon)
CADisplayLink (main)
 └ ScreenCapturer.attemptCapture
    ├ TVCaptureSchedule gate (+ CaptureGovernor)
    ├ CARenderServerRenderDisplay → srcSurface
    └ IOSurfaceAcceleratorTransferSurface → dst (BGRA, always portrait)
 └ handleFramebuffer: rotate/scale (CPU), tile hashes, dirty rects
 └ tryPublishPreparedFrame → LibVNCServer
    └ Tight: 1172-wide → 55-row strips → ~47 tjCompress calls/frame
 └ RFB on phone 127.0.0.1:15901
        │  ssh -L 127.0.0.1:15901 ← launched as `exec device-session.sh run`
        │  iproxy 15422→22 over USB
        ▼
                                                websockify :15801 ↔ :15901
                                                WKWebView → noVNC 1.7.0
                                                 JPEG rects via HTMLImageElement
                                                 → canvas; input as RFB events
                                                Agent: control.py (vncdotool) on :15901
                                                 1×1 size probe, RAW frame → PNG
                                                MCP server: screenshot/tap/drag/
                                                 text/key/health
Fixture: Safari → 127.0.0.1:15802 (SSH reverse forward to the Mac)
```

Key files (read these before designing; details not summarised here):

| Area | Path |
| --- | --- |
| Capture | `work/device-build-governor/TrollVNC/src/ScreenCapturer.mm` |
| Daemon (RFB, input, orientation, lifecycle) | `…/src/trollvncserver.mm` (~5200 lines) |
| Touch/keyboard injection | `…/src/STHIDEventGenerator.mm` |
| Capture pacing | `…/src/CaptureSchedule.h`, `CaptureGovernor.h`, `FrameUpdateRetry.h` |
| Jetsam protection | `…/src/OhMyJetsam.mm` |
| Launch profile | `iphonebridge/device-session.sh` |
| Lifecycle, ports, forwards | `iphonebridge/lifecycle.py`, `transport.py` |
| Deploy + manifest checks | `iphonebridge/deployment.py` |
| Agent path / MCP | `iphonebridge/control.py`, `iphonebridge/mcp_server.py` |
| Mac app | `Sources/iPhoneBridge/App.swift`, `ConnectionSettings.swift` |
| Viewer + input mapping | `viewer/main.js`, `viewer/mirror.mjs` (+ tests) |
| Build | `scripts/build-device-deps`, `scripts/build-app`, `docs/BUILDING.md` |
| Gates, licences | `VALIDATION.md`, `THIRD_PARTY.md`, `licenses/`, `docs/RELEASING.md` |

The `work/device-build-governor` tree equals clean upstream TrollVNC `a3e40816`
+ `patches/trollvnc-loopback.patch` + `patches/trollvnc-source-deps.patch`.

## Target architecture (starting proposal)

### Phone daemon

Keep: `ScreenCapturer` (render + hardware transfer), orientation observer,
`STHIDEventGenerator`, `OhMyJetsam`, the token/ownership launch model.

Replace: LibVNCServer, Tight/JPEG, tile hashing, prepare/publish machinery.

- **Recommended:** a new lean daemon source (e.g. `MirrorServer.mm`,
  `HEVCEncoder.mm`, `MirrorProtocol.h`) in the TrollVNC Theos project, reusing the
  kept files, rather than gutting the VNC-centric `trollvncserver.mm`.
  `device-session.sh` only accepts binaries matching
  `$base/trollvncserver-*` (exit 46) — keep that name or change the script
  (and its `script_sha256`).
- **Encode portrait always; send orientation as metadata; rotate on the Mac.**
  Capture is already portrait. This removes CPU rotation and avoids recreating
  the encoder on every rotation. Input coordinates are then mapped through the
  rotation on one side — decide which and keep it in one place.
- **Pixel format:** BGRA `CVPixelBuffer` straight into VT works (probe). Measure
  whether producing NV12 via `IOSurfaceAccelerator` saves further energy.
- **Static screen:** skip submits when `CARenderServerGetDirtyFrameCount` is
  unchanged (the check already exists in `renderDisplayToScreenSurface`). Send
  nothing; the Mac holds the last frame. Force a keyframe on join, on request,
  after a submit gap, after errors.
- **Backpressure:** never drop encoded frames mid-GOP (breaks decode until the
  next IDR). Gate *submission* on socket send-queue depth instead; force IDR
  after skipping. `CaptureGovernor`'s idea (discarded work = over-drive) maps
  onto "encoder or socket not ready"; it is otherwise likely obsolete.
- **Session invalidation:** handle `kVTInvalidSessionErr` and recreate (lock,
  sleep, media server events). Test lock/unlock explicitly.
- **Latency:** probe used default rate control → ~20 ms. Try H.264 with
  low-latency rate control and HEVC tuning (`MaxFrameDelayCount`, speed over
  quality) and pick by measured glass-to-glass latency vs bytes, not by codec.

### Wire protocol (original code; strawman)

Length-prefixed messages over the existing TCP forward. Reusing port 15901
keeps `lifecycle.py`'s forward unchanged.

```
frame   := u32 length (network order) | u8 type | payload
Phone → Mac
  HELLO        magic "IPBM", version, capabilities
  FORMAT       codec, width, height, orientation, VPS/SPS/PPS (HVCC-style)
  VIDEO        pts_ns, flags(keyframe), 4-byte-length-prefixed NAL units
  STILL        width, height, BGRA or PNG — lossless, for the agent path
  STATS/PONG   encoder latency, bytes, thermal state, echoed timestamps
Mac → Phone
  POINTER      down/move/up, x, y in declared pixel space, t
  KEY          keysym, down/up          BUTTON  home | app_switcher
  REQ_KEYFRAME REQ_STILL  SET_BITRATE   PING
```

Send VT's native length-prefixed NALs plus parameter sets out of band; the Mac
rebuilds the format with `CMVideoFormatDescriptionCreateFromHEVCParameterSets`.
Annex B is only needed for file dumps. Pin the protocol with golden byte vectors
tested on both sides.

### Mac app

- Remove the WKWebView viewer. New native view: connection
  (Network.framework or socket) → sample buffers → `AVSampleBufferDisplayLayer`
  (simplest; flag frames display-immediately). Fall back to
  `VTDecompressionSession` + Metal only if measured latency requires it.
- Port **every current input behaviour** by reading the existing JS/Swift:
  click, drag, drag-to-scroll, ASCII US typing, special keys, ⌘1 Home,
  ⌘2 App Switcher, view→pixel coordinate mapping, aspect fit, rotation,
  fullscreen, reconnect.
- Rebuild the benchmark: detect the fixture barcode directly in the **decoded
  `CVPixelBuffer`** (easier and more exact than today's canvas readback), plus
  display timing. Keep `--benchmark*` flag semantics or document changes.
- The WebKit image-load retention problem in
  [renderer-memory-2026-09-09.md](renderer-memory-2026-09-09.md) disappears;
  its decoding-document code and `patches/novnc-image-lifecycle.patch` go.

### Agent / MCP path

- Port `control.py` off vncdotool/RFB. Screenshots stay **lossless** via `STILL`
  (read the IOSurface, never the HEVC stream). Input via `POINTER`/`KEY`/`BUTTON`.
- Preserve: MCP tool names and parameters, stale-dimension rejection, post-action
  lossless screenshot, `framebuffer_pixels` coordinate contract.
- **Coordinate space changes:** today's framebuffer is aligned to **1172×2536**;
  native capture is **1170×2532**. Decide the declared space deliberately and
  update tools, tests and docs together.

## Phased plan with exit gates

Keep a working mirror at every step; delete the old path only in phase 6.

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| 0 | Branch from `main` (after governor merge). Reproduce baseline sink + probe runs. | Numbers match this doc within noise. |
| 1 | Phone streaming server (probe → real), protocol v1, **CLI receiver** writing `.h265`. | 300 s warm-phone run ≥58 fps, thermal ≤1, ffprobe 0 errors, static screen ≈0 bytes. |
| 2 | Native Mac view decoding the stream. Develop first against `work/hevc-handoff/hw-probe-stream.h265`. | Visible ≥55 fps for 22 min on a warm phone, visible window verified, memory bounded. |
| 3 | Input over the protocol, all current behaviours. | 30-trial input latency: median beats today's 83–98 ms (target ≤60 ms). |
| 4 | Agent/MCP port with lossless stills. | Screenshots pixel-exact vs direct capture; `scripts/check-mcp` passes. |
| 5 | Rotation, reconnect, cable pull, lock/unlock, VT invalidation, human+agent policy. | Scripted pass of each scenario. |
| 6 | Remove noVNC, websockify, LibVNCServer/Tight/libjpeg-turbo/libpng/lzo if unused, `prepare-viewer`, old patches and locks. | CI green; `THIRD_PARTY.md`, `licenses/`, README, `PERFORMANCE.md`, `VALIDATION.md`, CHANGELOG updated. |
| 7 | Release per `docs/RELEASING.md`. | Checklist complete. |

## Tools and procedures that already work

Local artefacts (gitignored `work/`, this Mac):

- `work/hevc-handoff/run-hw-probe.py` — stop bridge → private `iproxy
  15422:22` → upload binary (verified by reading bytes back) → run → fetch report
  (+ optional stream via `HW_PROBE_STREAM_SECONDS=8`) → restart bridge in `finally`.
- `work/hevc-handoff/summarize.py` — per-minute means and gap percentiles for sink reports.
- `work/hevc-handoff/hw-probe-stream.h265` — known-good 8 s HEVC test vector;
  `hevc-frame300.png` decoded reference; raw probe/sink JSON.
- `work/renderer-memory-20260909/measure-tight-sink.py` — phone-only RFB sink
  (`--duration N`); the current-path baseline tool.
- Probe source: `work/device-build-hwencode/TrollVNC/src/HardwareEncodeProbe.mm`,
  hooked in `trollvncserver.mm` `main()` behind `IPHONEBRIDGE_HW_ENCODE_PROBE`;
  Makefile adds `VideoToolbox` and `-DIPHONEBRIDGE_HW_ENCODE_PROBE=1`.

Probe encoder settings: HEVC, `RealTime=true`, `AllowFrameReordering=false`,
`MaxKeyFrameInterval=120`, `ExpectedFrameRate=60`, `AverageBitRate=40 Mbps`
(a generous cap for the probe, not a tuned value).

Load and phone handling:

- Fixture: `./bridge test-surface`; Safari already has
  `http://127.0.0.1:15802/latency.html` open → **Switch to motion mode**,
  **Background: heavy**.
- Phone must be awake and unlocked. An ~8.7 KB black screenshot means the
  display is off; wake with `./bridge navigate home --size 1172 2536`
  (`tap`, `drag`, `type`, `key` and `navigate` all require `--size` of the
  latest screenshot).
- The daemon SSH session occasionally drops ("closed by remote host"):
  `./bridge stop && ./bridge start`.
- **Thermal state dominates results.** Record it every window; always run a
  warm follow-up immediately after a cold run; the phone charges over USB
  throughout. Cold 5-minute passes have repeatedly failed warm.

## Gotchas (each cost time once)

- **Build daemon:** in a TrollVNC tree,
  `make -j4 all THEOS=/Users/espenmac/Code/oss/theos THEOS_PACKAGE_SCHEME=rootless FINALPACKAGE=1 SUBPROJECTS=`.
  Theos commit `16362d3`, `iPhoneOS16.5.sdk`, C++ is `-std=c++20`.
- **Shipping daemon changes** go through `patches/trollvnc-loopback.patch` +
  `dependency-lock.json` (`patch_sha256`, `patched_files_sha256`). Regenerate with
  `git diff --cached -U40` against a clean `a3e40816` tree from
  `work/vendor/TrollVNC`. **`-U20` breaks tests**: `tests/daemon_patch_sources.py`
  extracts whole function bodies from single hunks. Verify with
  `patch --fuzz=0 -p1` forward, then `--reverse --dry-run`.
- **Host harness** compiles headers with `-std=c++20` to match the Makefile;
  new pure headers get a `tests/daemon_*_harness.cpp` registered in
  `tests/test_daemon_performance.py`; ObjC++ extractions need stubs in
  `tests/daemon_overlap_harness.mm.in`.
- **Deploy a candidate (source workflow):** `deployment.py` reads
  `work/build-manifest.json` (binary path + sha256, script sha, `source.commit`).
  Swap it, `./bridge stop && ./bridge start`. The current manifest points at the
  deployed governor build `a3fd78d1`; `work/build-manifest.pre-governor-ae83019c.json`
  is the older pre-governor build, kept only as a control. Deploy refuses while
  `/var/mobile/Media/iPhoneBridge/active` exists.
- **The daemon is launched by the SSH `-L` process.** Stopping it tears down the
  forward; a binary that doesn't serve 15901 can't go through `./bridge start`
  (it waits for the RFB handshake). Use the probe runner pattern.
- **Env vars don't reach the daemon** through `device-session.sh`; change the
  script (and its sha) or use a CLI flag.
- **No `shasum` on the phone.** Verify transfers by reading bytes back.
- **iOS VideoToolbox SDK:** `kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder`,
  `…RequireHardware…` and `kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder`
  are macOS-only and won't compile; on iOS, H.264/HEVC encode is hardware.
- **VT output** is 4-byte length-prefixed NALs; parameter sets via
  `CMVideoFormatDescriptionGetHEVCParameterSetAtIndex`.
- **ProMotion:** `CADisplayLink` rates quantise to 120/n (60, 40, 30, 24…).
- Daemon log (`TVLog`) lands in `work/logs/device.log`.
- `pytest` isn't installed; use `unittest`. CI uses `uv run --frozen python -m unittest discover -s tests -v`.

## Constraints

- Supported: Apple silicon, macOS 26+, jailbroken **iOS 15.1.1** only, tested on
  iPhone 13 Pro (iPhone14,2), SSH key auth as `mobile`. Single-finger input,
  ASCII US keyboard, no audio.
- Release is **ad-hoc signed, not notarized**. Don't re-sign, ad-hoc sign or
  change the bundle ID for debugging without Espen's approval.
- **Licensing:** phone code derived from TrollVNC files stays **GPL-2.0-only**
  with source available. New Mac/Python code is original. Removing noVNC,
  websockify, LibVNCServer and codecs drops their notices — reconcile
  `THIRD_PARTY.md` and `licenses/` rather than assuming.
- Repo conventions: Conventional Commits, no attribution lines, docs updated with
  behaviour, files ~≤500 LOC, **no push or merge without Espen's authorization**.
  Each measured trial gets a `docs/*.md` + `docs/*.json` record with artefact
  hashes and stated limits, including failed trials.

## Open decisions (measure, then choose)

1. New lean daemon target vs reshaping `trollvncserver.mm`.
2. HEVC vs H.264 low-latency; rate-control and bitrate for real content.
3. `AVSampleBufferDisplayLayer` vs `VTDecompressionSession` + Metal.
4. Where rotation is applied for display and for input mapping.
5. Declared coordinate space: 1170×2532 vs 1172×2536.
6. Human + agent on one connection or two; input arbitration.
7. BGRA into VT vs hardware NV12 conversion.
8. Port reuse (15901) vs a new port and lifecycle change.
