# Native HEVC qualification — 2026-09-13

Status: sustained stream and visible endurance gates passed; input latency,
physical-device and packaged-release gates remain open. This record includes
failed candidates and measurement limits. It is not release approval. The original governor checkout
and its daemon remain available for rollback; the task uses an isolated branch.

## Baseline correction

The initial 300-second governor runs used motion **without** the heavy background,
confirmed by the visible fixture label and the fixture's source. Their cold/warm
complete-update rates were 59.25 and 48.05 updates/s. They do not reproduce the
handoff's heavy-motion baseline and must not be used as that comparison.

The corrected 300-second governor run, with **Background: heavy** visually
verified, delivered 8,993 complete updates: **29.98 updates/s**, gap p95 39.87 ms.
This reproduces the handoff's approximately 30 fps warm heavy-workload ceiling.

The repeated hardware-only probe delivered 59.17 fps with no encode errors. Its
thermal state started at 2 after the JPEG runs and fell to 1. Its 476-frame HEVC
dump decoded without errors. This run also used the lighter motion fixture, and
therefore does not reproduce the historical thermal-state-0 heavy-motion result.

The existing signed governor app completed 30 input trials on the disposable
input fixture: median 100 ms, p95 100 ms. Window visibility was verified and the
counter was read from canvas pixels. This measures input-to-canvas observation,
not physical monitor scanout.

## Candidate 1

Phone SHA-256:
`f7b3ecb13346a143041f9e912163e2224a897fd431c475113c038eb11ceb0700`.
The new daemon completed the IPBM/1 handshake and returned lossless 1170×2532
screenshots. A 10-second light-motion sample contained 571 frames and decoded
without ffmpeg errors.

After enabling and visually verifying **Background: heavy**, the first
300-second run delivered 16,353 complete frames, **54.51 fps**. Per-minute rates
were 54.27, 53.30, 54.53, 55.27 and 55.18 fps. Thermal state was 0–1, and no
encoder errors occurred. The 58 fps throughput gate failed. The immediate warm follow-up delivered **54.73 fps**, thermal state 1 throughout,
also failing the throughput gate. Both complete streams decoded without ffmpeg
errors. A per-callback timing gate discarded early arrivals after jitter; the next
candidate retains early requests behind one deadline wake.

The host receiver verifies complete framing, generation changes, parameter sets,
monotonic timestamps and keyframes. Independent ffmpeg decoding is a separate
check. Neither metric proves display presentation or phone-to-monitor latency.

## Functional checks on candidate 1

Nine live session checks passed: HELLO/geometry, input contention, stale-generation
rejection, explicit ownership handoff, lease release on TCP close, fresh video
join, competing subscriber rejection, forced IDR and video reconnect. These checks
do not synthesize accepted touches/keys, physical cable pulls or phone lock events.
The live stdio MCP check found the six expected tools and validated a native PNG.

A 20-second static fixture run delivered one initial frame and no further video.
Native before/after stills were pixel-identical. The independent USB screenshot
was tagged Display P3, while native capture used sRGB and returned an untagged PNG.
Direct values differed by up to 77; after P3-to-sRGB conversion the maximum channel
difference was 4. This is a failed pixel-exact comparison, not HEVC compression in
the still path. The colour investigation is recorded below.

## Candidate 2 — capture deadline

Phone SHA-256:
`866a3e3610df1e8834b4ede94decae81df30ef62804d99ae583eea99061ca4e2`.
One deadline timer preserves early display requests, enforces the configured
minimum interval and rejects stale timer generations on stop/restart. Deterministic
60/120 Hz jitter, late-timer and restart tests pass.

Candidate 2 completed 17,825 frames in 300 seconds: **59.42 fps**, with all
per-minute rates 59.32–59.47 fps. Thermal state was 0–1, no encode errors occurred,
and the complete stream decoded without ffmpeg errors. This passes the stream
throughput/thermal gate for that run; an immediate same-candidate warm follow-up
was deferred while investigating screenshot colour.

## Colour probes and retained capture limit

Two isolated, nonshipping probes were measured on `fixtures/colour.html`, including
explicit Display P3 and sRGB primaries, a grey ramp, text and sharp edges:

