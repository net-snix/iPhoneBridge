// Explicit diagnostics only. Counters never read pixels or change frame delivery.
import { observeVisibility } from './visibility.mjs';

export function installRenderMetrics(mirror) {
  const visibility = observeVisibility(document, performance);
  const id = `renderer-${Date.now()}`;
  let client = null, undo = [], generation = 0, stopped = false, timer = null;
  let frames = 0, images = 0, flips = 0;
  let previous = { at: performance.now(), frames: 0, images: 0, flips: 0, hiddenMs: 0 };

  function release() { for (const restore of undo) restore(); undo = []; }
  function wrap(object, name, wrapper) {
    const original = object[name];
    const replacement = wrapper(original);
    object[name] = replacement;
    undo.push(() => { if (object[name] === replacement) object[name] = original; });
  }
  async function sample() {
    if (stopped) return;
    const next = mirror.currentClient();
    const changed = next !== client;
    if (changed) {
      release(); client = next;
      if (client) {
        generation++;
        wrap(client, '_framebufferUpdate', original => function(...args) {
          const complete = original.apply(this, args);
          if (complete) frames++;
          return complete;
        });
        wrap(client._display, 'imageRect', original => function(...args) {
          images++;
          return original.apply(this, args);
        });
        wrap(client._display, 'flip', original => function(fromQueue) {
          const queued = this.pending() && !fromQueue;
          const result = original.call(this, fromQueue);
          if (!queued) flips++;
          return result;
        });
      }
    }
    const now = performance.now(), state = visibility.snapshot();
    const elapsed = now - previous.at;
    const report = { mode: 'renderer-health', id, recordedAt: Date.now(),
      generation, connected: Boolean(client), elapsedMs: elapsed, frames, images, flips,
      frameFps: changed ? null : (frames - previous.frames) * 1000 / elapsed,
      flipFps: changed ? null : (flips - previous.flips) * 1000 / elapsed,
      imagesPerSecond: changed ? null : (images - previous.images) * 1000 / elapsed,
      renderQueue: client?._display._renderQ.length ?? null,
      decoderFrames: document.querySelectorAll('iframe').length,
      visibility: state, hiddenMsDuringSample: state.hiddenMs - previous.hiddenMs };
    previous = { at: now, frames, images, flips, hiddenMs: state.hiddenMs };
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 3000);
    try {
      await fetch('http://127.0.0.1:15802/metrics', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(report), signal: controller.signal });
    } catch { /* The opt-in fixture server may be stopped independently. */ }
    finally {
      clearTimeout(deadline);
      if (!stopped) timer = setTimeout(sample, 5000);
    }
  }
  timer = setTimeout(sample, 0);
  return { stop() {
    stopped = true;
    clearTimeout(timer);
    release();
    visibility.stop();
  } };
}
