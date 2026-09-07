// Opt-in physical-phone benchmark. Original iPhoneBridge code, MIT.
const COLORS = [
  [16, 16, 16], [240, 240, 240], [255, 48, 48], [48, 255, 48],
  [48, 48, 255], [48, 255, 255], [255, 255, 48], [255, 48, 255], [255, 144, 48],
];
const MARKERS = [[0, 2], [1, 3], [2, 4], [3, 5], [36, 6], [37, 7], [38, 5], [39, 8]];

function colorAt(data, offset) {
  let best = -1, distance = 11026;
  for (let index = 0; index < COLORS.length; index++) {
    const color = COLORS[index];
    const score = (data[offset] - color[0]) ** 2 +
      (data[offset + 1] - color[1]) ** 2 + (data[offset + 2] - color[2]) ** 2;
    if (score < distance) { best = index; distance = score; }
  }
  return best;
}

export function decodeBarcodeRow(row, location) {
  const sample = cell => {
    const x = Math.round(location.x + (cell + 0.5) * location.pitch);
    if (x < 0 || x >= row.width) return -1;
    return colorAt(row.data, x * 4);
  };
  if (MARKERS.some(([cell, color]) => sample(cell) !== color)) return null;
  let sequence = 0;
  for (let cell = 4; cell < 36; cell++) {
    const bit = sample(cell);
    if (bit !== 0 && bit !== 1) return null;
    sequence = (sequence * 2 + bit) >>> 0;
  }
  return sequence;
}

export function locateBarcode(frame) {
  // Header/footer recognition prevents arbitrary app pixels becoming input targets.
  for (let y = 6; y < frame.height; y += 12) {
    const data = frame.data.subarray(y * frame.width * 4, (y + 1) * frame.width * 4);
    const runs = [];
    let previous = -2;
    for (let x = 0; x < frame.width; x++) {
      const color = colorAt(data, x * 4);
      if (color !== previous) {
        runs.push({ color, start: x, end: x + 1 });
        previous = color;
      } else runs[runs.length - 1].end = x + 1;
    }
    for (let index = 0; index + 3 < runs.length; index++) {
      const header = runs.slice(index, index + 4);
      if (!header.every((run, i) => run.color === i + 2)) continue;
      const pitch = (header[3].end - header[0].start) / 4;
      if (pitch < 3 || header.some(run => Math.abs(run.end - run.start - pitch) > pitch * 0.35)) continue;
      const location = { x: header[0].start, y, pitch, width: frame.width, height: frame.height };
      if (decodeBarcodeRow({ data, width: frame.width }, location) !== null) return location;
    }
  }
  return null;
}

function percentile(samples, fraction) {
  if (!samples.length) return null;
  const sorted = [...samples].sort((a, b) => a - b);
  const position = (sorted.length - 1) * fraction;
  const low = Math.floor(position), high = Math.ceil(position);
  return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
}