- `dbbcd554d64e45bff8d0e0e00ae419704e667b62699a88a7ac33c52d3747037d`
  changed only source/destination surface declarations from sRGB to Display P3.
  Five native captures were identical, as were the independent USB captures before
  and after them. Native pixels still represented sRGB; maximum raw difference
  from USB's P3 values was 117. Both sets of primary-colour swatches collapsed to
  full sRGB primaries in the native output.
- `97d6a7bab2a50230bac3dc0f5ba5585d42a0e7a0e01c207a94e181b0b01e5233`
  additionally bypassed the hardware transfer using a locked, bounds-checked
  source-row copy. The same sRGB clipping was present in the rendered source.
  The transfer step therefore does not cause this gamut loss.

The retained upstream implementation explicitly identifies this rendering path
as sRGB. A P3 tag cannot restore lost gamut. Production keeps the proven sRGB
capture and transfer, declares sRGB in lossless PNGs, and uses explicit BT.709
primaries / sRGB transfer / BT.709 matrix for HEVC (1/13/1). Neither isolated probe
is part of the production source. Exact P3/system screenshot parity remains
**unpassed**; this limitation is documented in usage and release qualification.

A native preview window was visually verified showing this colour fixture through
hardware HEVC decode. Its executable was copied into an unsigned local preview
bundle with the existing bundle identifier; no macOS signing or installed-app
replacement was performed.

## Candidate 3 — colour contract and foreground cadence

Phone SHA-256:
`b47f70a26791c08af9170cdc0d959e5bf390d6c163796a01e887d1e8ab5f453d`.
An initial heavy run delivered only 29.97 fps. Both this build and the prior
866a build stayed near 30 fps in the same phone state, including after reloading
the fixture. Offline barcode counts confirmed the source animation itself had
slowed. Home followed by reopening Safari restored 59 fps on unchanged b47f.
This was a source-cadence issue, not evidence of a colour-tagging regression.

The subsequent five-minute heavy run and its immediate five-minute warm follow-up
each delivered 17,812 frames: **59.373 fps**. Thermal states were 0–1 and 1,
respectively. Both entire streams decoded with zero ffmpeg errors; encoder and
colour errors stayed zero. This passes the 300-second stream/thermal gate.

## Native Mac endurance and input

Two 22-minute attempts were interrupted and remain unqualified:

- The first decoded and submitted approximately 59 frames/s, but detected no
  barcodes. The scanner incorrectly rejected one- and two-pixel colour-transition
  fragments from HEVC. A bounded tolerance fixes those edges while retaining
  complete header/data/footer validation. All 590 frames of the recorded clip
  then decoded and produced valid barcodes.
- The second observed 630 seconds in complete windows over 672.9 seconds wall
  time. One window fell to **54.27 fps**, below the 55 fps threshold; most others
  were near 59.4 fps. Peak RSS was **116.84 MiB**, with no median growth. A decoder
  recovery edge could suppress a replacement keyframe request if the requested
  IDR arrived while ingress was full. Capacity-aware, bounded retries and
  ACK-in-flight renewal are now tested. Whether that edge caused the measured
  window dip is unproven; new diagnostics separate misses from duplicate frames.

The first 30 native input trials completed at **108.94 ms median** decoded response,
**109.04 ms** display submission, and **116.59 ms** decoded p95. This fails the
60 ms target. The old canvas's 100 ms median uses coarser animation-tick observation,
so it cannot isolate the exact pipeline regression. Separate USB control PINGs
had a 1.42 ms median, narrowing the delay beyond ordinary command transport.

Native tap, slider drag, ASCII typing, backspace/left-arrow editing, drag-to-scroll,
Home/App Switcher shortcuts and fullscreen were visibly exercised. A control
connection closed while holding a touch caused a visible pointer-up/click and
released the lease. Closing another while holding Shift produced a visible
`keyup Shift`. These are actual held-input TCP recovery checks, not cable-pull tests.
The native MCP tool list and lossless PNG check passed again on b47f.

## Performance instrumentation and remaining work

