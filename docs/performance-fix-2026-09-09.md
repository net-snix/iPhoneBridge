# Frame delivery and rendering repair — 2026-09-09

The lost-final-frame bug and client-cleanup race are repaired. The retained
decoder also releases image resources and recovers from decode failures. A
22-minute run verified responsive input and correct final frames, but sustained
motion still slowed and renderer memory grew. This is a verified correctness
repair, not a complete cure for the reported long-session slowdown.

| Retained implementation | Start | After 22 minutes |
| --- | ---: | ---: |
| Input median / p95, 30 successful trials each | 92 / 109 ms | 80 / 84.6 ms |
| Delivered heavy-motion frames | 56.2 fps | 38.1 fps |
| Renderer physical footprint | 276 MiB | 821 MiB |

The original installed app reproduced a lost final frame and delivered 52.8 fps
and 96 / 99.6 ms input median / p95 in the short baseline. The new daemon matched
independent USB captures in all three checks before and all three checks after
the retained decoder's long run. Full numeric records and exact artifact hashes
are in [the accompanying data](performance-fix-2026-09-09.json).

## Changes

The single-encode profile could consume a new screen's dirty counter while the
encoder was busy, drop that capture, and never send the final state after motion
stopped. The patched daemon records that a capture was dropped. When an encode
finishes, it schedules a fresh capture on the next display tick, including when
the screen dirty counter has not changed. It keeps only a pending flag; it does
not retain or queue old image buffers. The retry and force-capture state is
atomic across the capture and encoding threads.

Frame swaps also used separate live client-list enumerations to acquire and
release client send locks. A client closing between those enumerations could
be excluded from the unlock pass, blocking its cleanup. A scoped owner now
retains the exact clients whose locks it acquired, unlocks those clients, then
releases their references. The same ownership applies to full-frame, dirty-tile
and rotation swaps.

The normal capture path now hashes the framebuffer once. The optional sparse
comparison path retains the second hash when it is needed for correctness.

The noVNC patch loads encoded bytes through a temporary HTML image's object URL,
then creates an ImageBitmap from that image. Once conversion completes, it
releases the HTML image and revokes the URL; the bitmap is closed after drawing.
This avoids per-frame base64 data URLs and gives decoded frames an explicit
lifetime. It adds image failure, dimension-mismatch and ten-second timeout
handling. A failed render rejects the pending flush and ends the RFB session so
the app can reconnect.
Disconnect disposes pending image resources and both session-owned canvas
backing stores. Normal successful drawing also releases each image's resources.

Full resolution, Tight quality 6, compression 2, capture/publish 60 fps, exact
dirty tiles and the single-encode limit are preserved. There is no periodic
reload, forced garbage collection, codec replacement or quality reduction.

## Verification scope

Tests use the installed AppKit/WKWebView application and the physical
iPhone14,2 on iOS 15.1.1, with a 1172×2536 portrait framebuffer. The Mac runs
macOS 27.0 build 26A5425a and WebKit 22625.1.29.11.26. This is a single-device
qualification, not a claim that every app or OS version has identical behavior.

The disposable Safari fixture measures input to the rendered Mac canvas and
counts distinct delivered motion barcodes. Canvas readback is enabled only by
the explicit benchmark launch argument. Process samples use physical footprint
for the app's verified WebKit process coalition, rather than treating RSS as
private memory or assuming all WebKit processes belong to the mirror.

The retained decoder's endurance session starts with consecutive thirty-second
motion measurements. It then keeps the same native process and heavy phone
animation running while sampling process footprint every thirty seconds.
Further motion diagnostics run at five, ten, fifteen and 21.5 minutes. The
intervening periods do no canvas
readback. This distinguishes normal viewing from diagnostic overhead without
restarting the app, reconnecting, changing the phone fixture or lowering image
settings. Each fresh diagnostic reads one full framebuffer to locate the barcode,
then one row per animation tick; these allocations are not normal viewer work.

The previously installed daemon `a82e40b4…` reproduced a missing final screen:
the independent USB screenshot showed counter 1 while RFB still showed 0 after
one second. Another input advanced both to 2. Three trials with the repaired
daemon `f3054357…` matched USB on the first check and after the same wait. Fifty clients
were also disconnected partway through a full RAW update; the daemon remained
at ten threads, with resident memory changing from 82,416 to 83,216 KiB, and
accepted a fresh client afterward.

Regression coverage exercises capture completion races, including 2,000
concurrent interleavings, client disappearance while locks are held, exact
reference and unlock ordering, render-queue failure and decoder disposal.
Source preparation verifies the upstream commits, patch bytes and each modified
source file before building or bundling.
All 70 Python/C++ tests and 48 viewer tests pass. Four native pixel comparisons
match exactly, including full-resolution JPEG/PNG and transparent/composited
PNG using identical encoded bytes through the original and retained decoders.
The final installed app also passed its bundle self-test, signature and served
source checks, native Home control and normal mirroring with the fixture stopped.

## Decoder alternatives

Four alternatives were evaluated and rejected. HTML images backed by Blob URLs
passed exact pixel comparisons but showed falling cadence and increasing
renderer footprint during approximately twelve minutes of heavy motion.
`createImageBitmap(Blob)` with explicit `close()` also passed the pixel checks,
but delivered about 36–37 fps and increased NetworkProcess footprint to about
498 MiB during a 7.7-minute run. Those results did not justify changing the
decoder. Retaining data URLs while adding error handling also failed: the
renderer grew from 2.05 to 6.60 GiB across six thirty-second windows, while
delivering 49.7–53.0 fps. Its memory map showed that the growth was overwhelmingly
in allocated WebKit memory. None of these alternatives is included in the
installed repair.

Reusing a bounded pool of four cleared HTML image elements also completed 22
minutes. Its cadence fell from 57.2 to 30.1 fps and renderer footprint rose from
288 to 722 MiB. Input remained responsive at 94 / 109.1 ms initially and
82 / 85 ms afterward. Lower memory did not justify its worse final cadence, so
element reuse was removed. These sequential runs do not isolate thermal or
other time-dependent system effects; they provide no evidence to promote reuse.

The pool experiment's 15-minute diagnostic could not recognize a barcode. Both
the phone and native mirror were visibly active afterward, and a repeat near
17.6 minutes measured 41.7 fps without a restart, reconnect or phone input.
The saved phone screenshot and 22,002 synthetic counter/flag cases passed the
locator. The failure-time native canvas was not captured, so the recognition
failure remains unexplained. The failed sample is retained in the data and is
not interpreted as zero rendered frames.

The installed WebKit reported `ImageDecoder` as unavailable, so no WebCodecs
decoder, private feature flag or operating-system change was introduced.

These observations establish measured regressions, not a proven WebKit leak in
the installed OS. The user's reported slowdown also persisted after an app
restart, so renderer memory alone has not been established as its full cause.

## Reproduction

Follow the disposable fixture setup in [PERFORMANCE.md](../PERFORMANCE.md).
Run the explicit `measure-mirror --mode image-quality` diagnostic for identical
encoded-byte pixel comparisons, input trials before and after the endurance
run, and animation windows while retaining process-footprint samples.
Use `scripts/measure-frame-retention` with an independent `idevicescreenshot`
binary to check final-state delivery under a slow RAW reader.

The fixture, raw process records and screenshots remain in ignored local work
directories. Shareable numeric results accompany this report; they omit device
identifiers and private screen contents.
