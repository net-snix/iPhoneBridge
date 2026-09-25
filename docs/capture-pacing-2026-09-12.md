# Sustained-speed repair: capture pacing — 2026-09-12

The long-session slowdown recorded across
[the renderer memory investigation](renderer-memory-2026-09-09.md) is resolved
for phone-side delivery. The cause was not a leak, a renderer defect or an
encoder defect. It was a positive feedback loop between over-driven capture and
CPU execution efficiency.

## Cause

The previously recorded diagnostics already contained the decisive evidence;
it had not been reduced to effective clock and IPC.
[The sparse worker counters](jpeg-worker-diagnostics-2026-09-11.json) record
per-job `cycles`, `instructions` and `cpu_ns`. Dividing them:

| Warm run | updates/s | JPEG CPU per update | cycles/cpu_ns (GHz) | IPC |
| --- | ---: | ---: | ---: | ---: |
| minute 1 | 59.37 | 7.98 ms | 2.49 | 3.09 |
| minute 3 | 59.48 | 9.82 ms | 2.26 | 3.28 |
| minute 4 | 57.13 | 17.72 ms | 1.61 | 2.64 |
| minute 5 | 51.73 | 19.33 ms | **2.016** | **1.94** |

2.016 GHz is the A15 Blizzard efficiency-core maximum, and an IPC fall from
~3.2 to ~1.94 matches a 4-wide Blizzard core replacing an 8-wide Avalanche core.
[The interactive-QoS trial](jpeg-worker-qos-trial-2026-09-11.json) falls further,
to 1.13–1.25 GHz. Execution efficiency (instructions per ns of on-CPU time) fell
2.9x while work per update stayed flat at about 43 JPEG jobs.

The same trial supplies the control that excludes every non-CPU explanation.
Over the warm decline:

- `capture_callback` 9.96 → 17.6 ms (1.8x)
- `render_display` 3.38 → 7.27 ms (2.2x)
- `prepare` 1.82 → 5.09 ms (2.8x)
- `surface_transfer` 4.44 → 4.63 ms (**1.04x**)

`surface_transfer` is `IOSurfaceAcceleratorTransferSurface`, a fixed-function
hardware copy. It does not slow at all while every CPU stage slows 2–3x. Memory
bandwidth, I/O, lock contention and accumulated daemon state are therefore all
excluded; the loss is CPU execution throughput alone.

Why the threads lose performance-core residency is a device-level scheduling and
thermal decision that these measurements do not resolve. They do establish what
the daemon controls, which is how much work it spends reaching that state.

## The loop the daemon controls

Capture ran at the configured 60 fps ceiling regardless of what encoding could
absorb. Each over-driven tick still paid for `CARenderServerRenderDisplay`, the
IOSurface transfer and the rotate/scale/hash preparation, and the prepared frame
was then discarded because encoding still held the only in-flight slot. From the
same warm run:

| Minute | delivered/s | captured/s | discarded/s | render+prepare per capture |
| --- | ---: | ---: | ---: | ---: |
| 1 | 59.52 | 59.70 | 0.18 (0.3%) | 5.21 ms |
| 3 | 34.22 | 54.68 | 20.47 (37.4%) | 11.90 ms |
| 4 | 32.25 | 52.93 | 20.68 (39.1%) | 12.36 ms |

About 20 captures per second were rendered, transferred and prepared purely to
be thrown away. That work is pure heat, and heat is what costs the threads their
performance cores. Slower encoding then discards more captures still.

This explains why every previous candidate failed. Two workers, four workers,
interactive output QoS and interactive worker QoS all address scheduling or
parallelism. The binding constraint is energy, so added parallelism makes the
loop worse: the four-worker warm run ended at 32.62 updates/s, no better than
serial.

## Change

`src/CaptureGovernor.h` paces capture to the rate the pipeline actually
sustains. Two signals, neither of which blocks the capture or output thread:

- `completed()` — one update finished encoding and sending (output thread).
- `blocked()` — a prepared frame could not publish for lack of in-flight
  capacity (main thread). This is the over-drive signal.

Once per display tick, on main, a 0.5-second window decides. The response is
deliberately asymmetric, because the limit being tracked moves on a multi-second
thermal time constant: shedding load briefly restores headroom that disappears
once the heat returns. Back off immediately to the measured delivered rate with
no headroom added; probe upward by 2 fps only after 20 consecutive clean windows.
A symmetric controller was measured first and rejected — it produced a 26→50 fps
sawtooth every four seconds and still declined across six minutes.

The floor is 20 fps and the ceiling is the configured `-F` maximum. Pacing is
applied through the existing `setPreferredFrameRateWithMin:preferred:max:`, so
both the display link and `TVCaptureSchedule` honour it, and a refused tick
returns before any render or transfer work.

A healthy session is never paced down: at 59.5 delivered the computed target
exceeds the 60 ceiling and clamps to it, so the governor only engages below
about 55.5 fps delivered. `IPHONEBRIDGE_CAPTURE_GOVERNOR=0` disables it, so one
identical binary can serve as its own control.

## Measurements