Phone candidate
`9fb24cad0553c63a273f3cc426c77f432c572e6c8b2b95b3543d4f12b286578a`
adds fixed-capacity input/frame timestamp histories to GET_STATS. It preserves
the preceding encode/capture policy and allocates diagnostic JSON only when
requested. The Mac now records recovery/barcode scan counters and restores the
old mouse-wheel/trackpad gesture path. Exact phone PTS values join decoded and
display-submission events to the relevant phone frame, rather than assuming the
first capture after input already contains its response.

## Performance iterations

Direct-render candidate
`cc2ff58291a2b64ed23f114f2d19b5582b959255c7a8a11b7b6dd5c6f0d7c39e`
renders into the already leased encoder surface, removing the intermediate
surface and hardware transfer. Two fresh-session, 30-trial runs on the unchanged
static page and same Mac executable measured capture-to-output enqueue medians
of **36.561 → 32.580 ms**, and end-to-end medians of **106.171 → 102.570 ms**.
Payload size and Mac decode time stayed comparable. Earlier unmatched runs had
different encoder histories and a large payload regression; those failed
observations remain preserved, rather than being averaged into this comparison.

Static fixture pixels below the system status bar matched exactly between paths,
including every BGRA channel and opaque alpha. Full screenshots differed in the
clock's minute digit. CPU-read stills alone cannot establish GPU synchronization;
independent stream decoding and sustained motion remain separate checks.

Idle-reference candidate
`f54afcea469950c1549454abd08cb17156e4bf90c9d961c56ef975b9567cdc7d`
removes only the policy forcing a keyframe after a 100 ms idle gap. Join, explicit
request, congestion, encoder error and generation recovery still force IDRs.
Thirty matched responses changed from forced IDRs to unforced P-frames. Total
response bytes fell **59.94%**; median payload fell **156,183 → 36,967 bytes**,
although candidate payloads were more variable and its p95 payload was larger.
Median decode response was **99.878 → 96.893 ms**, with p95 **116.350 → 110.570 ms**.
Pre-capture waiting also varied, so this entire latency difference cannot be
attributed to the policy. Excluding phone receive-to-capture waiting, the median
remaining path improved **43.569 → 41.149 ms**. The 60 ms total target still fails.

A separate 20-second idle-chain recording delivered one initial IDR and exactly
three P-frames for three Space presses, followed by no further video. Full ffmpeg
decode passed. The native hardware decoder independently decoded all four frames
with zero errors and barcode sequences **60, 61, 62, 63**, preserving colour 1/13/1.
This proves reference continuity through those idle periods, not a physical
disconnect or media-service invalidation.

The same f54 candidate then completed two consecutive five-minute heavy-motion
recordings at **59.127 fps** (17,738 frames) and **59.100 fps** (17,730 frames).
The phone was already warm from prior work. Thermal state stayed **0** throughout
both runs, and sampled encoder, colour-error and submission-skip counters did not
increase. Each entire recording decoded with ffmpeg exit zero and empty error
output. This passes the sustained stream/thermal gate for f54; it does not replace
the separate visible Mac endurance test.

On the Mac, filtered Time Profiler samples identified repeated image-format
creation and IOSurface attachment reads. An initial compatibility-checking cache
shifted the work into the matcher: format samples **170 → 167** in the matched
pair, so no material reduction was established. Its successor relies on sample
creation's documented compatibility validation and refreshes a cached description
once only on `kCMSampleBufferError_InvalidMediaFormat`; other failures propagate.
That revision also failed to reduce the measured work: format/sample preparation
samples were **176 → 180**, with main-thread samples **492 → 489**. The same
IOSurface metadata reads moved into sample creation. Both cache candidates were
discarded; correctness tests alone do not establish a performance benefit. These
are filtered sampling counts, not measured CPU milliseconds.

A separate phone identity experiment, `8a15cad217c2c6af1138cfc43a288cf3213bb21f1367725c120f725ada25e447`,
preserved default encoder selection but required positive hardware metadata from
the selected encoder's list entry. The live device returned encoder ID
`com.apple.videotoolbox.videoencoder.hevc` and omitted the Boolean hardware flag.
The experimental guard therefore rejected setup, correctly reporting
`unverified`. The experiment was discarded and the exact f54 source and binary
restored. Direct hardware requirement/readback keys require newer iOS; optional
metadata absence and high throughput do not formally verify hardware use on this
iOS 15.1.1 runtime. The recorded failure remains explicit qualification evidence.

