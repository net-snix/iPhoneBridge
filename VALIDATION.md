# Native HEVC validation — 2026-09-13

## 0.3.0 prerelease scope — 2026-09-25

The native HEVC implementation and generation-bound input are merged into `main`.
The release packages those sources as version 0.3.0, build 5. Hosted Python tests,
Swift tests and release compilation passed on the merged implementation.

No iPhone was connected during release preparation, so the physical-device
results below remain dated evidence from September 13 and 17. This prerelease
does not claim new cable-pull, rotation, lock/unlock, encoder-invalidation or
physical-trackpad qualification, or that the 60 ms input-latency target is met.
The release notes record checks performed on the final packaged artifacts.

## Codex integration update — 2026-09-17

GPT-6 Astra at `xhigh` successfully used the native source MCP adapter for health,
lossless screenshots, taps, dragging, typing, Backspace and scrolling. Seven
fixture checks passed in eight executed input calls; an independent USB capture
confirmed the final physical screen. All 139 Python tests pass, including
generation-bound input and same-size rotation rejection. See
[Astra MCP verification](docs/astra-mcp-2026-09-17.md) for exact identities,
checks, a preflight daemon interruption and qualification limits. This update
does not extend the native video or release qualifications recorded below.

## Original native qualification

At the time of this qualification, the native replacement was an isolated
development branch rather than a published release. The detailed trial record, including
failed candidates and mismatched baseline conditions, is
[Native HEVC qualification](docs/native-hevc-2026-09-13.md).

Test environment: Apple silicon Mac, macOS 26+, USB-connected iPhone 13 Pro
(`iPhone14,2`) with jailbroken iOS 15.1.1. No iOS update, jailbreak change, pairing
reset or persistent phone service installation was performed. Bridge ownership
records govern teardown; unrelated services and the original checkout are preserved.

## Implementation checks

- All 56 Swift tests pass: native framing, geometry, barcode, keyboard, ownership cleanup, bounded
  decoder/mailbox and generation-recovery tests pass.
- All 133 Python tests pass, including the eight compiled phone harness tests.
  Control/protocol tests exercise partial framing, command deadlines,
  stale dimensions, lossless orientation transforms, input cleanup and bounded
  payload parsing. Existing CLI/MCP names and required size parameters remain.
- Host-compiled phone harnesses exercise framing, geometry, surface ownership,
  leases, queue backpressure and five-second write-progress expiry.
- Source build and staging checks verify exact native source inventories, retained
  upstream bytes, lock/recipe/helper hashes, Theos/SDK identity and final binaries.
  The final committed source staged and rebuilt offline successfully. Whole-file
  daemon hashes differ because of UUID/signature metadata; executable sections
  and initialized data match the endurance-tested binary. Mac corresponding-source
  validation passes against the actual Python and USB build inputs, including
  the retained Tcl/Tk components. This is provenance proof,
  not a claim of byte-identical output across toolchains.
- The release-mode native VideoToolbox decoder decoded all 600 frames from the
  first real heavy-motion stream with zero errors and hardware decode required.

## Live status

The current phone candidate (`f54af…`) removes an intermediate capture transfer
and preserves HEVC references through ordinary idle gaps. It delivered **59.13
and 59.10 fps** in consecutive five-minute heavy-motion runs, thermal state **0**
throughout both, without increases in encoder, colour-error or submission-skip
counters. Both entire streams decoded without errors. Earlier failed and
mismatched candidates remain in the trial record with their exact identities.

The Mac candidate (`595bc…`) decodes directly to NV12. A matched 30-second profile
found **19.16% fewer app-process running timer samples**, with **22.15% fewer on
the main thread**. This is one sampled comparison, not measured CPU milliseconds
or total system CPU use. Four decoded surface mappings fell **61.31%**; mapped
storage is distinct from RSS. Separate visible-motion checks stayed near
**59.1 fps**. Source-relative image comparisons and a live colour-chart inspection
found no visible regression, with different chroma interpolation at narrow edges.

Static capture, MCP tool discovery and screenshot output, and nine
protocol/ownership checks passed on the earlier native candidate. Tool discovery
does not prove every tool's live input action. Stills preserve captured sRGB pixels, but do not
match the wider-gamut Display P3 system USB screenshots exactly; isolated probes
located that limit in the retained screen-rendering API.

The final 30 input trials completed with **93.71 ms median / 135.77 ms p95**
decoded response, above the 60 ms target. Exact frame joins preserve the three
slower initial keyframe responses; none are excluded. The old canvas benchmark
reported a 100 ms median with coarser observation timing. These separate runs
are not a matched comparison. Two older visible endurance attempts remain
unqualified: the first exposed a barcode detector issue; the second had one
54.27 fps window and was interrupted. Barcode handling and bounded decoder
recovery are now corrected. The current candidate passed **all 44 windows of a
22-minute visible run**, at **58.33–59.30 fps**, with **118.52 MiB peak RSS** and
no median memory growth. All 273 health reports passed renderer continuity;
no barcode misses, ingress drops or recovery requests occurred.

Visible tap, slider drag, ASCII typing, backspace/left-arrow/forward-delete input,
drag-to-scroll, wheel scrolling, Home/App Switcher shortcuts and fullscreen were
exercised on native previews. A physical trackpad has not been tested.
All nine protocol/session checks passed again on the final f54 phone daemon,
as did six-tool MCP discovery and its native lossless PNG. The final Mac preview
also visibly reconnected after a normal process restart.

Remaining live checks include the 60 ms input-latency target. Physical cable pull,
rotation, lock/unlock and genuine VideoToolbox invalidation require direct device
evidence; unit tests and synthetic disconnects do not prove them.
The phone's default VideoToolbox encoder selection also lacks positive hardware
metadata on this iOS runtime; high throughput does not formally prove its hardware
identity. The Mac explicitly requires hardware decoding.

## Release boundary

Local source builds and daemon deployment do not prove the relocated app bundle.
macOS signing, a clean-PATH packaged-app run, combined release archive assembly,
hosted CI and publication are separate release checks in
[RELEASING.md](docs/RELEASING.md). The existing explicit signing/push/merge
permissions remain in force.

Older RFB/WebKit validation and failures remain in the Git history and dated
investigation documents. They do not describe the native implementation.
