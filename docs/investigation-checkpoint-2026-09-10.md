# Paused investigation — 10 September 2026

Saved at the user's request. Benchmarks and agent work are stopped. Resume only
when requested. No release or push was performed.

## Current state

- The latest serial control finished before shutdown. Both recent test runners
  report successful collection and restoration of the normal `3487cfad` daemon.
- The native benchmark app is closed. Shared USB, SSH, viewer and fixture services
  were preserved. The installed application was not replaced by these trials.
- Repository changes are an investigation checkpoint, not a qualified release.
  The earlier `ae83019c` capture change and the JPEG experiments still require
  sustained native verification before promotion.

## Findings saved today

Serial JPEG compression accounts for about 89% of warmed output CPU time:
18.3 ms of 20.4 ms per completed update. Raising output-thread QoS did not fix the
decline. Capture itself continued near 60 updates/s.

The first two-worker JPEG candidate preserves complete wire bytes in the host
tests. Its five-minute phone trial improved final-minute delivery to 42.58 fps,
but still declined from 59.4 fps. It is not the complete fix.

The following serial control used the same combined sources, codec archives,
timing and negotiation serialization, with JPEG workers disabled. It ended at
29.87 fps, with 20.85 ms coordinator CPU and 23.14 ms send wall time. Both trials
have complete timing pairs, stable negotiated profiles and zero reported errors.
Physical temperature and CPU frequency were not controlled, so these are not
thermally matched measurements.

The two-worker implementation drains around 20 separate Tight regions per frame,
with about 43 JPEG jobs. Those boundaries limit possible overlap. Its final-minute
send time was 15.99 ms, followed by 7.49 ms before the next send. Capture callbacks
and queued publication also overlap that interval; the full remaining delay has
not been causally attributed.

## Saved evidence and staged work

All experiment sources, raw measurements, build manifests and runners remain in
`work/renderer-memory-20260909/` (intentionally ignored by Git):

- `jpeg-stage-sink-3483/`: serial JPEG CPU attribution.
- `jpeg-pipeline-sink-c89d/`: completed two-worker trial; restored normally.
- `jpeg-pipeline-control-sink-184a/`: completed serial control; restored normally.
- `jpeg-pipeline-candidate/combined/verification.json`: full pinned-codec wire,
  lifetime, failure and timing integration checks; all five UBSan groups pass.
- `jpeg-pipeline-negotiation/`: frozen configuration/lifetime prerequisite.
- `jpeg-pipeline-cpu-timing/`: frozen coordinator/worker CPU diagnostic.
- `jpeg-fbu-pipeline-candidate/`: next candidate, interrupted during source/test
  editing. **Not frozen, compiled, tested or installed.**

The next candidate retains the public synchronous Tight API while allowing the
framebuffer-update loop to batch JPEG work across regions. Successful completion
must drain before LastRect; all errors must abort and drain before the software
cursor is hidden or source pixels can change. Add disjoint-region and software
cursor regression cases before any phone trial.

After a candidate passes a short warmed test, remaining qualification includes
22 minutes of visible native cadence and memory, input latency before/after,
image quality, three slow-reader final-frame checks against independent USB
captures, and rotation/reconnection behavior. ASan could not start on this Mac;
UBSan passes do not replace that missing check or device verification.
