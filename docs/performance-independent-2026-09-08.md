# Independent performance review — 2026-09-08

Fable's 1×1 size probe and shorter LibVNCServer update delay are worthwhile. The
broader conclusion that the remaining path is near its hardware limit is not
established. This pass found a reproducible stale-frame bug with the installed
`-Q 1` profile, substantial WebKit rendering cost, and a better PNG compression
tradeoff than the evening review considered.

This is a measurement and review pass. It adds reproducible diagnostics and
records findings; it does not change the installed capture profile or ship a new
daemon. The late-evening changes were already present when work resumed.

## Artifact and method

- Installed `/Applications/iPhoneBridge.app`, version 0.2.0; daemon SHA-256
  `a82e40b4b17171da54056fcd1cc60095b35002e0e408d32e6238ce80906e85af`.
- Live flags: `-s 1 -F 60 -P 60 -d 0 -Q 1`, blocking buffer swaps;
  patched `deferUpdateTime = 1`. Session script SHA-256
  `6c5d958ec632ae4d67f7fdcb53acd9a8eed7b61ba1361ef7e7ba740b1ea844d9`.
- iPhone14,2, iOS 15.1.1, RFB framebuffer 1172×2536. The separate USB screenshot
  service returns 1170×2532; barcode recognition accommodates each coordinate
  space, rather than comparing mismatched whole-image pixel arrays.
- MacBook Pro with M5 Pro, macOS 27.0 build 26A5425a. Python 3.13.11,
  Pillow 12.3.0, vncdotool 1.4.2, noVNC 1.7.0 at `63107bd0…`.
- Device source: TrollVNC `a3e40816…` with the project patch; source-built
  LibVNCServer `42494999…`, rather than assuming the plain 0.9.15 tag matches it.
- The installed and checkout copies of `control.py` and `latency.html` matched
  by SHA-256. Benchmarks used the installed AppKit/WKWebView viewer, with Tight
  quality 6 and compression 2 throughout. Normal viewing was restored afterward.

The phone was unlocked for the existing disposable Safari fixture. No phone app
was terminated. Only the bridge's owned session was restarted; shared USB
forwards were preserved. The initial disconnected session had to be cleaned up
with the normal ownership checks before reconnecting.

Machine-readable results: [performance-independent-2026-09-08.json](performance-independent-2026-09-08.json).
Private screenshots, complete canvas events, and Instruments traces remain in
ignored `work/perf-independent-2026-09-07/`; they are not release assets.

## First priority: final frames can be lost

**Reproduced in all three trials on the installed daemon.** The fixture was in
input mode with its background disabled. Alongside the viewer, a warm control
client stayed connected while an additional read-only RFB client requested a
full RAW frame and held its reader for 400 ms. While that update was in flight,
the warm client sent one Space to increment the visible counter. The slow
reader then drained and disconnected before verification. This intentionally
stresses backpressure; the ordinary timing runs below all completed.

| Trial | Before | Physical phone, direct USB screenshot | RFB screenshot | RFB after another second | After a new idle input |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 60 | 61 | 60 | Not sampled | 62 |
| 2 | 62 | 63 | 62 | 62 | 64 |
| 3 | 64 | 65 | 64 | 64 | 66 |

This distinguishes lost output from lost input: the phone accepted the input,
but a fresh non-incremental RFB screenshot returned the previous counter. The
test released every key and restored a current frame with a later, separately
verified input. It never retried an uncertain input.

The source explains the failure:

1. `ScreenCapturer` records `sDirtyFrameCount` before calling the frame handler.
2. `handleFramebuffer` returns immediately when `gInflight >= gMaxInflightUpdates`.
3. `displayFinishedHook` decrements the count without scheduling a replacement.
4. If the physical screen has stopped changing, the next capture sees the same
   dirty counter and skips it. Re-requesting an RFB frame reads the existing
   front buffer, so it cannot recover the missing screen change.

