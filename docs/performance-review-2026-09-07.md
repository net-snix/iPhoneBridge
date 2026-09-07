# iPhoneBridge performance review — 2026-09-07 (evening)

Detailed record of the review that produced the "Performance review" summary in [PERFORMANCE.md](../PERFORMANCE.md). Companion to [performance-2026-09-07.json](performance-2026-09-07.json), which holds the earlier capture-profile benchmark.

## Summary

- The streaming path is close to what the hardware allows. Two items are pure waste and fixable: a hidden 5 ms sleep inside LibVNCServer before every update, and a full 11.9 MB RAW frame that every agent input action transferred only to compare dimensions.
- The second item is fixed in this pass (`iphonebridge/control.py`, about 420 ms per tap/drag/type/key/navigate). The first needs a one-line daemon patch through the build and deploy pipeline and is documented with the exact hunk.
- websockify, the SSH cipher, the Python startup, and the Mac app itself are not bottlenecks; several tempting changes were measured and rejected because they buy little or would change image quality.
- Constraint kept throughout: no loss of quality. Full 1172×2536 framebuffer, Tight `qualityLevel=6` (JPEG Q79, 4:4:4) with `compressionLevel=2`, lossless PNG screenshots.

## Environment and method

| Item | Value |
| --- | --- |
| Phone | iPhone14,2, iOS 15.1.1, portrait framebuffer 1172×2536, ProMotion panel |
| Daemon | TrollVNC `a3e4081` plus project patch, bundled LibVNCServer 0.9.15, deployed build `a631bf9c…`, flags `-s 1 -F 60 -P 60 -d 0 -Q 2` (default), tile 32, rects 256, blocking swap |
| Mac | Apple silicon, macOS 27.0 (Darwin 27.0.0), Python 3.13.11, Node 26.0.0 |
| Pinned libraries | noVNC 1.7.0, websockify 0.13.0, vncdotool 1.4.2, Pillow 12.3.0 |
| Method | Live bridge measurements from this Mac; no input was sent to the phone. Items marked *derived from source* come from reading the pinned code, not from a measurement. |

A separate Codex session was refactoring the lifecycle, deployment and packaging code in this checkout during the review and stopped the daemon at 21:44. This review's edits were confined to `iphonebridge/control.py`, `tests/test_control.py`, `PERFORMANCE.md`, `scripts/measure-agent-path` and this file.

## Pipeline map

Input: Mac keyboard/mouse → WKWebView → noVNC → WebSocket → websockify (Python) → `ssh -L` → iproxy/usbmuxd → USB → phone `sshd` → loopback → TrollVNC → HID event.

Video: CADisplayLink capture → IOSurface copy → dirty-tile hashing → blocking buffer swap → `rfbMarkRectAsModified` → LibVNCServer per-client output thread (sleep, Tight/JPEG encode) → loopback → `sshd` → USB → `ssh -L` → websockify → WebSocket → noVNC decode → backbuffer canvas → flip → WebKit compositor.

Agent (CLI/MCP): `api.connect` → 100 ms WebSocket-detection wait in the daemon → RFB handshake → size check → input → 250 ms settle → full RAW capture → PNG → disconnect.

## Measurements

### Transport

| Measurement | Result |
| --- | --- |
| USB SSH throughput | 32 MiB from the phone in 1.01 s including ≈0.2 s session setup, about 40 MB/s |
| SSH session setup | 0.21–0.23 s per `ssh … true` (connection cost, not network RTT) |
| Cipher and KEX | `aes128-gcm@openssh.com` both directions, `ecdh-sha2-nistp256`, no compression |
| Implication | A 400 KB full-screen JPEG frame spends ≈10 ms in transfer; at 60 fps that is 24 MB/s, close to the ceiling. Bytes per frame matter for latency, not only for fps. |

### websockify (Python proxy)

Synthetic test on spare ports: a local TCP server echoing 10-byte messages and then streaming 30 KB timestamped chunks at 16 MB/s for 5 s, read by a Node client either directly or through a fresh websockify instance.

| Path | Echo RTT p50 / p95 / p99 | One-way stream delay max |
| --- | --- | --- |
| Direct TCP | 0.13 / 0.23 / 0.32 ms | 2.9 ms (first chunk), then ≈0 |
| websockify, run 1 | 0.19 / 0.66 / 0.81 ms | 0.06 ms |
| websockify, run 2 | 0.47 / 0.81 / 1.16 ms | 0.24 ms |

websockify sets `TCP_NODELAY` on both the client and target sockets (`websocketproxy.py`), so small input messages are not held by Nagle. Verdict: sub-millisecond cost, not worth replacing.

Static viewer assets through websockify: 55–72 ms per request, with one handler process spawned per connection. About 45 module requests with keep-alive parallelism, roughly 0.3 s once per launch or reconnect.

