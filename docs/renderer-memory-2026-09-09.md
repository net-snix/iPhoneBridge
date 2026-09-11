# Renderer memory investigation — 2026-09-09

The investigation reproduced native image-load bookkeeping growth in WebKit.
Releasing HTML images, revoking Blob URLs and closing ImageBitmaps does not
release the page's history of image URLs. Each changing JPEG rectangle adds a
unique URL to that history.

The candidate gives decoding its own document and a bounded lifetime. The
document admits at most 4,096 image jobs and is removed after every admitted
image has been drawn or disposed. Waiting jobs then use a fresh document. Only
one decoding document is attached at a time; the main page, framebuffer and
VNC connection remain continuous. The current candidate draws the decoded
HTMLImageElement directly, retaining its Blob URL and document lease until
drawing or disposal. It removes the earlier intermediate ImageBitmap conversion.

**WebKit memory qualification passed; sustained FPS remains unresolved.** On
September 11, the current direct-image renderer (`c501684e…`) completed a 22-minute visible
physical-phone run. WebContent stayed at 106–123 MiB, and all four native pixel
comparisons matched before and after collection. It also passes 74 Node tests.
The native app itself grew 3.14 MiB during collection; that smaller drift remains
unexplained. Input, staged-daemon lifecycle and sustained-speed qualification
remain incomplete, so the complete candidate is not qualified for promotion.

Separate phone-only runs reproduced the FPS decline without Mac rendering.
The latest candidate moved publication outside the main thread, but JPEG CPU
time still rose from 8.68 to 35.41 ms per update while delivery fell from 59.53
to 27.42 updates/s. Subsequent sparse worker counters show rising CPU cost per
instruction during a warm slowdown, with unchanged sampled worker policy.
A bounded four-worker experiment and a two-worker priority experiment also
failed their warm sustained-speed tests. The measurements below
distinguish each tested artifact and its limits.
Earlier synthetic replays had a zero-size canvas and cannot qualify displayed
speed.

The first installed capture candidate (`8c061f12`) delivered 54.75 updates/s in a
60-second phone-only Tight sink, then 53.97 updates/s over five minutes. Its
minute means were 54.75, 54.80, 54.18, 53.05 and 53.05 updates/s. This removes
the observed 30 fps collapse in that test, but initial delivery is below the old
cold 60 fps result. The subsequent timer-precision candidate failed its sustained
tests below. These measurements discard encoded bytes and do not qualify native
display speed, input latency or pixel quality.

## Current capture investigation — 2026-09-10

The latest physical test held WebContent at 96–120 MiB while cadence declined.
A separate 600-second Tight sink delivered 20,828 updates and logged 15,153
captures rejected by the phone's busy gate: together, 59.97 capture outcomes/s.
The immediate cadence loss is upstream of the Mac renderer; the numeric evidence
is preserved in [renderer-cadence-2026-09-10.json](renderer-cadence-2026-09-10.json).

The tested early-admission scheduler checks capacity before copying the screen and coalesces
an asynchronous retry when encoding completes. Display ticks and retries share
one capture cap. Eight scheduler regressions and five existing retry regressions
pass, and independent static review found no actionable concurrency issue.
The strict-timer variant also passes eight real Dispatch lifetime cases and
72 repository Python tests. Actual installed bytes and launch arguments were
verified against daemon `3487cfad`.

That variant delivered 55.71 updates/s in a minute-long Tight sink. In the native
app, the first 30-second barcode window measured 42.56 fps and subsequent
continuous telemetry fell to 36 fps. The run was stopped after 112 seconds;
WebContent stayed at 127–137 MiB, with no hidden intervals or reconnects.
All four native pixel comparisons matched, and 30 input responses completed
with a 98.5 ms median and 116.55 ms p95 before that run.

Closing only the native app preserved the verified phone daemon, USB and SSH
session. A subsequent sink averaged 55.57 updates/s over a minute, but its
last second fell to 46 updates/s. A subsequent five-minute sink reproduced the
full decline, with minute means of 53.55, 46.35, 33.68, 30.07 and 30.00 updates/s.
This occurred without Mac rendering. Short sink runs do not establish sustained
performance. Capture admission reduces wasted copies but also reduces
capture/encoding overlap. The subsequent stage timing below identified this
serialization as the next repair target. No performance benefit
has been established for the strict timer itself. No push or release has been made.

### Bounded phone stage timing

A separate diagnostic daemon (`181b6fad`) used the exact same five static
libraries as `3487cfad` and recorded scalar timing distributions for 300 seconds,
then emitted one report. One Tight client completed the run without reconnects
or profile changes. The normal daemon was restored and verified afterward.
The [sanitized stage measurements](phone-stage-timing-2026-09-10.json) preserve
minute summaries and their interpretation limits.

