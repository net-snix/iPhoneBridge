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

Quit the benchmark app when finished, then open iPhoneBridge normally. Normal mode does not import the benchmark module, poll the test server, read canvas pixels, or send test keys. The benchmark is retained as a reproducible performance diagnostic, not a production telemetry loop.

# Performance review — 2026-09-07, evening

Full findings, measurements and rejected options: [docs/performance-review-2026-09-07.md](docs/performance-review-2026-09-07.md). Nothing in that pass lowers image quality.

- **Applied:** agent input actions (tap, drag, type, key, navigate) no longer transfer a full 11.9 MB RAW frame just to compare dimensions. `iphonebridge/control.py` checks the size with a 1×1 non-incremental request, saving about 420 ms per action while keeping the stale-dimension rejection and the lossless post-action screenshot. Verify with `.venv/bin/python scripts/measure-agent-path` against a running bridge.
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
