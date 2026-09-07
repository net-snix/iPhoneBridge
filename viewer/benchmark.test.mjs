import test from 'node:test';
import assert from 'node:assert/strict';
import { installBenchmark, locateBarcode, decodeBarcodeRow } from './benchmark.mjs';

const COLORS = [[16,16,16], [240,240,240], [255,48,48], [48,255,48], [48,48,255],
  [48,255,255], [255,255,48], [255,48,255], [255,144,48]];

function barcode(sequence, { noise = 0, valid = true } = {}) {
  const width = 600, height = 96, x = 31, pitch = 11.5;
  const data = new Uint8ClampedArray(width * height * 4);
  const cells = [2,3,4,5];
  for (let bit = 31; bit >= 0; bit--) cells.push((sequence >>> bit) & 1);
  cells.push(6,7,5,8);
  if (!valid) cells[38] = 0;
  for (let y = 24; y < 72; y++) {
    for (let px = x; px < x + 40 * pitch; px++) {
      const color = COLORS[cells[Math.floor((px - x) / pitch)]];
      for (let channel = 0; channel < 3; channel++) {
        data[(y * width + px) * 4 + channel] = color[channel] + (((px + channel) % 3) - 1) * noise;
      }
      data[(y * width + px) * 4 + 3] = 255;
    }
  }
  return { data, width, height };
}

function rig({ valid = true, latency = 64, respond = true, animation = false,
  visibleAt = 0, unstableUntil = 0, modeChangesAt = Infinity } = {}) {
  let now = 0, nextId = 0, sequence = 0;
  const tasks = new Map(), keys = [], reports = [];
  const schedule = (fn, delay = 0) => { const id = ++nextId; tasks.set(id, { fn, at: now + delay }); return id; };
  const cancel = id => tasks.delete(id);
  const frame = () => {
    const count = now < unstableUntil ? Math.floor(now / 32) : sequence;
    const value = animation || now >= modeChangesAt ? (Math.floor(now / 32) | 0x80000000) >>> 0 : count;
    return barcode(value, { valid: valid && now >= visibleAt, noise: 15 });
  };
  const canvas = { width: 600, height: 96,
    getContext: () => ({ getImageData: (_x, y) => {
      const image = frame();
      return { width: image.width, height: 1, data: image.data.subarray(y * image.width * 4, (y + 1) * image.width * 4) };
    } }),
  };
  const client = {
    compressionLevel: 2, qualityLevel: 6, getImageData: frame,
    sendKey(key, code, down) {
      keys.push({ key, code, down, now });
      if (down && respond) schedule(() => { sequence = (sequence + 1) >>> 0; }, latency);
    },
  };
  const environment = {
    performance: { now: () => now, timeOrigin: 100000 },
    setTimeout: schedule, clearTimeout: cancel,
    requestAnimationFrame: fn => schedule(fn, 16), cancelAnimationFrame: cancel,
    fetch: async (url, options) => { reports.push({ url, data: JSON.parse(options.body) }); return { ok: true }; },
  };
  const runner = installBenchmark({ client, screen: { querySelector: () => canvas }, environment });
  async function drive(promise) {
    let finished = false, value, error;
    promise.then(result => { value = result; finished = true; }, reason => { error = reason; finished = true; });
    for (let turn = 0; !finished && turn < 10000; turn++) {
      for (let i = 0; i < 8; i++) await Promise.resolve();
      if (finished) break;
      const task = [...tasks.entries()].sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      assert.ok(task, 'operation must stay bounded by a timer');
      tasks.delete(task[0]); now = task[1].at; task[1].fn();
    }
    assert.ok(finished, 'benchmark finished within bounded scheduler steps');
    if (error) throw error;
    return value;
  }
  return { runner, client, keys, reports, drive, schedule };
}

test('locates scaled/JPEG-noisy header and footer, decodes big-endian uint32', () => {
  for (const sequence of [0, 1, 0x80000000, 0x12345678, 0xffffffff]) {
    const image = barcode(sequence, { noise: 24 });
    const location = locateBarcode(image);
    assert.ok(location);
    const row = { width: image.width, data: image.data.subarray(location.y * image.width * 4) };
    assert.equal(decodeBarcodeRow(row, location), sequence);
  }
});

test('rejects missing footer and arbitrary application pixels', () => {
  assert.equal(locateBarcode(barcode(7, { valid: false })), null);
  assert.equal(locateBarcode({ width: 600, height: 96, data: new Uint8ClampedArray(600 * 96 * 4) }), null);
});

test('input guard sends no key when live fixture is missing', async () => {
  const fixture = rig({ valid: false });
  const report = await fixture.drive(fixture.runner.runInput({ id: 'guard', label: 'baseline' }));
  assert.equal(fixture.keys.length, 0);
  assert.equal(report.completedTrials, 0);
  assert.match(report.failure.reason, /no input sent/);
  assert.equal(fixture.reports[0].data.id, 'guard');
  assert.equal(fixture.reports[0].url, 'http://127.0.0.1:15802/metrics');
});

