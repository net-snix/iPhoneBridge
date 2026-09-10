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

**Qualification remains incomplete.** The earlier ImageBitmap candidate passed
a 22-minute windowless memory test with 950,716 JPEG rectangles. The current
direct-image candidate passes 74 Node tests and four installed native pixel
comparisons, but its physical-phone test delivered about 30 frames/s. Synthetic
replays previously described as visible had a zero-size canvas and cannot
qualify displayed speed. Work was paused at the user's request on 2026-09-10;
no benchmark is running and the phone retains the prior `f3054357` daemon.

## Saved pause checkpoint — 2026-09-10

The latest physical test held WebContent at 96–120 MiB while cadence declined.
A separate 600-second Tight sink delivered 20,828 updates and logged 15,153
captures rejected by the phone's busy gate: together, 59.97 capture outcomes/s.
The immediate cadence loss is upstream of the Mac renderer; the numeric evidence
is preserved in [renderer-cadence-2026-09-10.json](renderer-cadence-2026-09-10.json).

An uninstalled capture-scheduling candidate is saved locally under
`work/renderer-memory-20260909/capture-retry-candidate/`. It contains the exact
baseline and candidate sources, complete and incremental patches, their hashes,
eight scheduler regressions, and the five existing retry regressions. The host
checks passed and an independent static concurrency review found no actionable
issues. The candidate has not been built for the phone or qualified for speed.
Pending canonical integration edits are saved in
`parent-integration-preparation.patch` alongside the candidate. The canonical
daemon patch and dependency pin remain unchanged.

Resume with review and integration of that saved candidate, then build and verify
the actual installed daemon before cold/warm throughput, input, independent
final-frame checks and a visible 22-minute run. Capture admission reduces wasted
copies but also reduces capture/encoding overlap; measured ordinary behavior must
decide whether to keep or reject it. No push or release has been made.

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
capture retry is being evaluated; it has not been installed or qualified.

### Remaining qualification

Testing is authorized and ongoing. The corrected synthetic replay must first
prove its canvas is displayed, then establish a matched visible comparison.
The installed candidate still needs a successful physical-phone animation run
longer than 20 minutes with continuous memory and connection telemetry, input
latency before/after, and independent USB final-frame verification. Native
pixel equivalence alone does not qualify sustained speed. The candidate has
not been released; do not report the long-session frame-rate decline as resolved
until those checks pass.
