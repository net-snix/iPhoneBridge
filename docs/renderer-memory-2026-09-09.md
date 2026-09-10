# Renderer memory investigation — 2026-09-09

The sustained memory growth comes from native image-load bookkeeping in WebKit.
Releasing HTML images, revoking Blob URLs and closing ImageBitmaps does not
release the page's history of image URLs. Each changing JPEG rectangle adds a
unique URL to that history.

The repair gives decoding its own document and a bounded lifetime. The document
admits at most 4,096 image jobs. Once those conversions finish, it is removed;
waiting jobs then use a fresh decoding document. Only one decoding document is
attached at a time. The visible page, framebuffer and VNC connection remain
continuous. This uses the existing HTML image decoder and ImageBitmap defaults.

**The 22-minute windowless memory test passed on 2026-09-10.** It processed
950,716 JPEG rectangles with bounded WebContent and GPU footprints and native
allocation counts. The candidate builds, automated checks pass, and four native
pixel comparisons match exactly. The user deferred visible speed testing; the
final physical-phone performance checks remain required before release.

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

The diagnostic window stayed visible. It counted completed frame updates and
draws without reading the canvas. Native heap inspection occurred only at the
documented diagnostic points; intervals touched by inspection are excluded from
frame-rate comparisons.

- One full-frame JPEG per update did not reproduce the rapid growth.
- The 47-strip replay reproduced about one retained 112-byte allocation per
  decoded image. Live image objects and cached images remained bounded.
- Replacing `src = ""` with `removeAttribute('src')` avoided an unnecessary
  error event but did not remove the retained allocation per image.
- The isolated decoding-document prototype kept those allocations bounded,
  while sustaining approximately 58 frames/s.
- A document created with `createHTMLDocument()` alone cannot decode the images:
  WebKit requires an active attached frame. That probe was rejected.

The original three-minute arm accumulated about 500,000 of the per-image
allocations. After roughly 450,000 image decodes, the isolated-document arm
contained approximately 5,000, including unrelated baseline allocations.

## Visibility is a separate measurement condition

Covering the test window changed both WebKit `document.hidden` and native window
occlusion state. Frame delivery dropped even with a single JPEG per frame and
stable memory. Keeping that single-image test visible removed the slowdown.
The 47-strip baseline still slowed down during the longer visible run, so
occlusion does not explain that result. The app has no inactivity timer or
custom visibility logic causing either condition.

The earlier 56→38 fps measurement did not record occlusion. It establishes a
decline during that session, but cannot assign the entire decline to the memory
growth. New benchmarks record hidden intervals and reject them when evaluating
visible performance. Qualification uses an ordinary window that must remain
uncovered for speed comparisons; it never forces the window to stay on top.

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
and cancellation release their leases; late ImageBitmap results are closed.
Admission waits at a context boundary instead of attaching extra decoding frames.

This context ownership remains necessary while the supported WebKit versions
retain every image-load URL in the owner document. It can be removed after a
supported raw-byte native decoder or changed WebKit behavior passes the same
pixel, lifetime and sustained-use gates. Do not replace it with periodic mirror
reloads, reconnects, private WebKit flags or forced garbage collection.

## Qualification results

The completed 22-minute visible 47-strip baseline slowed from 58.28 fps (samples
at 50–110 seconds) to 28.36 fps (samples at 1,190–1,280 seconds). WebContent
physical footprint increased from 238 MiB at 20 seconds to 684 MiB at 1,310
seconds. All 44 sampled visibility/occlusion states were visible. The diagnostic
was no longer the active application later in the run; a matched candidate run
is still needed to separate any remaining scheduling effect from URL retention.

Candidate verification: 75 Node tests, 70 Python tests, source/dependency checks,
device staging checks, and the standard signed app build. The Node suite includes
a regression test for disconnect at the 4,096-job admission boundary. The phone
daemon remains SHA-256
`f30543574711d977a1a41ed053f9e4e49f6cfa4ff535d3f65011b7c2e6de4c5e`;
the earlier final-frame and client-lock repairs remain in place.

Native pixel comparisons on the installed candidate passed all four cases:
1172×2536 PNG, 1172×2536 JPEG, translucent PNG, and translucent PNG composited
over a solid background. Every case had zero differing pixels and a maximum
channel difference of zero. That installed patch was
`541cb361ea42df8bf860635373b7abeb73c199743a0250290685f663e85ea9be`.
The subsequent teardown-order repair changes no image decoding or conversion
code. The final prepared source and rebuilt app use patch
`bbe3976ddc1f7c966dbe9edc67826f603345e43063d67ddc9d5bca7556f032c7`.

### September 10 windowless memory check

The final prepared candidate completed 1,320.02 seconds of the same precomputed
47-strip replay: 950,716 image rectangles and 20,228 completed updates/draws.
There was one connection, no interrupted telemetry, and a clean disconnect at
completion. All source hashes matched before and after the run. Numeric results
and active footprint samples are in
[renderer-memory-2026-09-10.json](renderer-memory-2026-09-10.json).

This diagnostic used a detached WKWebView with no window, a nonpersistent data
store, and application activation prohibited. The public
`WKPreferences.inactiveSchedulingPolicy = .none` setting kept the diagnostic
executing while hidden. This setting is not part of the app. The test had no
phone connection or input forwarding, and its hidden processing rate cannot
qualify visible frame rate or provide a matched speed comparison with the
earlier visible baseline.

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

This completes the requested memory check on the final decoder source. It does
not establish sustained physical-phone frame rate or input latency.

### Deferred qualification

Still required, deferred at the user's request: install the final rebuilt
candidate, repeat native pixel checks on that artifact, and run a visible
physical-phone animation for more than 20 minutes with continuous memory and
connection telemetry, input latency before/after, and independent USB
final-frame verification. The latest candidate is built and saved locally;
it has not been released. Do not present the original long-session frame-rate
decline as resolved until those checks pass.
