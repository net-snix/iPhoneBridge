# Native HEVC performance

Current implementation and qualification: [2026-09-13 native trials](docs/native-hevc-2026-09-13.md). The native path uses VideoToolbox HEVC encoding and direct hardware decode/display. Its evidence distinguishes delivered video, decoded barcode pixels, display submissions and physical scanout, including the limits of encoder hardware identification on iOS 15.1.1.

The material below records the earlier RFB/JPEG/WebKit implementation. Its performance numbers and tuning recommendations are historical, not native defaults.

# Sustained-speed repair: capture pacing — 2026-09-12

The long-session slowdown is resolved for phone-side delivery. Reducing the
previously recorded hardware counters to effective clock and IPC identifies the
mechanism: execution efficiency falls about 2.9x while work per update stays
flat, with the final warm window at 2.016 GHz and IPC 1.94 — the A15 efficiency
core's maximum clock and a 4-wide core's throughput. The one pipeline stage that
runs on fixed-function hardware, `IOSurfaceAcceleratorTransferSurface`, does not
slow at all (4.44 → 4.63 ms) while every CPU stage slows 2–3x, which excludes
memory bandwidth, I/O, contention and accumulated daemon state.

What the daemon controls is how much work it spends reaching that state. Capture
ran at the 60 fps ceiling regardless of what encoding could absorb, so about 20
captures per second were rendered, transferred and prepared purely to be
discarded — pure heat, feeding the loop that costs the threads their performance
cores. This is why added parallelism and raised QoS all failed: the binding
constraint is energy, not scheduling.

`src/CaptureGovernor.h` paces capture to the rate the pipeline actually
sustains, backing off immediately on evidence of over-drive and probing upward
only after a sustained clean period. On the unchanged heavy-motion fixture the
phone-only sink improved from **20.18 to 31.21 updates/s overall** and from
**18.7 to 29.8** in the final 30 seconds, with inter-update gap p95 falling from
**69.3 ms to 35.6 ms**. The confirmation run held **30.0 updates/s for five
consecutive minutes with no decline**, against a baseline that fell from 59 to
17 within two minutes.

Sustained 60 fps is not restored and is not reachable on this device with
full-resolution CPU JPEG; the repair converts an unstable collapse with erratic
timing into a stable, regular rate. Native visible speed, input latency and
memory behaviour are not qualified by these runs. See the
[cause, control law and measurements](docs/capture-pacing-2026-09-12.md).

# Renderer memory repair — 2026-09-09

The follow-up [renderer memory investigation](docs/renderer-memory-2026-09-09.md)
identifies WebKit's retained image-load URL history and describes the bounded
decoding-document repair. An earlier candidate completed 22 minutes and
950,716 image rectangles in a windowless memory test, with WebContent at
178–207 MiB. The current candidate draws native HTML images directly. Visible
physical-phone tests support bounded memory, but still reproduce a cadence
decline after the capture retry change. A
zero-size canvas invalidated earlier synthetic visible-speed comparisons;
their allocation measurements retain their recorded scope. The long-session
speed decline remains unresolved. Phone stage timing on 2026-09-10 showed
capture and encoding running serially while each stage remained below one
60 Hz frame interval. The bounded capture/encoding overlap candidate held
59.54 updates/s for five minutes in an initial phone-only sink, but its subsequent
native run fell from 55.27 to 33.89 visible fps with bounded memory. A warmed-phone
sink then also fell to 29–30 updates/s with the viewer disconnected. Native
rendering is therefore not required to reproduce the remaining slowdown.
The subsequent source probe kept capture/preparation near 60 fps while encode/send
time grew from 7.8 to 22.5 ms, with socket writes below 1 ms. Publication scheduling
added roughly 11 ms before the next send. The subsequent paired CPU probe
measured output-thread CPU rising from 7.5 to 21.0 ms per update, accounting for
94% of the final 22.3 ms send interval. Capture still ran near 60 fps, while
delivery ended near 30 fps. Requested output-thread QoS remained default and
the phone reported elevated thermal state; neither observation establishes
the active CPU core or frequency. A later exact JPEG probe measured compression
at 89% of warmed output-thread CPU: 18.3 ms per update out of 20.4 ms. Palette
analysis took 1.0 ms and other output work 1.2 ms. A checked interactive-QoS
trial still fell to 31 fps and was rejected. Parallel encoding remains an
unqualified experiment; see the [JPEG attribution](docs/jpeg-stage-timing-2026-09-10.json).

# Frame delivery repair — 2026-09-09

Version 0.2.1 repairs the lost final frame described below and a client-lock
cleanup race. It also gives viewer images explicit resource lifetimes and
recovers from decode failures instead of leaving the render queue stalled.
The 22-minute test verified final-frame delivery and responsive input, but
continuous-motion cadence still declined and renderer memory still grew. The
original long-session slowdown is not fully resolved.
See the [repair and sustained-use report](docs/performance-fix-2026-09-09.md)
and [numeric results](docs/performance-fix-2026-09-09.json) for the tested
artifacts, unchanged image settings, and remaining limits.

The following sections retain their original artifact scope and findings.

# Independent review — 2026-09-08