### Phone daemon (derived from source)

- Capture: `ScreenCapturer.mm` drives a CADisplayLink with `preferredFrameRateRange` min=pref=max=60. `CARenderServerGetDirtyFrameCount` skips unchanged frames; changed frames go through `CARenderServerRenderDisplay` and `IOSurfaceAcceleratorTransferSurface`. A frame rendered between ticks waits up to 16.7 ms (8.3 ms average) before capture.
- Frame handler (`trollvncserver.mm` `handleFramebuffer`): drops the frame when `gInflight >= 2` encodes are outstanding (`-Q 2`), copies/rotates with vImage, hashes 32-pixel tiles (parallel at flush), builds up to 256 rects or a bounding box, sends the full screen when ≥60 % of tiles changed (`-P 60`), then swaps buffers while holding every client's send lock (blocking swap, no tearing).
- Event model: `rfbRunEventLoop(gScreen, 10 ms, TRUE)` → LibVNCServer's threaded mode with `clientInput` and `clientOutput` threads per client.
- **Hidden delay:** `clientOutput()` in LibVNCServer 0.9.15 `main.c` calls `THREAD_SLEEP_MS(cl->screen->deferUpdateTime)` immediately before `rfbSendFramebufferUpdate`, and `rfbGetScreen()` sets `deferUpdateTime = 5`. TrollVNC never assigns it, so every update waits 5 ms on top of TrollVNC's own `-d` window (already 0). The same loop uses the value as its polling tick while a client is still negotiating, which is why the recommendation below is 1 rather than 0.
- Pointer events: `deferPtrUpdateTime` is left zero-initialised, so `rfbProcessClientNormalMessage` delivers every pointer move immediately; drags are not deferred.
- Sockets: `rfbNewTCPOrUDPClient` sets `TCP_NODELAY`. WebSocket support is compiled in, so every plain RFB connection waits `WEBSOCKETS_CLIENT_CONNECT_WAIT_MS` (100 ms) before the server banner ("Normal socket connection" in `work/device.log`). This is a fixed cost per CLI/MCP action.
- Tight encoder: each rect is split into strips of at most 65536 pixels (1172×55, 47 per full frame); each strip is analysed separately and photo-like strips become separate JPEGs. Quality 6 maps to Q79 with 4:4:4 subsampling; compression 2 keeps the 96-colour palette threshold (see the earlier selection notes in PERFORMANCE.md).
- Observed session stats (`work/device.log`, 21:27–21:31 viewer session): 5922 Tight rects, 10.4 MB sent for 600 MB raw-equivalent (98.3 % saved).

### Viewer (noVNC 1.7.0, derived from source plus one micro-benchmark)

- JPEG strips go through `display.imageRect`: `Base64.encode` in JavaScript, a `data:` URL, an `Image` object, then a render queue that waits for each image's `load` event in order before drawing into the backbuffer; `flip()` copies the damaged bounds to the visible canvas.
- `Base64.encode` cost measured in Node (V8): 30 KB 0.13 ms, 100 KB 0.65 ms, 300 KB 1.92 ms (≈156 MB/s). Only photo-like full-screen frames reach hundreds of KB.
- The server does not support ContinuousUpdates, so noVNC sends one `FramebufferUpdateRequest` after parsing each update; parsing of the next update is paused while the previous frame's images are still decoding.
- Mouse moves are batched at 17 ms (`MOUSE_MOVE_DELAY`).

### Agent path (`iphonebridge/control.py` with vncdotool)

| Measurement | Result |
| --- | --- |
| `import iphonebridge.control` | 0.13–0.15 s |
| `api.connect` | returns immediately; the connection completes on the first call |
| First `captureScreen` (connect, handshake, full RAW frame, PNG) | 542 ms |
| Second full capture | 423 ms |
| RAW refresh alone (11.9 MB over USB) | 298 ms |
| PNG encode + paste (Pillow default level 6, 1172×2536 UI frame) | ≈125 ms, 1.15–1.72 MB |
| PNG level sweep (same frame) | level 0: 25 ms / 8.9 MB; 1: 33 ms / 2.07 MB; 2: 39 ms / 1.33 MB; 3: 47 ms / 1.22 MB; 6: 99 ms / 1.15 MB |

vncdotool negotiates only RAW (`VNCDoToolClient.encoding`), and its ZRLE and Hextile decoders are pure Python per-pixel loops that would be slower than the transfer they save. Before this pass every input action captured the full frame twice, once purely to compare dimensions.

### Mac app and startup

- `App.swift` has no hot-path work: it spawns `bridge start/stop`, relays resize and status messages, and hosts the WKWebView. Nothing to change for performance.
- Startup pieces: `lsof` 0.09 s, `ideviceinfo` 0.04 s, two 0.2 s post-spawn sleeps, 0.2 s handshake polling. Stop: the device session script polls with `sleep 1`, so every quit and Reconnect waits at least one extra second.

