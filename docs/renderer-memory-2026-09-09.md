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

**Qualification is paused at the user's request.** The candidate builds and its
automated checks pass. Its final pixel checks and sustained physical-phone
measurements remain required before release. The previously validated 0.2.1
app has been restored, and the candidate is saved separately.

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

## Controlled reproduction

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
visible performance. `--benchmark --benchmark-visible` explicitly keeps the
qualification window floating; normal launches retain ordinary window behavior.

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

The decoding context belongs to one noVNC Display. Disconnect disposes queued
requests and removes the context. Timeout, failure and cancellation release
their leases; late ImageBitmap results are closed. Admission waits at a context
boundary instead of attaching extra decoding frames.

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

Candidate verification completed before pausing: 74 Node tests, 70 Python tests,
source/dependency checks, device staging checks, and the standard signed app
build. The phone daemon remains SHA-256
`f30543574711d977a1a41ed053f9e4e49f6cfa4ff535d3f65011b7c2e6de4c5e`;
the earlier final-frame and client-lock repairs remain in place.

Still required: native pixel equivalence for the final candidate, a visible
physical-phone animation run exceeding 20 minutes with continuous memory and
connection telemetry, input latency before/after, and final-frame retention.
Do not present the candidate as a qualified fix until those checks pass.