The [new independent pass](docs/performance-independent-2026-09-08.md) confirms
the size probe saves about **403 ms per agent action**, but finds an important
problem with the later `-Q 1` change: **three slow-reader tests lost the final
screen update**, verified against direct USB screenshots. Final-frame recovery
needs fixing before the frame-dropping optimization can be considered complete.

The installed native app delivered **50.7–53.7 fps on the heavy fixture**, with
input medians of **83.5 and 97 ms**. WebKit used roughly **one CPU core** during
a separate heavy-load profile. USB streaming measured about **45 MB/s** with
either tested cipher. PNG level 2 saved **17–37 ms of encoding** for about
**5–6% more bytes** on two fixed frames, with identical decoded pixels.

The report evaluates Fable's findings, distinguishes source estimates from live
measurements, and includes [raw numeric results](docs/performance-independent-2026-09-08.json).
This pass adds diagnostics and findings; it does not promote new runtime tuning.
The sections below retain the earlier measurements and their original scope.

# Mirror latency — 2026-09-07

The selected profile reduced input-to-visible response from about **166 ms to 83–98 ms** and increased delivered motion from **30 fps to about 55 fps** on the physical iPhone. Sparse animation delivered about **59 fps**. Full resolution and the existing Tight encoder quality/compression levels remain unchanged.

## Selected change

`iphonebridge/device-session.sh` now launches TrollVNC with `-F 60 -P 60 -d 0` at `-s 1`:

- Capture targets 60 fps.
- Exact dirty-tile comparisons produce changed-region updates; above 60% changed tiles the server can fall back to a full update.
- No extra dirty-region coalescing window is added.
- Blocking buffer swaps and the upstream two-encode limit remain in place. Every input test response, including the final change, was observed before the next input.