## Applied in this pass

**Size probe instead of a full frame** (`iphonebridge/control.py`): a vncdotool client subclass adds `probeSize()`, which sends a non-incremental 1×1 `FramebufferUpdateRequest`. The server answers with its current framebuffer size, including a pending DesktopSize change after rotation, plus one pixel. `_check_frame` now uses it; the stale-dimension rejection and message are unchanged, and the post-action screenshot is still a full lossless capture. The probe resets the client image so the following full capture starts from a clean buffer instead of growing a 1×1 image.

- Tests (`tests/test_control.py`): probe request bytes and reset, stale size rejected with zero frames transferred, one probe plus one capture per action, MCP error paths unchanged. Full suite: 40 tests pass; viewer tests: 17 pass.
- Expected effect: about 420 ms removed from every tap, drag, type, key and navigate action (roughly a third of a typical action). `screenshot` is unchanged.
- Verification: `.venv/bin/python scripts/measure-agent-path` times the probe, a full capture and a complete no-input action with both size checks. The live confirmation was blocked because the daemon was stopped by the concurrent session; run it once `./bridge start` succeeds again and append the numbers here.

## Recommendations

1. **Remove LibVNCServer's 5 ms update delay** (needs daemon rebuild and redeploy). In `patches/trollvnc-loopback.patch`, `setupRfbScreen()`, right after `rfbGetScreen()` succeeds:

   ```objc
   // iPhoneBridge: the threaded output loop sleeps deferUpdateTime before every
   // update; TrollVNC already coalesces with -d. Keep the 1 ms tick the loop
   // uses while a client is still negotiating instead of the 5 ms default.
   gScreen->deferUpdateTime = 1;
   ```

   Expected: about 4 ms less input-to-visible latency on every frame, no bandwidth or quality change. Verify with the input benchmark (`scripts/measure-mirror`) and with the 1×1 probe round trip from `scripts/measure-agent-path`, which includes this sleep.
2. **Faster stop and reconnect** (needs `bridge deploy` while stopped). In `iphonebridge/device-session.sh` replace `sleep 1` with `sleep 0.2` and scale the loop bounds (stop: 8 → 40, run exec wait: 10 → 50, cleanup: 5 → 25). The phone's `sleep` is GNU coreutils 9.5 and accepts fractions. Saves about 0.8 s per quit and Reconnect; ownership checks unchanged.
3. **Experiment: capture at the panel rate.** `-F` accepts up to 240 and unchanged frames are skipped before any copy, so `-F 120` would halve the average render-to-capture wait (≈4 ms) for 60 fps content. It increases capture and hashing work for 120 Hz animations; judge only with the full benchmark (latency, fps, phone CPU).
4. Minor: shorten the 0.2 s handshake polling and post-spawn sleeps in `lifecycle.py` once the lifecycle refactor settles; worth about 0.1–0.3 s per start.

## Evaluated and rejected

| Idea | Why not |
| --- | --- |
| Bypass websockify (noVNC to the daemon's built-in WebSocket support through the SSH forward) | Measured hop cost is sub-millisecond; the change would not be measurable and adds a static-file server. |
| Decode JPEG rects with `createImageBitmap` in the viewer | Saves the 1.9 ms per 300 KB JavaScript base64 and one event-loop turn per frame, but only by overriding pinned noVNC display internals from `mirror.mjs`. Too much coupling for a few milliseconds on heavy frames. |
| Lower PNG compression for screenshots | Level 1 encodes in 33 ms instead of 99 ms but doubles the file the MCP client must move and store. Kept the default. |
| Lower Tight compression, lower JPEG quality, 4:2:0 chroma | Fewer bytes per frame, but each changes what the viewer shows. Excluded by the no-quality-loss requirement. |
| Compressed encodings for agent captures (ZRLE, Hextile) | vncdotool decodes them in pure Python per pixel; slower than the RAW transfer they save. |
| Persistent RFB session in the MCP server | Would skip ≈100 ms of connection setup per action and allow incremental captures, but keeps the phone capturing at 60 fps for an idle client whenever the viewer is closed. |
| Different SSH cipher | `aes128-gcm` is already negotiated. |
| Tile size, `-R`, `-Q`, non-blocking swap | Upstream defaults are reasonable; any change needs the full benchmark and the non-blocking swap risks tearing. |

## Open items

- Live before/after timing of the applied change (blocked on the daemon; see Applied in this pass).
- Human drag in the native window has not been separately verified in VALIDATION.md; the source analysis above shows pointer moves are not deferred by LibVNCServer, so no latency reason is expected.