export function installBenchmark({ client, screen, compressionLevel, label = '', environment = {} }) {
  const clock = environment.performance ?? performance;
  const raf = environment.requestAnimationFrame ?? requestAnimationFrame;
  const caf = environment.cancelAnimationFrame ?? cancelAnimationFrame;
  const schedule = environment.setTimeout ?? setTimeout;
  const cancel = environment.clearTimeout ?? clearTimeout;
  const send = environment.fetch ?? fetch;
  let location = null, lastSearch = -Infinity, stopped = false, busy = false;
  let cancelWait = null, releaseKey = null;

  if (compressionLevel !== undefined) setCompression(compressionLevel);

  function setCompression(level) {
    if (!Number.isInteger(level) || level < 0 || level > 9) throw new Error('compressionLevel must be 0–9');
    client.compressionLevel = level;
  }

  function readSequence(searchIntervalMs = 1000) {
    const canvas = screen.querySelector('canvas');
    if (!canvas || !canvas.width || !canvas.height) return null;
    if (location && (canvas.width !== location.width || canvas.height !== location.height)) location = null;
    if (location) {
      const row = canvas.getContext('2d').getImageData(0, location.y, canvas.width, 1);
      const sequence = decodeBarcodeRow(row, location);
      if (sequence !== null) return sequence;
    }
    if (clock.now() - lastSearch < searchIntervalMs) return null;
    lastSearch = clock.now();
    location = locateBarcode(client.getImageData());
    if (!location) return null;
    const row = canvas.getContext('2d').getImageData(0, location.y, canvas.width, 1);
    return decodeBarcodeRow(row, location);
  }

  function observe(check, durationMs, searchIntervalMs = 1000) {
    return new Promise(resolve => {
      let frame = null, timer = null, done = false;
      function finish(value) {
        if (done) return;
        done = true;
        caf(frame); cancel(timer);
        cancelWait = null;
        resolve(value);
      }
      function tick() {
        if (stopped) return finish({ failure: 'Benchmark stopped' });
        try {
          const result = check(readSequence(searchIntervalMs), clock.now());
          if (result) return finish(result);
        } catch {
          return finish({ failure: 'Cannot read the mirror canvas' });
        }
        frame = raf(tick);
      }
      cancelWait = () => finish({ failure: 'Benchmark stopped' });
      timer = schedule(() => finish({ timeout: true }), durationMs);
      frame = raf(tick);
    });
  }

  async function pause(durationMs) {
    await new Promise(resolve => {
      const timer = schedule(() => { cancelWait = null; resolve(); }, durationMs);
      cancelWait = () => { cancel(timer); cancelWait = null; resolve(); };
    });
  }

  function startReport(mode, options) {
    if (busy) throw new Error('A benchmark is already running');
    if (stopped) throw new Error('Benchmark runner has stopped');
    if (options.compressionLevel !== undefined) setCompression(options.compressionLevel);
    busy = true;
    return { mode, label: options.label ?? label, id: options.id ?? null,
      compressionLevel: client.compressionLevel, qualityLevel: client.qualityLevel,
      timeOrigin: clock.timeOrigin, startedAt: clock.now(), samples: [], events: [], failure: null };
  }

  async function finishReport(report) {
    report.finishedAt = clock.now();
    report.p50 = percentile(report.samples, 0.5);
    report.p95 = percentile(report.samples, 0.95);
    report.sampleUnit = 'milliseconds';
    report.barcode = location;
    try {
      const controller = new AbortController();
      const timer = schedule(() => controller.abort(), 3000);
      try {
        const response = await send('http://127.0.0.1:15802/metrics', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(report), signal: controller.signal,
        });
        report.posted = response.ok;
        if (!response.ok) report.postError = `Metrics endpoint returned ${response.status}`;
      } finally { cancel(timer); }
    } catch { report.posted = false; report.postError = 'Metrics endpoint unavailable'; }
    finally { busy = false; }
    return report;
  }

  async function runInput(options = {}) {
    const trials = options.trials ?? 30;
    if (!Number.isInteger(trials) || trials < 1 || trials > 100) throw new Error('trials must be 1–100');
    const report = startReport('input', options);
    report.requestedTrials = trials;
    try {
      let candidate = null, stableSince = null;
      const calibration = await observe((sequence, now) => {
        if (sequence !== null && sequence >= 0x80000000) {
          return { failure: 'Animation fixture detected; no input sent' };
        }
        if (sequence === null) { candidate = null; stableSince = null; return null; }
        if (sequence !== candidate) { candidate = sequence; stableSince = now; return null; }
        return now - stableSince >= 100 ? { sequence, stableSince, calibratedAt: now } : null;
      }, 2000, 250);
      if (calibration.timeout || calibration.failure) {
        report.failure = { reason: calibration.failure ?? 'No stable input fixture barcode within 2s; no input sent',
          timeout: Boolean(calibration.timeout) };
      } else {
        report.calibration = calibration;
      }
      for (let trial = 0; trial < trials; trial++) {
        if (report.failure) break;
        if (stopped) { report.failure = { trial, reason: 'Benchmark stopped' }; break; }
        const baseline = readSequence();
        if (baseline === null) {
          report.failure = { trial, reason: 'Live fixture barcode not found; no input sent' }; break;
        }
        if (baseline >= 0x80000000) {
          report.failure = { trial, reason: 'Animation fixture detected; no further input sent' }; break;
        }
        const expected = (baseline + 1) >>> 0;
        const started = clock.now();
        const pending = observe((sequence, now) => {
          if (sequence === null || sequence === baseline) return null;
          if (sequence >= 0x80000000) return { failure: 'Animation fixture detected; no further input sent', sequence };
          if (sequence !== expected) return { failure: 'Unexpected sequence; no further input sent', sequence };
          return { sequence, observedAt: now, latency: now - started };
        }, 2000);
        let releaseTimer = null;
        let held = true;
        releaseKey = () => {
          if (!held) return;
          held = false;
          cancel(releaseTimer);
          client.sendKey(0x20, 'Space', false);
        };
        let result;
        try {
          client.sendKey(0x20, 'Space', true);
          releaseTimer = schedule(() => {
            try { releaseKey?.(); } catch { cancelWait?.(); }
          }, 20);
          result = await pending;
          if (held && !stopped && clock.now() < started + 20) await pause(started + 20 - clock.now());
        } finally {
          releaseKey?.();
          releaseKey = null;
          cancel(releaseTimer);
        }
        if (result.timeout || result.failure) {
          report.failure = { trial, reason: result.failure ?? 'Sequence response timed out; no retry',
            timeout: Boolean(result.timeout), baseline, expected, observed: result.sequence ?? null };
          break;
        }
        report.samples.push(result.latency);
        report.events.push({ baseline, sequence: result.sequence, sentAt: started, observedAt: result.observedAt });
        if (trial + 1 < trials) await pause(100);
      }
    } catch {
      cancelWait?.();
      report.failure = { reason: 'Benchmark connection or canvas failed; no further input sent' };
    }
    report.completedTrials = report.samples.length;
    return finishReport(report);
  }

  async function runAnimation(options = {}) {
    const durationMs = options.durationMs ?? 10000;
    if (!Number.isFinite(durationMs) || durationMs < 100 || durationMs > 30000) throw new Error('durationMs must be 100–30000');
    const report = startReport('animation', options);
    report.requestedDurationMs = durationMs;
    report.uniqueSequences = 0;
    report.sourceFramesSkipped = 0;
    let previous = null, observedAt = null;
    const result = await observe((sequence, now) => {
      if (sequence === null || sequence === previous) return null;
      if (previous !== null) {
        const delta = (sequence - previous) >>> 0;
        if (delta >= 0x80000000) return { failure: 'Sequence moved backwards or fixture changed' };
        report.sourceFramesSkipped += delta - 1;
        report.samples.push(now - observedAt);
      }
      report.uniqueSequences++;
      report.events.push({ sequence, observedAt: now });
      previous = sequence;
      observedAt = now;
      return null;
    }, durationMs);
    if (result.failure) report.failure = { reason: result.failure };
    if (!report.uniqueSequences) report.failure = { reason: 'No live fixture barcode observed' };
    const elapsed = report.events.length > 1 ? report.events.at(-1).observedAt - report.events[0].observedAt : 0;
    report.receivedFps = elapsed ? (report.uniqueSequences - 1) * 1000 / elapsed : 0;
    return finishReport(report);
  }

  function stop() {
    stopped = true;
    cancelWait?.();
    releaseKey?.();
  }

  return { runInput, runAnimation, stop };
}