test('input observes physical sequence response and 20ms key release', async () => {
  const fixture = rig();
  const report = await fixture.drive(fixture.runner.runInput({ trials: 3, compressionLevel: 1 }));
  assert.equal(report.completedTrials, 3);
  assert.equal(report.failure, null);
  assert.deepEqual(report.samples, [64,64,64]);
  assert.equal(report.p50, 64); assert.equal(report.p95, 64);
  assert.equal(report.timeOrigin, 100000);
  assert.equal(report.compressionLevel, 1); assert.equal(report.qualityLevel, 6);
  assert.equal(fixture.keys.length, 6);
  assert.ok(report.events[0].sentAt - report.calibration.stableSince >= 100);
  for (let i = 0; i < 6; i += 2) {
    assert.equal(fixture.keys[i].down, true); assert.equal(fixture.keys[i + 1].down, false);
    assert.equal(fixture.keys[i + 1].now - fixture.keys[i].now, 20);
    if (i > 0) assert.ok(fixture.keys[i].now - report.events[i / 2 - 1].observedAt >= 100);
  }
});

test('very fast response still keeps requested 20ms press duration', async () => {
  const fixture = rig({ latency: 1 });
  const report = await fixture.drive(fixture.runner.runInput({ trials: 1 }));
  assert.equal(report.samples[0], 16);
  assert.equal(fixture.keys[1].now - fixture.keys[0].now, 20);
});

test('input timeout aborts after one key, never retries blindly', async () => {
  const fixture = rig({ respond: false });
  const report = await fixture.drive(fixture.runner.runInput({ trials: 30 }));
  assert.equal(fixture.keys.length, 2);
  assert.equal(report.completedTrials, 0);
  assert.equal(report.failure.timeout, true);
  assert.match(report.failure.reason, /no retry/);
  assert.equal(report.finishedAt - fixture.keys[0].now, 2000);
});

test('animation measures distinct sequences without any input', async () => {
  const fixture = rig({ animation: true });
  const report = await fixture.drive(fixture.runner.runAnimation({ durationMs: 1000, id: 'frames' }));
  assert.equal(fixture.keys.length, 0);
  assert.equal(report.failure, null);
  assert.ok(report.uniqueSequences >= 30);
  assert.equal(report.sourceFramesSkipped, 0);
  assert.ok(report.receivedFps > 30 && report.receivedFps < 33);
  assert.equal(report.p50, 32);
  assert.equal(report.finishedAt, 1000);
});

test('stop releases an in-flight key and cancels further trials', async () => {
  const fixture = rig({ respond: false });
  const pending = fixture.runner.runInput();
  fixture.schedule(() => fixture.runner.stop(), 133);
  const report = await fixture.drive(pending);
  assert.equal(fixture.keys.length, 2);
  assert.equal(fixture.keys[1].now, 133);
  assert.match(report.failure.reason, /stopped/);
});

test('input rejects animation high bit before sending any key', async () => {
  const fixture = rig({ animation: true });
  const report = await fixture.drive(fixture.runner.runInput());
  assert.equal(fixture.keys.length, 0);
  assert.match(report.failure.reason, /Animation fixture detected/);
});

test('calibration waits for delayed page load without input', async () => {
  const fixture = rig({ visibleAt: 500 });
  const report = await fixture.drive(fixture.runner.runInput({ trials: 1 }));
  assert.equal(report.failure, null);
  assert.ok(fixture.keys[0].now >= 600);
  assert.equal(report.completedTrials, 1);
  assert.equal(report.samples[0], 64);
});

test('calibration requires 100ms stable sequence and excludes calibration time from latency', async () => {
  const fixture = rig({ unstableUntil: 400 });
  const report = await fixture.drive(fixture.runner.runInput({ trials: 1 }));
  assert.equal(report.failure, null);
  assert.ok(fixture.keys[0].now >= 500);
  assert.deepEqual(report.samples, [64]);
});

test('continuously moving unmarked sequence never receives input', async () => {
  const fixture = rig({ unstableUntil: Infinity });
  const report = await fixture.drive(fixture.runner.runInput());
  assert.equal(fixture.keys.length, 0);
  assert.equal(report.failure.timeout, true);
  assert.equal(report.finishedAt, 2000);
});

test('animation switch between trials prevents further input', async () => {
  const fixture = rig({ modeChangesAt: 250 });
  const report = await fixture.drive(fixture.runner.runInput({ trials: 3 }));
  assert.equal(report.completedTrials, 1);
  assert.equal(fixture.keys.length, 2);
  assert.match(report.failure.reason, /Animation fixture detected/);
});