Same phone (iPhone14,2 / iOS 15.1.1), portrait 1172×2536, quality 6,
compression 2, unchanged `-F 60 -P 60 -d 0 -Q 1 -s 1` profile, the existing
heavy-motion Safari fixture, and one Tight sink with no Mac rendering. Encoded
bytes are discarded; this measures phone delivery, not display, input or pixels.
Runs are sequential on an increasingly warm phone and are not thermally matched.

| Minute means (updates/s) | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline `ae83019c` (no governor) | 32.95 | 16.48 | 17.48 | 17.18 | 18.28 | 18.70 |
| Symmetric controller (rejected) | 30.45 | 28.45 | 24.48 | 22.07 | 21.65 | 21.72 |
| Governor, first run | 22.92 | 20.52 | 20.33 | 20.88 | 27.95 | 29.25 |
| **Governor, confirmation** | 37.30 | 30.07 | 30.00 | 30.00 | 29.98 | 29.90 |

The confirmation run holds 30.0 updates/s for five consecutive minutes with no
decline. Against baseline: overall 20.18 → 31.21 updates/s (+54.6%), final
30 seconds 18.7 → 29.8 (+59.5%).

Inter-update gaps, which is what a user experiences as delay:

| | p50 | p95 | p99 | max |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 53.1 ms | 69.3 ms | 77.8 ms | 104.1 ms |
| Governor | 33.2 ms | 35.6 ms | 36.8 ms | 90.7 ms |

The p50–p99 spread falls from 24.7 ms to 3.6 ms. Delivery becomes regular rather
than erratic, which addresses the reported delays independently of frame rate.

The governor's first run began from the worst thermal state of the four and
still finished best, recovering from 20.3 to 29.3 updates/s within the run. That
recovery is the loop running in the intended direction: capture work removed,
heat removed, headroom returned.

## Limits

- Phone-side delivery only. Native visible fps, input latency, image comparisons
  and 22-minute memory behaviour are not qualified by these runs.
- Sustained 60 fps is not restored and is not reachable on this device with
  full-resolution CPU JPEG. The repair converts an unstable 60→17 collapse with
  erratic timing into a stable, regular rate.
- The runs are not thermally matched and the phone was charging throughout.
- Core placement is inferred from effective clock and IPC. No run logged the
  CPU number directly.

## Further work

The equilibrium is set by JPEG cost per frame, so raising it means spending
fewer joules per frame rather than scheduling differently.

### Hardware encode is feasible and removes the thermal cost (measured)

A [feasibility probe](hardware-encode-probe-2026-09-12.json) ran the production
capture path into a VideoToolbox session and discarded the output. Two results,
both on the unchanged heavy-motion fixture at the unchanged 60 fps profile:

- A **hardware HEVC session was created and driven from the daemon**. This was
  the main risk: iOS normally restricts hardware encode to foreground apps, and
  a refusal or a silent software fallback would have ended this direction.
- **300 seconds at 59.1-59.3 encoded frames/s with no decline, zero encode
  failures, and thermal state 0 throughout.**

Against the matched cold JPEG run, same fixture and rate, both starting at
thermal state 0:

| Minute | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| JPEG thermal state | 0 | 0 | 0,1 | 1,2 | **2** |
| JPEG worker CPU per update | 7.94 ms | 7.90 ms | 7.88 ms | 8.04 ms | 8.82 ms |
| Hardware thermal state | 0 | 0 | 0 | 0 | **0** |
| Hardware submit CPU per frame | — | — | ~0.073 ms | — | — |

The JPEG path drives the phone to the serious thermal category within four
minutes from cold. The hardware path does not move it off nominal in five. Wire
bytes fall from about 46 MB/s to 4.8 MB/s, which removes most of the SSH AES
cost over USB as well.

The cost is latency: submit-to-callback inside the encoder measured 20.4-20.6 ms
mean and 24.6 ms maximum, roughly one frame of pipelining. That is a real trade,
not a free win, and must be weighed against the frame interval and publication
gap it replaces.

The output is valid video. An 8-second Annex B elementary stream was captured,
transferred and decoded with ffmpeg: HEVC Main, 1170x2532, yuv420p, **475 frames
decoded with zero errors** (59.4 fps), and a decoded frame rendered to PNG shows
the phone screen with sharp text and correct colours.

No transport, VideoToolbox decode path, display, input latency or end-to-end
pixel comparison was performed.

- `ScreenCapturer` already produces a `CMSampleBufferRef`
  backed by a `CVPixelBuffer`, which is `VTCompressionSessionEncodeFrame` input.
  A fixed-function encoder does not throttle, for the same reason
  `surface_transfer` does not.
- Hardware-scaled capture. `-s` currently resamples on the CPU with
  `vImageScale_ARGB8888`. Making the destination IOSurface smaller and letting
  the accelerator scale during the transfer it already performs would cost no
  CPU.
- `TIGHT_MAX_RECT_SIZE` (65536) splits a 1172-wide update into 55-row strips,
  so a full-screen frame costs 47 separate `tjCompress` calls. Secondary.