See the pinned [capture implementation](https://github.com/82Flex/TrollVNC/blob/a3e40816ea5b93a7c80c09625175893d15bd1070/src/ScreenCapturer.mm)
and [frame handler and display hooks](https://github.com/82Flex/TrollVNC/blob/a3e40816ea5b93a7c80c09625175893d15bd1070/src/trollvncserver.mm).

`-Q` counts active client update operations; it is not a queue of captured
frames. `-Q 1` makes one busy client enough to enter the drop path. The upstream
logic can also be reached at higher limits with multiple active clients. This
pass did not rerun `-Q 2` as a negative control, so it does not claim that the
older profile is universally immune.

**Recommendation:** preserve a pending final update when dropping work and
guarantee a later capture when encoding becomes available. Verify both slow
reader recovery and ordinary input/video performance. Treat this as a
correctness requirement before accepting frame dropping as a completed latency
optimization. More continuous-animation samples cannot test this failure mode.

Reproduce with the native benchmark fixture visibly in **input / background:
none** mode, then:

```sh
IPHONEBRIDGE_DATA_DIR="$HOME/Library/Application Support/iPhoneBridge" \
  .venv/bin/python scripts/measure-frame-retention \
  --output-dir work/retention-check \
  --data-dir "$HOME/Library/Application Support/iPhoneBridge" \
  --usb-tool /Applications/iPhoneBridge.app/Contents/Helpers/usb/bin/idevicescreenshot
```

The script recognizes the barcode before sending input, rejects animation mode,
checks the physical result independently, and stops if the outcome is uncertain.
It exits with status 1 when it detects a lost final frame.

## Fresh native-viewer measurements

Each input run completed 30/30 trials; each motion run observed ten seconds.
These are new measurements of the installed `a82e40b4…` daemon.

| Fixture | Run 1 input median / p95 | Run 2 input median / p95 | Run 1 motion | Run 2 motion |
| --- | --- | --- | ---: | ---: |
| Light moving pattern | 99 / 101 ms | 99 / 109.7 ms | 58.7 fps | 45.7 fps |
| Heavy background | 83.5 / 114.1 ms | 97 / 113.6 ms | 53.7 fps | 50.7 fps |

The variation is retained, including the slower light-motion run. These short
runs do not establish a sustained frame-rate floor or a thermal/battery result.
The similar input results across loads are also a reason not to infer an exact
per-frame latency benefit from a four-millisecond sleep reduction.

As in the original harness, timing runs from a noVNC key event to detection of
the matching barcode in the rendered Mac canvas. Observation is sampled on
`requestAnimationFrame`, generally about 16.7 ms apart. It includes diagnostic
canvas readback and excludes physical monitor scanout. Profiling and bulk
transfer tests were performed outside these timing runs.

Fable's late-evening heavy-load results used **Mac Safari**, whereas these use
**the native WKWebView app**. Their Q1/Q2 comparison is internally useful; its
absolute times and frame rates are not an A/B comparison against this table.
Neither fixture is a new measurement of Pokémon GO's frame rate.

## The Mac rendering process matters

A separate 20-second Time Profiler trace targeted the WebContent process created
by the benchmark app. Process identities were established across the app's quit
and relaunch, rather than attaching to an unrelated browser process.

During the heavy animation, 15 one-second `ps` samples had these median CPU
estimates, where 100% means one core:

| Process | CPU |
| --- | ---: |
| Swift/AppKit app | 3.0% |
| Its WebKit WebContent process | 101.4% |
| Its WebKit GPU service process, CPU usage | 21.1% |
| Its WebKit networking process | 14.3% |
| Phone daemon | 74.4% |

These are CPU estimates during profiling, not GPU utilization or power readings.
WebContent's trace contained 19.827 seconds of sampled running CPU time in the
20-second recording. Leaf sample shares included AppleJPEG **25.5%**,
JavaScriptCore **19.3%**, and memory copies **5.3%** for `_platform_memmove`.
Specific leaf costs included URL parsing **6.7%**, JavaScript rope-string
flattening **4.7%**, and native base64 decoding **3.1%**. These categories should
not be added to their containing library totals.

The draw-image path appeared in about 37% of inclusive samples. That includes
its callees, notably image decoding/copying; it is not another independent 37%
to add to the leaf costs.

Fable correctly identified the small Swift shell, but that does not rule out
the app's rendering pipeline. The actual WebKit profile is stronger evidence
than a V8 microbenchmark of JavaScript base64 encoding alone. A maintained
source change that avoids data URLs deserves testing before being dismissed;
its end-to-end benefit and color behavior still need measurement.

One source correction: noVNC creates an `Image` and assigns its URL immediately,
then queues drawing in order. This is not proof that every strip's decode waits
for the previous strip to finish, or that each strip requires a separate event
loop turn. See [pinned noVNC display code](https://github.com/novnc/noVNC/blob/63107bd06d9e1f6136ff21aeda8cd62cbf0d433e/core/display.js).

## USB and agent costs

Nine SSH transfers covered 8, 32 and 64 MiB, three times each. Delivered bytes
were counted, with session setup and arrival of the first 64 KiB recorded
separately. Compression was disabled by SSH negotiation.

- Default diagnostic sessions negotiated **ChaCha20-Poly1305**, not the AES-GCM
  identity stated in the earlier review. Their delivered stream rate after the
  first 64 KiB was **44.99 MB/s median**, range **44.96–45.17 MB/s**.
- Repeating the nine transfers with AES-128-GCM produced **44.97 MB/s median**,
  range **44.69–45.12 MB/s**. There is no useful cipher improvement in this test.
- Whole-session 32 MiB transfers took **1.023–1.030 s**, or **32.6–32.8 MB/s**
  including startup. An independent `ssh true` took **228 ms median**.

The earlier “32 MiB in 1.01 seconds, about 40 MB/s” mixes whole-session timing
with a setup-subtracted estimate. Its order of magnitude is reasonable, but it
is not a directly measured stream rate or a hardware ceiling. At the observed
45 MB/s route rate, a hypothetical 400 KB frame at 60 fps consumes 24 MB/s, about
53% of the measured throughput. Actual encoded bytes per frame still matter.

New agent measurements:

| Measurement | Result | Scope |
| --- | ---: | --- |
| First 1×1 probe | 113.7 ms median | Five fresh RFB connections; lazy connection included |
| Warm 1×1 probe | 2.33 ms median | Fifty requests |
| Full RAW refresh and image assembly | 272.8 ms median | Five requests, static fixture |
| PNG level 6 encoding alone | 57.8 ms median | Same static fixture |
| No-input action, probe precheck | 765 ms median | Three calls, Home Screen, includes 250 ms settle |
| No-input action, former full-frame precheck | 1168 ms median | Three calls, same screen |
| Screenshot API | 507 ms median | Three calls, Home Screen |
| Installed MCP screenshot tool round trip | 446.2 ms median | Five calls, static fixture |
| MCP client's PNG decode | 9.3 ms median | Additional to the tool round trip |
| MCP server start and initialization | 988 ms | One fresh server process; not repeated per call |

The size probe saves **403 ms** in the new no-input comparison, confirming the
earlier roughly 408 ms result. The warm probe also supports the value of the
1 ms daemon delay. However, a first probe still costs about **111 ms more** than
a warm one, so connection overhead remains material for agents.

The built-in WebSocket detector waits 100 ms before a plain RFB greeting.
This bridge already terminates browser WebSockets at websockify, so disabling
unused daemon-side WebSocket support is a concrete build candidate. The pinned
[detection code](https://github.com/LibVNC/libvncserver/blob/42494999e6492aaab9c1db785ecd293ef10b3aed/src/libvncserver/websockets.c)
and [CMake option](https://github.com/LibVNC/libvncserver/blob/42494999e6492aaab9c1db785ecd293ef10b3aed/CMakeLists.txt)
support that proposal. It was not rebuilt or promoted in this pass.

A persistent MCP connection is another option, not an automatic choice. Fable
is right that a remaining client keeps capture enabled after the viewer closes.
But while the viewer is already connected, a second idle client does not start
an additional capture loop; unchanged displays also skip rendering. A bounded
idle timeout could preserve most setup savings without an indefinitely live
client. Its concurrency, recovery, and idle-power behavior need validation.

## Reconsider PNG level 2

Each sweep used one fixed RGB frame, five samples per level in rotating order.
Every decoded candidate was compared byte-for-byte with the same RGB pixels.
All matched. PNG compression changes compression effort, not image fidelity;
Pillow documents the [compression-level setting](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#png).

| Fixed frame | Level 6 encode / size | Level 2 encode / size | Encoding saved | Size increase |
| --- | --- | --- | ---: | ---: |
| Home Screen | 100.1 ms / 3.850 MB | 63.4 ms / 4.067 MB | 36.8 ms | 5.7% |
| Heavy fixture | 55.3 ms / 1.787 MB | 38.7 ms / 1.879 MB | 16.6 ms | 5.1% |

Measured base64-plus-JSON serialization and PNG decode overhead did not consume
those savings. For the Home Screen they were 6.8 / 23.4 ms at level 6 versus
6.8 / 24.6 ms at level 2. The heavy fixture was 3.5 / 15.1 ms versus
3.7 / 15.8 ms. This includes local processing, not the application's eventual
upload to a model service.

Rejecting level 1 because it makes a much larger file does not establish that
levels 2 or 3 are poor choices. Level 2 merits an actual CLI/MCP A/B test across
more screens. These sweeps alone do not claim a shipped end-to-end improvement.

## Other findings and priorities

1. **Fix final-frame delivery first.** Keep the verified slow-reader regression
   and require the final counter after animation stops, not just successful
   responses while a background continues repainting.
2. **Remove redundant full hashing.** With `-d 0`, `handleFramebuffer` performs
   a full serial tile hash and then recomputes full hashes at flush, usually in
   parallel, without changing the back buffer between them. One redundant scan
   traverses about 11.9 MB per accepted frame. The pinned source confirms this;
   its device-time saving is not measured here. Preserve exact dirty-region and
   final-update behavior when consolidating the passes.
3. **Qualify PNG level 2 and removal of unused WebSocket sniffing.** Both have
   concrete measured costs and can preserve resolution and displayed pixels.
4. **Measure viewer changes in WKWebView.** Its JPEG/data-URL/image handling is
   significant. Keep changes in maintained source, with pinned provenance,
   rather than overriding private methods at runtime.
5. **Keep larger encoder work separate.** Fable's full-frame request timing is
   useful, but “time to last byte” includes waiting, encoding, transfer and
   overlap between them. Subtracting estimated USB time does not isolate
   40–55 ms of encoder CPU. Parallel Tight encoding merits investigation; a
   hardware H.264 path changes the image representation and needs a new quality
   contract. noVNC advertises H.264 only when its browser capability check passes,
   not unconditionally.
6. **Retain websockify unless new evidence points to it.** This pass did not
   repeat Fable's synthetic proxy benchmark. Its reported small local hop cost
   is a reasonable basis for lower priority, not proof about every load.

The 0.2-second session polling change is sensible, but its exact stop/reconnect
savings were not independently A/B tested here. Likewise, `-F 120` remains an
experiment: shorter capture phase can help, while additional copy/hash/encode
work can hurt. Neither is a substitute for the correctness fix above.

The earlier Swift-only trace was captured on the previous release daemon on
September 7 and is not included in the current performance headline. No fresh
Pokémon GO throughput, long thermal run, or Q1/Q2 matched native-app A/B is
claimed by this report.

## Reproduce the read-only timings

```sh
export IPHONEBRIDGE_DATA_DIR="$HOME/Library/Application Support/iPhoneBridge"
.venv/bin/python scripts/measure-performance probe --label current
.venv/bin/python scripts/measure-performance capture --label current
.venv/bin/python scripts/measure-performance png --label current
.venv/bin/python scripts/measure-performance usb --label current
.venv/bin/python scripts/measure-performance usb --label aes-comparison \
  --cipher aes128-gcm@openssh.com
.venv/bin/python scripts/measure-performance mcp --label current \
  --command /Applications/iPhoneBridge.app/Contents/Helpers/bridge
.venv/bin/python scripts/measure-agent-path
```

Use the existing `scripts/measure-mirror` instructions for the opt-in native
viewer benchmark. Keep the fixture visible and run profiling and bulk transfers
separately. Run the normal app again after diagnostics.