Display-link ticks remained near 60 Hz while delivered updates declined from
about 55 to 36/s. Mean capture-callback time rose from 9.4 to 12.5 ms; encoding
plus sending rose from 7.3 to 13.8 ms. Timer wake lateness stayed near
0.05–0.07 ms. The phone reported a fair thermal state initially and a serious
state from the 50–60-second window onward. Apple's
[thermal-state definition](https://developer.apple.com/documentation/foundation/processinfo/thermalstate-swift.enum/serious)
associates the serious category with measures that reduce system performance.
This is a categorical system signal, not CPU temperature or clock speed.

The tested early-admission scheduler serializes capture and encoding, so the
growing sum of their costs limits throughput. Encode/send includes socket waits;
this probe alone does not assign the whole increase to JPEG CPU work or heat.
A bounded latest-frame preparation/publication split is staged to restore
overlap while preserving client locks and final-frame delivery. Capture prepares
one replaceable back buffer; publication must retain that frame until all readers
can safely release the front buffer. Completion retries publication without
recapturing, and every display tick also retries when the source has stopped
changing. The canonical patch is integrated and its six resulting source files
match the reviewed candidate exactly. Thirty-five portable host cases pass,
including fourteen prepare/publish, hash, rotation, final-frame and lifecycle
cases, eight real Dispatch timer cases, five retained snapshot/try-lock cases,
four publication-state cases and four capture-cap cases. The fourteen
prepare/publish cases also pass UndefinedBehaviorSanitizer. These checks do not
establish iPhone speed.

The associated concurrency review found two existing resize hazards in the
pinned library/caller boundary. Lock acquisition and release traverse separate
client lists, which can change during disconnect. The library also resets the
pixel format and updates client conversion tables before the caller restores its
BGRA layout after unlocking. The staged change uses a retained client
snapshot and commits the final pixel format with geometry under the same locks.
The explicit-format path must also avoid exposing an intermediate RGB layout to
clients still being initialized before their admission hook. Library regressions
pass eleven cases using the actual patched functions with pthread locks and
UndefinedBehaviorSanitizer; physical rotation and
client join/disconnect checks remain pending.

### Overlap candidate delivery preflight

Normal source-built daemon `ae83019c` completed a 300-second physical-phone Tight
sink at 1172×2536, quality 6, compression 2 and the unchanged 60-fps profile.
It delivered 17,862 complete image updates: **59.54 updates/s overall**, with
minute means of **59.58, 59.57, 59.40, 59.55 and 59.60**. The final 30 seconds
averaged 59.67. No native renderer was connected during the collection; encoded
bytes were discarded. The prior daemon was restored and the preserved USB,
viewer and fixture services verified afterward.

This initial run passed the five-minute delivery preflight, but the later
warmed-phone comparison below failed. It does not establish sustained native
display speed or 22-minute memory behavior.
Thermal state was not instrumented in this normal binary, so earlier runs are
not a thermally matched A/B comparison. Exact artifact identities and results
are in [overlap-qualification-2026-09-10.json](overlap-qualification-2026-09-10.json).

The subsequent native run passed all four exact image comparisons and 30 input
trials (82 ms median, 107.7 ms p95), but failed the visible-speed gate. Its first
barcode window measured 55.27 fps; continuous frame/flip telemetry fell to
32.18 fps by 181 seconds. An independent 30-second barcode window then measured
33.89 fps. WebContent stayed at 124–139 MiB with one decoder document, the same
connection and telemetry identity, no hidden intervals, and unchanged floating
window geometry. The collector was stopped at 200 seconds; it did not complete
22 minutes. The phone-only improvement therefore does not resolve the complete
native slowdown. Source comparison found matching Tight format, quality and
compression, and about 43 JPEG rectangles per update throughout both paths.
The barcode's source sequence advanced at 60.01 iterations/s in both the initial
and slow native windows. In the slow window the viewer observed 1,017 distinct
sequences and skipped 783 source increments. The fixture increments once per
Safari animation callback, so its source animation remained near 60 Hz while
the mirror displayed 33.89 fps. This observation applies to that native window;
the later encoded-byte sink does not decode the source barcode.

A 15.6-second Time Profiler capture of the same slow WebContent process
recorded 9.743 seconds of sampled running time across its threads. Canvas
drawing appeared in 3.220 seconds of those samples; shared-bitmap creation in
2.955 seconds; JPEG stacks in 2.833 seconds. Image-load event dispatch included
the synchronous draw and decode work, so these overlapping stack totals must
not be read as separate costs or asynchronous image-load waits. This establishes
a substantial native draw/decode cost, but one slow-state profile does not
explain the gradual decline. The native app was then closed and the prior
daemon restored through the owned-session supervisor.

The immediate warmed-phone comparison then reproduced the decline without
native rendering. The same `ae83019c` daemon and unchanged heavy-motion fixture
delivered 9,161 updates over 300 seconds, averaging **30.54 updates/s**. The first
eight seconds were near 59–60; delivery fell to 31 by the eleventh second and
remained near 29–30. The temporary daemon was restored afterward. This establishes
that the native viewer is not necessary for the slowdown and that overlap alone
does not sustain 60 fps in these later conditions. The daemon was restarted
between tests and its thermal state was not instrumented, so this comparison
does not by itself prove a thermal cause. Phone-side tick, preparation,
publication and encode/send timing is the next discriminator.

### Overlap timing identifies the limiting stage

A separate `ee9e9971` diagnostic recorded 300 seconds of scalar stage counters
inside a 310-second phone-only sink. It preserved the `ae83019c` algorithms and
four exact codec archives, adding optional timing around the transport library's
existing update writes. All 30 windows were valid: one client and capture session,
unchanged format/quality, no send failures, and observed socket timing. The
normal daemon was restored afterward. [Sanitized results](overlap-stage-timing-2026-09-10.json)
record source identities and interpretation limits.

Display ticks stayed at 60 Hz, and capture/preparation remained near 59.6 fps
throughout. Encoding plus sending grew from 7.80 ms in the first minute to
22.51 ms in the last, while measured update socket writes grew from only 0.21
to 0.69 ms. The remaining elapsed time in the send path grew from 7.59 to
21.82 ms. Publication and delivery fell to about 30 fps as encoding remained
active across frame intervals. Late completion-to-next-send gaps averaged
10.89 ms, including a 9.66 ms mean wait for queued publication work on main.
The latest preparation was typically fresh by the eventual publication, while
older preparations were overwritten during encoding.

The capture callback itself grew from 9.63 to 13.50 ms and remained below one
60 Hz interval on average. iOS reported fair thermal state for the first
40–50 seconds and serious state thereafter. This localizes the remaining
bottleneck to encode/send work plus publication scheduling; it does not prove
that all elapsed send time is JPEG CPU work or establish a CPU clock/core cause.
The paired CPU probe below separates on-CPU cost from waiting during this stage.

### Paired output-thread CPU timing

The source-built `bcebccf7` diagnostic repeated the same full-resolution Q6/C2
phone-only sink for 310 seconds, collecting 30 fixed windows over its first
300 seconds. All same-thread clock pairs were valid, with no CPU-clock errors,
overlapping sends, client changes, profile changes or send failures. The normal
daemon was restored afterward. Its codec and LibVNC archives were byte-identical
to the preceding `ee9e` diagnostic.

| Minute | Capture/s | Send/s | Send wall, ms | Output-thread CPU, ms | Wall minus CPU, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 59.78 | 59.48 | 7.54 | 7.53 | 0.015 |
| 2 | 59.83 | 59.52 | 10.48 | 10.17 | 0.307 |
| 3 | 59.80 | 55.37 | 13.30 | 12.95 | 0.353 |
| 4 | 59.67 | 33.37 | 20.53 | 19.80 | 0.723 |
| 5 | 59.67 | 30.15 | 22.33 | 21.04 | 1.289 |

The final send interval was 94.23% on-CPU. The dominant increase is therefore
processing time, rather than socket blocking or time waiting to be scheduled.
These measurements cover all work on the sending pthread between display
hooks, including Tight classification, codecs, protocol and socket work. They
do not isolate JPEG CPU time. Final-minute update writes averaged 0.650 ms;
the separate delay before starting the next send averaged 10.83 ms.

Every policy sample reported `QOS_CLASS_DEFAULT` (21), with raw Mach current
priority and priority both 31. Requested QoS excludes temporary overrides;
these fields do not identify the effective QoS, CPU core or frequency. Thermal
state was fair in the first six windows and serious thereafter. The correlation
does not by itself establish a thermal cause. Capture and preparation remained
near 60/s throughout, so the loss is still downstream of source capture.

The JPEG build already includes ARM Neon implementations and enables SIMD.
Increasing Tight's rectangle-size limit globally would change which tiles use
lossless encoding and would exceed a pinned 16-bit client's JPEG scratch
buffer. JPEG-only merging is a separate possible experiment, but changes DCT
block boundaries and cannot promise identical decoded pixels. Neither option
has been adopted as a fix. See the [CPU measurements and exact source hashes](overlap-cpu-timing-2026-09-10.json).

A controlled source-built QoS trial changed only the dedicated output pthread
to `QOS_CLASS_USER_INTERACTIVE`. The setter succeeded, and all 30 measured
windows reported the requested class. It still slowed from 59.45 to 31.12
sends/s, with final-minute send CPU at 20.64 ms versus the control's 21.04 ms.
Capture remained near 60/s. This candidate failed the sustained cadence gate
and was not promoted; the normal daemon was restored. See the
[comparison and exact candidate hashes](output-qos-trial-2026-09-10.json).

### Exact JPEG CPU attribution

The `3483b27e` source diagnostic adds paired CPU and wall probes around the
actual legacy `tjCompress` call and the separate `SendSubrect` palette/translation
block. It repeats the same 310-second sink with the default output QoS, collecting
30 windows over 300 seconds. Every whole-send and stage pair was complete, with
no probe or operation failures. The profile stayed unchanged, and the normal
daemon was restored afterward.

| Minute | Send/s | Whole output CPU, ms/update | JPEG CPU | Palette/translation CPU | Other output CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 59.42 | 7.40 | 6.61 | 0.32 | 0.47 |
| 2 | 59.45 | 10.13 | 9.13 | 0.45 | 0.56 |
| 3 | 58.63 | 11.70 | 10.54 | 0.51 | 0.65 |
| 4 | 40.23 | 17.76 | 15.88 | 0.80 | 1.07 |
| 5 | 32.07 | 20.44 | 18.26 | 0.96 | 1.22 |

JPEG accounts for **89.34%** of the final-minute output CPU. The mean JPEG call
count remains about 43 per update, while capture stays near 60/s. The unchanged
serial JPEG cost alone exceeds a 16.67 ms frame interval. This supports testing
two bounded encoders on the existing tiles; it does not establish a parallel
speedup or qualify native display, latency or memory behavior. The other-CPU
column includes instrumentation, planning, protocol and remaining output work.
See the [paired sums, scope and exact source hashes](jpeg-stage-timing-2026-09-10.json).

### Two JPEG workers: partial improvement, remaining delay

The first bounded two-worker candidate preserves the exact encoded bytes in its
host tests, but still fails sustained delivery. Its five measured minute means
were 59.40, 59.47, 59.50, 54.95 and **42.58 updates/s**. A subsequent serial control
used identical sources and codec archives with only the worker compile option
disabled; it ended at **29.87 updates/s**. Both runs retained one client and the
same quality and format, had complete CPU timing pairs with zero reported
errors, and restored the normal daemon afterward.

The worker candidate's final-minute send time was 15.99 ms, followed by another
7.49 ms before the next send. It drains queued jobs around each public Tight
region: about 20 regions for 43 JPEG jobs per update. The subsequent candidate
extends batching across the complete framebuffer update, with explicit completion
and error drains before source pixels can change. Six UBSan host groups passed,
including disjoint regions, wire order, software cursors and failure paths.
Main-thread capture and queued publication also overlap the remaining
gap; the aggregate timing does not attribute the entire delay to either cause.

These runs are not thermally matched: their final windows share thermal state 2,
but physical temperature and CPU frequency were not controlled. Parallel workers
did not demonstrate lower total CPU use. This remains an unqualified experiment,
with [source checks, paired sums and limits](jpeg-pipeline-trial-2026-09-10.json).

On September 11, complete-update batching held about 59.4 updates/s in a cold
five-minute sink, but a later warm sink declined to **33–36 updates/s**. Capture
stayed near 60/s. The final minute took 17.65 ms to send plus 10.19 ms before the
next send; worker JPEG CPU rose from 8.34 to 22.10 ms per update. Even removing
the entire gap would not yield 60 fps at that unchanged send cost. Thermal state
remained 2 throughout despite the changing CPU cost; temperature, clock frequency
and core placement were not measured. This does not establish a batching
regression against earlier runs with different CPU cost.

The intervening native preflight supplies valid daemon timing only: its window
was partly offscreen and changed geometry, so no sustained visible, memory, image
or input qualification is claimed. All three sessions restored the normal daemon.
The candidate remains unpromoted. See the [complete-update trial evidence and
limits](jpeg-fbu-pipeline-trial-2026-09-11.json).

### Publication outside the main thread

Candidate D (`50951d1b…`) gives prepared-frame state one owner and moves publication
to a bounded serial queue. The completion wake runs after both LibVNC send and
negotiation locks are released. Capture and render remain on main; codec, image
quality, resolution and the two JPEG workers are unchanged.

The phone-only trial recorded 12,526 complete publication wake CPU pairs with
zero diagnostic errors and 5,908 new publication-to-send associations originating
from the completion wake. In the final minute, wake queue delay averaged 0.128 ms,
owner wait 0.526 ms and wake CPU 0.087 ms. The complete finish-to-next-send gap
averaged 4.703 ms, compared with 10.185 ms in the earlier C run. These separate
runs were not thermally matched, and publication-event latency is not display
latency.

Delivery still declined from **59.53 to 27.42 updates/s**. Worker JPEG CPU rose
from 8.68 to 35.41 ms per update, coordinator CPU from 1.03 to 4.03 ms and send
wall time from 5.62 to 31.78 ms. Job count stayed near 43 per update and socket
bytes per update rose only 0.178%. Thermal category stayed at 2. Queue waiting
cannot explain the CPU-time increase; worker policy, core placement, frequency
and hardware instruction counts were not measured. D is not a sustained FPS fix.

The runner restored the normal daemon but initially rejected publication metadata.
Its validator incorrectly expected no helper probes and required a prior
publication for the initial full send. Source review established three deliberate
greeting-only probes and a legal full-frame send before the first publication.
The corrected validator requires that exact startup pattern, preserves measured
single-client and timing checks, and passes 14 acceptance cases plus 73 injected
fault rejections. A separate retrospective result validates the unchanged D report;
the original failed run remains preserved. Exact records and limits are in
[offmain-publication-trial-2026-09-11.json](offmain-publication-trial-2026-09-11.json).

### Sparse worker policy and execution counters

Two consecutive five-minute phone-only runs used diagnostic daemon `286419fd`
and the unchanged two-worker library `61fcaabe`. Each run collected 60 worker
policy observations, 60 selected JPEG-job counter pairs and 60 no-work controls.
Every pair was complete and valid. The existing measurements still covered every
JPEG job. Both runs had one client and unchanged Q6/C2, JPEG quality 79, 4:4:4 and
1172 × 2536 geometry. The normal daemon was restored after each run. Exact
identities, minute tables and raw-data hashes are in
[the worker diagnostic record](jpeg-worker-diagnostics-2026-09-11.json).

The first run held 59.35–59.55 updates/s. In the immediate warm follow-up,
delivery changed from 59.37 to 51.73 updates/s while whole JPEG CPU time rose
from 7.98 to 19.33 ms/update. Capture remained near 59.5–59.8 starts/s, and the
phone reported serious thermal state throughout the warm run. Short cold runs
therefore still cannot establish a sustained fix.

Among the selected warm jobs, CPU time per instruction increased 97.22% from
minute one to five, even though instructions/job decreased 20.74%. Requested
worker QoS remained 21, with Mach current/base priority 31/31 in all observations.
Both cycles/instruction and effective cycles/CPU-time changed; the latter alone
would miss part of the loss in execution efficiency. These measurements support
an execution-efficiency component, but do not identify core placement, literal
clock frequency, DVFS or the underlying cause.

Warm no-work controls averaged 1.13 μs CPU and 1.80 μs wall, respectively 0.297%
and 0.447% of the raw selected-job sums. No controls were subtracted. The brackets
are not atomic, include some kernel/probe work and do not bound all diagnostic
overhead. No sampled job geometry or pixels were recorded, so these samples do
not establish matched image work or represent all JPEG jobs. A four-worker trial
can test additional concurrency; these counters neither prove spare capacity nor
predict a gain. Native display, input and sustained performance remain unqualified.

### Four workers: cold delivery held, warm slowdown remained

Diagnostic daemon `f02fb9ac` retained D's daemon source, timing scopes and four
codec archives. Its library `88b0b411` changes only the private fixed worker count
from two to four. One client owns the pipeline; each encoded-result slot remains
bounded at 1 MiB, giving four a 4 MiB result-capacity bound. Additional stacks and
codec state are outside that bound. The default remains two workers.

All 29 UBSan host groups passed. Ninety-three full-wire comparisons totaling
263,360,496 bytes matched D's two-worker output. Four concurrent readers,
deliberately reversed completion, ring reuse, extra-worker failures, disconnect,
resize and cursor restoration passed. This establishes tested ordering and
lifetime behavior, rather than a performance gain.

Two consecutive five-minute phone-only runs used the same heavy-motion fixture,
one client and unchanged resolution/JPEG settings. Both completed all timing and
publication checks and restored the normal daemon. The first run held minute
means of 59.48, 59.25, 59.43, 59.32 and 59.00 updates/s; its final ten seconds fell
to 57.4. The immediate warm follow-up measured **59.42, 59.50, 52.05, 32.62 and
36.22 updates/s**. Four workers therefore do not fix sustained delivery and are
not selected for adoption.

Warm JPEG CPU time rose from 11.09 to 34.38 ms/update in minute four, then 31.23
in minute five. Capture starts fell from 59.67 to 49.83/s before recovering to
52.95/s. Capture callback time rose from about 10 to 18 ms; rendering and frame
preparation grew while surface transfer stayed near 4.4 ms. Publication wakes
completed without accounting errors. The broader slowdown cannot be attributed
to a broken publication wake from these data.

Some intervals at similar measured JPEG CPU/update had shorter send wall time
with four workers, but physical state and image work were not matched. Those
intervals do not override the failed warm run. Source identities, complete minute
tables and hashes are in
[the four-worker trial record](jpeg-four-worker-trial-2026-09-11.json).

### Higher JPEG-worker priority did not prevent the warm slowdown

Diagnostic daemon `f60ae01a` uses E's unchanged daemon and sparse measurements,
with a two-worker library (`6d9d7659`) that requests interactive QoS at thread
creation. The output and capture policies are unchanged. All 29 UBSan groups
passed, including worker-attribute failure cleanup and readmission. Ninety-three
full-wire comparisons totaling 263,360,496 bytes matched D exactly; the default
remains unchanged and the diagnostic flag remains off.

Both five-minute phone-only runs completed all measurement checks and restored
the normal daemon. Each run observed requested QoS 33 and relative priority zero
in all 60 worker samples. The first run delivered minute means of 59.43, 59.53,
59.42, 54.67 and 50.20 updates/s. The warm follow-up began about 67 seconds
after the first sink ended and delivered
**59.52, 48.38, 34.22, 32.25 and 36.03 updates/s**. The priority change therefore
does not fix sustained delivery and is not selected for adoption.

Warm JPEG CPU rose from 7.69 to 31.44 ms/update in minute four, while capture
starts fell from 59.70 to 52.93/s. Capture callback time rose from 9.96 to
17.64 ms. Sparse worker observations also show reduced execution efficiency;
requested QoS remained 33, with Mach base priority 37. These measurements do not
identify core placement, literal clock speed or a single thermal cause.
Publication accounting passed in both runs, with increasing capture replacement
and capacity waits under load. The change neither removes the broader slowdown
nor qualifies native display or input performance. Exact minute summaries and
source hashes are in [the worker-priority trial record](jpeg-worker-qos-trial-2026-09-11.json).

## Why the previous cleanup did not stop growth

In the inspected WebKit source, `ResourceLoadNotifier::dispatchWillSendRequest`
passes each loaded URL to `DocumentLoader::didTellClientAboutLoad`. The loader
stores it in `m_resourcesClientKnowsAbout`, a strong string set. Cocoa builds
also store data URLs. The set lives until the document loader is destroyed;
image disposal, URL revocation and load completion do not clear it.

Primary source, pinned for review:

- [ResourceLoadNotifier.cpp](https://github.com/WebKit/WebKit/blob/b5f0255e3ed796abfd39614950fe8e2ab2a223e8/Source/WebCore/loader/ResourceLoadNotifier.cpp#L135-L143)
- [DocumentLoader.h](https://github.com/WebKit/WebKit/blob/b5f0255e3ed796abfd39614950fe8e2ab2a223e8/Source/WebCore/loader/DocumentLoader.h#L877-L885)

The installed framework reports WebKit `22625.1.29.11.26` on macOS 27.0
`26A5425a`. It has not been mapped to the reviewed source commit. The native
allocation measurements and document-lifetime control independently reproduce
the behavior on the installed framework.

## Original controlled reproduction

A separate AppKit/WKWebView diagnostic used a nonpersistent data store, matching
the app. A local, pull-driven RFB server replayed generated 1172×2536 images;
it had no phone connection or input forwarding. Each frame contained 47 Tight
JPEG strips, matching the pinned encoder's high-colour, nonsolid full-frame
geometry: 46 strips of 1172×55 and one of 1172×6. JPEG quality was 79 with
4:4:4 subsampling, corresponding to the viewer's quality level 6. The server
precomputed encoded bytes, so encoding CPU could not cause a decline.

Real phone frames can contain different rectangle geometry, fills and palettes.
The replay tests the rate of image creation; it is not a recording of every
phone update. Both A/B arms received identical encoded packets.

The diagnostic counted completed frame updates and draws without verifying that
the canvas was displayed. A later screenshot exposed a fixture error: the root
layout had automatic height, so removing the placeholder left noVNC's
percentage-height canvas at zero size. The window contained black content even
though its visibility flags were true. These runs measure decoding and allocation
under an invisible canvas, not visible rendering performance. Native heap
inspection occurred only at the documented diagnostic points.

- One full-frame JPEG per update did not reproduce the rapid growth.
- The 47-strip replay reproduced about one retained 112-byte allocation per
  decoded image. Live image objects and cached images remained bounded.
- Replacing `src = ""` with `removeAttribute('src')` avoided an unnecessary
  error event but did not remove the retained allocation per image.
- The isolated decoding-document prototype kept those allocations bounded.
  Its approximately 58 completed updates/s was an invisible-canvas processing
  rate, not a displayed frame rate.
- A document created with `createHTMLDocument()` alone cannot decode the images:
  WebKit requires an active attached frame. That probe was rejected.

The original three-minute arm accumulated about 500,000 of the per-image
allocations. After roughly 450,000 image decodes, the isolated-document arm
contained approximately 5,000, including unrelated baseline allocations.

## Visibility is a separate measurement condition

Window visibility, native occlusion state and WebKit `document.hidden` do not
prove that the changing canvas has a nonzero visible area. The fixture error
invalidates visible-speed claims from the synthetic RFB baseline and subsequent
direct-image replay, including interpretations of their activation and
occlusion controls. Their allocation measurements still describe the recorded
decoding workload. They do not establish whether visible phone rendering slows
for the same reason.

The earlier 56→38 fps measurement did not record occlusion. It establishes a
decline during that session, but cannot assign the entire decline to the memory
growth. Corrected benchmarks must verify nonzero canvas bounds and actual moving
pixels before timing, then record visibility continuously. Current authorization
includes a floating window on the second monitor so the full changing canvas
can remain exposed. No production App Nap or WebKit scheduling preference was
added for these controls.

On September 10, a covered installed-app test lost telemetry after about five
minutes. RunningBoard logged the owned WebContent process as
`running-suspended-NotVisible` at 08:20:47 local time. At 08:26:34 it became
active and telemetry resumed on the same connection generation. This attempt
does not qualify sustained use; the observed interruption was OS suspension.

## Alternatives considered

Lossless Tight avoids image URL loading, but the generated fixtures required
1.38–1.42 MB per frame, versus 0.76–0.78 MB with JPEG. Local zlib compression
alone took approximately 25–27 ms per frame. ZRLE was substantially larger and
slower. These are offline Mac measurements, not phone throughput, and do not
qualify either encoding as a performance-preserving replacement.

A raw-byte WebAssembly JPEG decoder also avoids URL loading, but introduces a
different decoder and does not establish the same pixels, colour handling or
throughput. Neither alternative was added. The installed WebKit exposes no
`ImageDecoder` API, so it cannot be used as a portable solution for this build.

## Lifetime and removal criteria

The decoding context belongs to one noVNC Display. Disconnect closes admission
before disposing queued requests, so releasing the last lease at a document
boundary cannot create a successor document during teardown. Timeout, failure
and cancellation release their leases. Ready images retain their source URL and
lease until drawing or disposal; late load, error and timeout callbacks cannot
revive disposed resources. Admission waits at a context boundary instead of
attaching extra decoding frames.

This context ownership remains necessary while the supported WebKit versions
retain every image-load URL in the owner document. It can be removed after a
supported raw-byte native decoder or changed WebKit behavior passes the same
pixel, lifetime and sustained-use gates. Do not replace it with periodic mirror
reloads, reconnects, private WebKit flags or forced garbage collection.

## Qualification results

The original 22-minute 47-strip baseline's processing rate fell from 58.28
completed updates/s (samples at 50–110 seconds) to 28.36 (samples at
1,190–1,280 seconds). WebContent physical footprint increased from 238 MiB at
20 seconds to 684 MiB at 1,310 seconds. All 44 sampled visibility/occlusion
states were visible, but the zero-size canvas invalidates the run as a visible
speed baseline. The footprint and allocation observations remain evidence of
decoding with an invisible canvas.

The earlier ImageBitmap candidate passed 75 Node tests, 70 Python tests,
source/dependency checks, device staging checks, and the standard signed app
build. The Node suite includes
a regression test for disconnect at the 4,096-job admission boundary. The phone
daemon used for that validation had SHA-256
`f30543574711d977a1a41ed053f9e4e49f6cfa4ff535d3f65011b7c2e6de4c5e`;
the earlier final-frame and client-lock repairs remain in place.

Native pixel comparisons on the earlier installed candidate passed all four cases:
1172×2536 PNG, 1172×2536 JPEG, translucent PNG, and translucent PNG composited
over a solid background. Every case had zero differing pixels and a maximum
channel difference of zero. That installed patch was
`541cb361ea42df8bf860635373b7abeb73c199743a0250290685f663e85ea9be`.
The subsequent teardown-order repair changed no image decoding or conversion
code. That earlier prepared source and rebuilt app used patch
`bbe3976ddc1f7c966dbe9edc67826f603345e43063d67ddc9d5bca7556f032c7`.
Its later visible physical-phone run failed performance qualification after
about 460 seconds; the windowless memory result was not a sustained-use pass.

The current direct-image candidate is patch
`c501684eef3529357b2483e6df7538550c6c4e1a9737242a25a96e1bfada5c2a`.
It passes 74 Node tests, including document-boundary teardown and image-source
lifetime regressions, and source/dependency verification. All four native pixel
comparisons on the installed candidate match exactly. Its visible physical-phone
test delivered about 30 frames/s, so sustained frame delivery remains unresolved.

### September 10 windowless memory check

The earlier `bbe3976d…` candidate completed 1,320.02 seconds of the same precomputed
47-strip replay: 950,716 image rectangles and 20,228 completed updates/draws.
There was one connection, no interrupted telemetry, and a clean disconnect at
completion. All source hashes matched before and after the run. Numeric results
and active footprint samples are in the historical run record
[renderer-memory-2026-09-10.json](renderer-memory-2026-09-10.json).
That record's installation and qualification status reflects the earlier run;
the current candidate status is described above.

This diagnostic used a detached WKWebView with no window, a nonpersistent data
store, and application activation prohibited. The public
`WKPreferences.inactiveSchedulingPolicy = .none` setting kept the diagnostic
executing while hidden. This setting is not part of the app. The test had no
phone connection or input forwarding, and its hidden processing rate cannot
qualify visible frame rate. The earlier baseline's zero-size canvas also
prevents using it as a visible speed comparison.

Physical-footprint sampling began after 95 seconds of warmup. The following
table uses 41 samples while the connection was active, through 1,299 seconds;
it excludes the post-disconnect sample. Early and late medians each cover five
samples (95–217 seconds and 1,179–1,299 seconds).

| Process | Sampled range, MiB | Early median, MiB | Late median, MiB |
| --- | ---: | ---: | ---: |
| WebContent | 177.6–206.8 | 188.1 | 190.5 |
| GPU | 110.0–127.5 | 118.3 | 122.5 |
| Network | 6.1–7.0 | 6.2 | 6.1 |
| Combined WebKit processes | 302.4–340.5 | 312.7 | 320.7 |

Native heap snapshots corroborate the bounded footprint. The 112-byte
non-object allocation class that tracked one retained allocation per image in
the baseline contained 5,675 objects early, 5,891 around 13 minutes, and 2,924
around 21 minutes. These counts include unrelated allocations and are a proxy
for retained URL strings, not a direct enumeration of URLs. Cached images stayed
at 1,216–1,263 across those snapshots. Each active snapshot had two document
loaders, consistent with the main page and one decoding document. After
disconnect, only one loader and zero HTML image elements remained. No garbage
collection was forced.

This establishes bounded memory for that earlier decoder under the recorded
windowless workload. It does not qualify the current direct-image candidate or
establish sustained physical-phone frame rate or input latency.

### September 10 physical-phone isolation

The direct-image candidate's next native run reproduced the slowdown while
memory stayed bounded: the first 30-second barcode window measured 59.98 fps;
the window starting at five minutes measured 34.88 fps. WebContent physical
footprint stayed between 96.5 and 119.9 MiB, with one decoding document and no
hidden telemetry intervals. The run was stopped after about six minutes;
it did not complete the planned 22 minutes.

Terminating only the native app while preserving the same phone daemon, SSH
and USB session did not remove the slowdown. A single-client Tight reader that
performed no image decoding still fell to approximately 35 updates/s.

A separate ten-minute probe enabled the existing daemon's verbose logging.
Delivery fell from 59.77 updates/s in the first minute to 29.98 in the last.
It delivered 20,828 updates and logged 15,153 busy-gate rejections, accounting
for 59.97 captures/s together. The daemon continued capturing near 60 Hz while
discarding every other frame because the preceding encode/transmit was busy.
Source inspection identifies the next-display-tick retry as an amplifier when
service time exceeds one 16.67 ms interval. This does not identify the exact
stage whose cost crossed the interval, or establish a thermal cause.

The verbose probe had one client and no Mac renderer. Verbose logging itself
can add overhead, and starting it replaced both the phone daemon and SSH
session. The canonical session was restored and verified afterward. See the
[numeric diagnosis](renderer-cadence-2026-09-10.json). A coalesced asynchronous
capture retry has been installed for the tests described above; it remains
unqualified for sustained visible performance.

### September 11 visible physical-phone memory qualification

The unchanged `c501684e…` direct-image renderer completed **1,320.4986 seconds**
with phone candidate C (`d03d4c28…`): 40 physical-footprint samples, 264 renderer
health records and five 30-second barcode windows. The app and all four renderer
source hashes match the recorded context and frozen c501 manifest. This completes
the 22-minute visible-memory gate for the sampled WebKit workload; it does not
qualify the complete candidate or resolve frame delivery.

Every health record stayed connected on the same renderer identity and generation,
with one attached decoding iframe and zero new hidden time. The lifetime hidden
counter remained at 3 ms from before collection. All 40 geometry checks and the
after-run check preserved the opaque floating 380×863 window, fully inside the
built-in 120 Hz display. Geometry is sampled; these checks do not measure monitor
scanout or enumerate retained detached documents.

| Process | Sampled range, MiB | Early median, MiB | Late median, MiB |
| --- | ---: | ---: | ---: |
| Native app | 35.59–38.74 | 35.70 | 38.56 |
| WebContent | 105.92–123.13 | 109.30 | 110.82 |
| GPU | 191.88–191.97 | 191.95 | 191.88 |
| Network | 6.45–6.63 | 6.48 | 6.45 |
| Combined WebKit processes | 304.32–321.58 | 307.74 | 309.15 |

The table uses each process's sampled `footprint`, not RSS or its lifetime peak.
Combined WebKit is the sum of WebContent, GPU and Networking footprints, not a
deduplicated machine memory total. Early uses three samples ending within
0–120 seconds; late uses four within 1,200.4986–1,320.4986 seconds. The collector's
built-in late summary omitted final sample 39 because its rounded `1320.50`
label exceeded the unrounded duration. Its raw footprint ended at 1,320.4881
seconds, inside the run; including it gives the corrected medians above.

WebKit ended below its first sample, with an early-to-late median increase of
1.4140 MiB. The native app grew **3.1406 MiB**, with 38 increasing sample steps,
one unchanged step and no decreases. That monotonic nondecreasing drift remains
unexplained; this is not evidence that all app allocations reached a plateau.
The first footprint was about 30.7 seconds into collection, several minutes after
app launch. No matched unrepaired baseline or heap census was collected.

Between the first and last health records, **1,953,892 imageRect submissions**
and 45,439 complete framebuffer-update calls were observed over 1,315.946 seconds.
The image counter runs before imageRect processing; it does not count completed
decodes. Existing counter totals before the first record are excluded.

Early/late weighted framebuffer-update telemetry declined from **39.46 to
31.39/s**. The five barcode windows measured **37.21, 34.01, 33.56, 36.73 and
30.04 distinct observations/s**, while source sequence advances imply about
60/s in each window. Missing sequences at canvas observation do not locate the
loss within capture, sending, decoding or observation. FPS remains unfixed.

All four same-byte native image comparisons passed both before and after the
collection, with zero differing pixels or channels. Input was not tested before
the run. The post-run benchmark aborted before sending input because the fixture
was still animating: zero of 30 requested trials completed. One bridge tap and
two native-viewer clicks also failed to switch that hot C fixture to input mode.
After C was stopped and canonical `3487cfad…` restored, one bridge tap switched
the same disposable control; saved before/after images confirm the mode change.
That control was not a latency benchmark, and differing load/session conditions
leave the cause of C's nonresponse unresolved.

The owned app and its three WebKit processes all exited. The normal phone daemon
was restored, with the shared USB, viewer and fixture services preserved. Exact
sample values, source/raw hashes, rounding correction and control evidence are
in [renderer-memory-2026-09-11.json](renderer-memory-2026-09-11.json).

After restoring canonical phone daemon `3487cfad…`, three separate quiet final-frame
checks passed. Each held a RAW reader for 400 ms while another client changed the
static fixture once. The subsequent RFB barcode matched the independent USB
capture, remained correct after another second, and advanced on the recovery
input. These checks used no native viewer and do not qualify staged C or D.
See [final-frame-check-2026-09-11.json](final-frame-check-2026-09-11.json).

### Remaining qualification

The current c501 renderer now has a completed 22-minute visible WebKit memory
check and exact native pixel comparisons before and after it. Full qualification
remains incomplete: sustained native FPS still declines, input needs matched
before/after latency checks and an explanation of the hot-run nonresponse, and
independent USB quiet/final-frame checks for the staged phone variants, physical
rotation and reconnect/disconnect checks remain outstanding. The native app's
memory drift also remains unexplained.
Neither pixel equivalence nor the restored control's successful tap closes those
gates. The candidate remains unpromoted; do not report the long-session frame-rate
or input problems as resolved.