The retained Mac candidate, `595bc42b61deb727c8aa096d9f6117ac3977c6190e76120ae18487b962df4211`,
requests 8-bit video-range bi-planar YUV (`420v` / NV12) from the hardware decoder.
The decoded buffer and its colour attachments reach the display layer directly;
the optional barcode scanner converts only the sampled points. The previous
BGRA path converted every complete frame before display.

A matched 30-second heavy-fixture Time Profiler pair on unchanged f54 measured
**1,889 → 1,527** running timer samples (**−19.16%**), with main-thread samples
**587 → 457** (**−22.15%**). This is one app-process comparison, excluding the
decoder XPC service and WindowServer; counts are not CPU milliseconds or a total
system saving. Separate visible barcode observations delivered **59.072 →
59.121 fps**, with no barcode misses, ingress drops or recovery requests.
Four decoded IOSurface mappings fell from **48,103,424 to 18,612,224 bytes**
(**−61.31%**); mapped storage is not private resident memory.

Both decoders processed the same static colour stream and motion/idle reference
clips without errors. Relative to the captured sRGB source, static content mean
absolute error improved **1.074 → 0.637** and PSNR **35.34 → 37.77 dB**. Flat
primary errors stayed within one level. Chroma interpolation differs across a
two-pixel colour boundary, so the paths are not pixel-identical; there is no
additional chroma subsampling. These quantitative PNG exports do not establish
the display layer's exact interpolation. A separate live preview inspection
confirmed clear text, primary swatches, grey ramp and thin lines, without an
obvious colour-range regression. The existing P3 capture limit remains.

The final NV12/f54 input run completed all 30 trials with **93.709 ms median**
decoded response and **135.766 ms p95**; display submission was **93.887 /
135.948 ms**. The first three responses took 133–146 ms. There were no barcode
misses, ingress drops or recovery requests. This is a final-candidate observation,
not a matched latency comparison or a pass of the 60 ms target.

The complete visible NV12/f54 endurance run then passed all **44 × 30-second
windows**, giving **1,320 seconds of observation** over 1,365.18 seconds wall time.
Every decoded and display-submitted window exceeded 55 fps; decoded whole-window
rates ranged **58.33–59.30 fps**. Peak RSS was **118.516 MiB**, and the baseline /
final one-minute medians were **115.750 / 115.516 MiB**. All 273 renderer-health
reports passed continuity checks. Across those reports there were no barcode
misses, ingress drops or recovery requests, and 14 duplicate barcode observations.
These duplicates are retained in diagnostics and do not inflate distinct-frame
rates. Phone thermal state was 0 and encoder errors were zero at the final sample.
The Mac preview was then restarted normally without benchmark instrumentation,
visibly reconnected, and returned to the static input fixture.

All nine session/ownership checks and the six-tool MCP discovery plus native PNG
check passed again on f54 after endurance. The checks use controlled TCP sockets;
they do not replace the physical-device gates below.

Native wheel scrolling reached the disposable fixture's bottom marker, its
Back to top control restored scroll position zero, and `ABC`, Left, Delete
produced `AB`. This used synthesized wheel input; a physical trackpad was not tested.

Exact Display P3/system screenshot parity remains unpassed. Physical rotation,
cable pull, lock/unlock and genuine VideoToolbox invalidation still require device
evidence. macOS signing, relocated packaged-app validation, hosted CI and
publication retain the explicit release boundaries in [RELEASING.md](RELEASING.md).

Raw reports and video are local under `work/native-hevc-qualification/`; the
adjacent JSON retains hashes without device identifiers or private captures.
Historical baselines live in the original checkout's corresponding directory.

## Committed source and rebuild checks

The native refactor was committed as `4d616d1`. The final device source package
staged and rebuilt offline successfully, and the 158-file Mac dependency-source
inventory passed its current runtime/source preflight. The initial committed
phone rebuild (`458b7257…`) and offline rebuild have different whole-file hashes
from the endurance-tested f54 artifact. Executable sections and initialized data
match; the UUID and corresponding signature hashes account for the differences.
This is source and binary-content evidence, not a second endurance measurement
on those rebuilt file identities. The normal preview remains on the tested
NV12/f54 combination. macOS bundle signing and public release remain pending.