The viewer explicitly retains `qualityLevel=6` and `compressionLevel=2`. Reducing compression was rejected: LibVNCServer Tight changes its palette threshold from 96 colors to 24 at compression level 1, converting more regions to lossy JPEG even with the same JPEG quality setting. Compression level 0 is clamped to 1 when JPEG is enabled. [Pinned Tight implementation](https://github.com/LibVNC/libvncserver/blob/LibVNCServer-0.9.15/src/libvncserver/tight.c).

No new native binary patch, runtime replacement, resolution scaling, asynchronous tearing mode, audio feature, or shared USB tunnel change was needed.

## Measurements

Same iPhone14,2 / iOS 15.1.1, portrait framebuffer 1172×2536, actual AppKit/WKWebView app with pinned noVNC 1.7.0. Each input run used 30 successful responses; motion runs observed the same animated fixture for ten seconds. Numbers are rounded.

| Profile | Input median / p95 | Moving pattern | Phone daemon CPU during motion |
| --- | --- | --- | --- |
| Original: 30 fps, full updates | 166 / 182 ms | 30.1 fps | 70.5% of one core |
| 60 fps, full updates | 148 / 150 ms | 32.9 fps | 77.4% of one core |
| **60 fps, precise updates, no deferral** | **98 / 103 ms** | **54.5 fps** | **77.9% of one core** |
| Selected profile confirmation | 83 / 99 ms | — | — |

The selected profile also delivered 59.3 fps with sparse changes. Compared with baseline, the first matched selected run reduced median response by 41% and p95 by 43%, while delivering 81% more motion frames. The confirmation run was faster, illustrating normal phase variation. CPU rose about 7.4 percentage points of one core; daemon resident memory stayed near 82 MB. CPU samples are `ps` estimates (ten samples/profile), not battery or thermal endurance measurements.

Summary data: [docs/performance-2026-09-07.json](docs/performance-2026-09-07.json). Complete samples and timestamps remain in local ignored `work/latency-results.ndjson` and `work/*-cpu.txt`. The failed first 60 fps input attempt found no barcode during page navigation and sent no input; it is excluded from the comparison.

## What is measured

The opt-in viewer sends a Space key through the same live noVNC connection. The disposable page on the phone increments a visible binary barcode. The benchmark detects that increment in the rendered Mac framebuffer using the viewer's monotonic clock. This covers Mac input sending, USB transport, iOS event handling, page drawing, capture, encoding, return transport, decode and canvas drawing. It is not a CLI request-duration measurement or an estimate from clocks on different machines.

Canvas observation runs on `requestAnimationFrame`, with roughly 16.7 ms sampling granularity. It observes rendered canvas pixels, not physical monitor scanout. Barcode readback adds diagnostic overhead, present in both configurations; normal viewing does no readback. Animation counts distinct decoded sequence numbers and measures intervals between them. The test pattern is repeatable but is not a measurement of every game, app or video workload.

## Reproduce

Close the normal mirror first. Use the prepared environment and an awake, unlocked phone:

```sh
./bridge start
./bridge test-surface
open iPhoneBridge.app --args --benchmark
```

Open `http://127.0.0.1:15802/latency.html?mode=input` in phone Safari and wait for the barcode. Tap **Ready for keyboard test** if Safari needs focus, then:

```sh
./scripts/measure-mirror my-input-run --trials 30
```

For video cadence, open the same phone page with `mode=motion` or `mode=sparse`, wait for it to appear, then:

```sh
./scripts/measure-mirror my-motion-run --mode animation --duration-ms 10000
```

The fixture and metrics server bind to Mac loopback; the phone reaches the fixture through the existing bridge-owned USB reverse forward. Requests and reports stay in `work/`. The input runner requires a recognized barcode, rejects the animation flag, waits up to two seconds for a stable input fixture, releases each key after 20 ms, and aborts on an unexpected/missing response. It never retries an uncertain input. Animation measurement sends no input.

Keep the app window visible during speed checks. When explicitly wanted,
`--benchmark-visible` keeps the test window on top and
`--benchmark-display=<display ID>` places it on a connected CoreGraphics display
(for example, the ID reported by `system_profiler SPDisplaysDataType -json`).
Both options require `--benchmark`; an unavailable requested display stops the
launch. Ordinary launches retain the normal window level and placement.

Reports record hidden intervals; covered or minimized windows are not valid
samples of active mirror performance. Benchmark mode records completed
update/draw counts, image counts, connection continuity and decoding-frame count
every five seconds without reading canvas pixels between requested barcode tests.

Quit the benchmark app when finished, then open iPhoneBridge normally. Normal mode does not import the benchmark module, poll the test server, read canvas pixels, or send test keys. The benchmark is retained as a reproducible performance diagnostic, not a production telemetry loop.

# Performance review — 2026-09-07, evening

Full findings, measurements and rejected options: [docs/performance-review-2026-09-07.md](docs/performance-review-2026-09-07.md). Nothing in that pass lowers image quality.

- **Applied:** agent input actions (tap, drag, type, key, navigate) no longer transfer a full 11.9 MB RAW frame just to compare dimensions. `iphonebridge/control.py` checks the size with a 1×1 non-incremental request, keeping the stale-dimension rejection and the lossless post-action screenshot. Measured live: a no-input action dropped from 1209 ms to 801 ms (probe round trip 7.6 ms, full capture plus PNG 417 ms); reproduce with `.venv/bin/python scripts/measure-agent-path` against a running bridge.
- **Recommended next:** set `gScreen->deferUpdateTime = 1` in the TrollVNC patch to remove LibVNCServer's default 5 ms sleep before every update (about 4 ms per frame), poll with `sleep 0.2` in `device-session.sh` (about 0.8 s per quit/reconnect), and benchmark `-F 120` capture on this ProMotion phone.
- **Not bottlenecks:** websockify (sub-millisecond), the SSH cipher (`aes128-gcm` already), Python startup, and the Mac app itself. The USB tunnel measured about 40 MB/s, so bytes per frame remain the main structural limit.

## Standalone release follow-up

The source-built release daemon `7d8fac46…240fb2` and the installed app were
checked on the same phone. A corrected 1×1 probe preserves vncdotool's protocol
callback chain across subsequent input and capture calls. The live, no-input
comparison measured **793.9 ms** for probe plus post-action capture versus
**1198.4 ms** with the former full-frame precheck, a **404.5 ms** reduction in
that test. The warm probe median was 7.2 ms; a full PNG capture took 426.5 ms.
Both returned matching 1172×2536 dimensions. This measures the agent capture
path, not native input-to-visible latency or video frame rate.

[Raw release measurements](docs/release-agent-path-2026-09-07.json). Native video
performance figures earlier in this document retain their original artifact
scope; they were not relabeled as a fresh release-daemon benchmark.

# Applied — 2026-09-07, late evening

Daemon build `a82e40b4…` (patched `deferUpdateTime = 1`, launched with `-Q 1`) and the session script with 0.2 s polling replaced the release daemon `7d8fac46…`. Full details, including the game-load measurements that motivated the encoder-queue change, are in [docs/performance-review-2026-09-07.md](docs/performance-review-2026-09-07.md).

| Measurement | Before | After |
| --- | --- | --- |
| Daemon request round trip (1×1 update, warm connection) | 7.6 ms | 2.8 ms |
| Bridge `stop` / `connect` from the source checkout | ≥2 s / ≈5 s | 1 s / 4 s |
| Heavy full-screen load, input-to-visible p50 (Mac Safari viewer, 2 runs each) | 111.5 and 114 ms (`-Q 2`) | 101 and 98.5 ms (`-Q 1`) |
| Heavy full-screen load, delivered motion | 47.2 and 48.1 fps (`-Q 2`) | 44.4 and 44.9 fps (`-Q 1`) |

`-Q 1` drops a captured frame while an encode is still in flight instead of queueing it behind that encode, so each delivered frame is fresher; the cost is a few fps when the encoder is saturated. Under a 3D game the phone spends 40–55 ms encoding each full frame on one core, so that trade favours latency. The heavy-load numbers above come from the new `load=heavy` option of `fixtures/latency.html` viewed through Safari on the Mac rather than the app window, so they are comparable with each other but not with the earlier 83–98 ms app measurements.

Not changed: full resolution, Tight quality 6 with compression 2, and the lossless screenshot path. Lower JPEG quality was measured and rejected because it barely reduces encode time (49–58 ms per game frame across every quality level).
