// Explicit diagnostics only. Normal mirroring never imports this module.
// Compare identical encoded bytes through the old and current decode paths.
export function comparePixels(before, after, width, height) {
  if (before.length !== width * height * 4 || after.length !== before.length) {
    throw new Error('Pixel buffer dimensions do not match');
  }
  let differentPixels = 0, differentChannels = 0, maxChannelDifference = 0;
  let firstDifference = null;
  for (let offset = 0; offset < before.length; offset += 4) {
    let changed = false;
    for (let channel = 0; channel < 4; channel++) {
      const difference = Math.abs(before[offset + channel] - after[offset + channel]);
      if (!difference) continue;
      changed = true;
      differentChannels++;
      maxChannelDifference = Math.max(maxChannelDifference, difference);
      firstDifference ??= {
        x: (offset / 4) % width, y: Math.floor(offset / 4 / width), channel,
        before: before[offset + channel], after: after[offset + channel],
      };
    }
    if (changed) differentPixels++;
  }
  return { exactMatch: differentPixels === 0, differentPixels, differentChannels,
    maxChannelDifference, firstDifference };
}

export async function readImageDecoderCapability(environment = globalThis) {
  const capability = { imageDecoderType: null, jpeg: null, png: null, error: null };
  let timer;
  try {
    const decoder = environment.ImageDecoder;
    capability.imageDecoderType = typeof decoder;
    if (typeof decoder === 'undefined') return capability;
    if (typeof decoder?.isTypeSupported !== 'function') {
      throw new Error('ImageDecoder.isTypeSupported is unavailable');
    }
    const types = ['jpeg', 'png'];
    const results = await Promise.race([
      Promise.allSettled(types.map(type => Promise.resolve().then(() => decoder.isTypeSupported(`image/${type}`)))),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('ImageDecoder capability check timed out')), 2000);
      }),
    ]);
    const errors = [];
    for (let index = 0; index < types.length; index++) {
      const result = results[index];
      if (result.status === 'fulfilled') capability[types[index]] = result.value;
      else errors.push(`image/${types[index]}: ${result.reason?.message ?? String(result.reason)}`);
    }
    if (errors.length) capability.error = errors.join('; ');
  } catch (error) {
    capability.error = error?.message ?? String(error);
  } finally {
    clearTimeout(timer);
  }
  return capability;
}

export function installImageQuality() {
  let stopped = false, busy = false, stopReason = 'Image quality diagnostic stopped';
  const pending = new Set();
  const cases = [
    { name: 'full-resolution-png', width: 1172, height: 2536, mime: 'image/png' },
    { name: 'full-resolution-jpeg', width: 1172, height: 2536, mime: 'image/jpeg', quality: 0.8 },
    { name: 'translucent-png', width: 32, height: 24, mime: 'image/png', translucent: true },
    { name: 'translucent-png-composited', width: 32, height: 24, mime: 'image/png',
      translucent: true, background: '#21394f' },
  ];

  function stop(reason = 'Image quality diagnostic stopped') {
    stopReason = reason;
    stopped = true;
    for (const cancel of [...pending]) cancel();
  }

  function bounded(start, label) {
    if (stopped) return Promise.reject(new Error(stopReason));
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        pending.delete(cancel);
        if (error) reject(error); else resolve(value);
      };
      const cancel = () => finish(new Error(stopReason));
      const timer = setTimeout(() => finish(new Error(`${label} timed out`)), 10000);
      pending.add(cancel);
      try { start(finish); } catch (error) { finish(error); }
    });
  }

  function canvas(width, height) {
    const result = document.createElement('canvas');
    result.width = width; result.height = height;
    return result;
  }

  function pattern(spec) {
    const result = canvas(spec.width, spec.height);
    const context = result.getContext('2d');
    const pixels = context.createImageData(spec.width, spec.height);
    for (let y = 0; y < spec.height; y++) {
      for (let x = 0; x < spec.width; x++) {
        const offset = (y * spec.width + x) * 4;
        pixels.data[offset] = Math.floor(x * 255 / (spec.width - 1));
        pixels.data[offset + 1] = Math.floor(y * 255 / (spec.height - 1));
        pixels.data[offset + 2] = ((x >> 5) ^ (y >> 5)) & 1 ? 224 : 24;
        pixels.data[offset + 3] = spec.translucent ? (x * 17 + y * 31) & 255 : 255;
      }
    }
    context.putImageData(pixels, 0, 0);
    return result;
  }

  async function compareCase(spec, ImageResource, Base64) {
    let source, baseline, baselineCanvas, currentCanvas, resource;
    let loaded, failed;
    try {
      source = pattern(spec);
      const encoded = await bounded(finish => source.toBlob(blob => {
        finish(blob ? null : new Error('Canvas image encoding failed'), blob);
      }, spec.mime, spec.quality), 'Canvas image encoding');
      if (encoded.type !== spec.mime) throw new Error(`Browser did not encode ${spec.mime}`);
      const bytes = await bounded(finish => {
        encoded.arrayBuffer().then(value => finish(null, value), finish);
      }, 'Encoded image bytes');
      baselineCanvas = canvas(spec.width, spec.height);
      currentCanvas = canvas(spec.width, spec.height);
      const contexts = [baselineCanvas.getContext('2d'), currentCanvas.getContext('2d')];
      if (spec.background) {
        for (const context of contexts) {
          context.fillStyle = spec.background;
          context.fillRect(0, 0, spec.width, spec.height);
        }
      }

      const baselineStart = performance.now();
      baseline = new Image();
      await bounded(finish => {
        loaded = () => finish(null);
        failed = () => finish(new Error('Baseline data URL image failed to decode'));
        baseline.addEventListener('load', loaded);
        baseline.addEventListener('error', failed);
        baseline.src = `data: ${spec.mime};base64,${Base64.encode(new Uint8Array(bytes))}`;
      }, 'Baseline image decode');
      if (baseline.naturalWidth !== spec.width || baseline.naturalHeight !== spec.height) {
        throw new Error('Baseline decoded image dimensions differ');
      }
      contexts[0].drawImage(baseline, 0, 0);
      const baselineDecodeAndDrawMs = performance.now() - baselineStart;
      baseline.removeEventListener('load', loaded);
      baseline.removeEventListener('error', failed);
      baseline.src = '';

      const currentStart = performance.now();
      await bounded(finish => {
        resource = new ImageResource(new Uint8Array(bytes), spec.mime, spec.width, spec.height,
          () => finish(null), error => finish(error));
        resource.start();
      }, 'Current image decode');
      contexts[1].drawImage(resource.image, 0, 0);
      const currentDecodeAndDrawMs = performance.now() - currentStart;
      resource.dispose();

      const comparisonStart = performance.now();
      const comparison = comparePixels(
        contexts[0].getImageData(0, 0, spec.width, spec.height).data,
        contexts[1].getImageData(0, 0, spec.width, spec.height).data,
        spec.width, spec.height);
      return { ...spec, encodedBytes: bytes.byteLength, baselineDecodeAndDrawMs,
        currentDecodeAndDrawMs, comparisonMs: performance.now() - comparisonStart, ...comparison };
    } finally {
      if (baseline) {
        baseline.removeEventListener('load', loaded);
        baseline.removeEventListener('error', failed);
        baseline.src = '';
      }
      resource?.dispose();
      for (const value of [source, baselineCanvas, currentCanvas]) {
        if (value) value.width = value.height = 0;
      }
    }
  }

  async function run(options = {}) {
    if (busy) throw new Error('Image quality diagnostic is already running');
    busy = true;
    const report = {
      schemaVersion: 1, id: options.id, label: options.label ?? '', mode: 'image-quality',
      timeOrigin: performance.timeOrigin, startedAt: performance.now(),
      userAgent: navigator.userAgent, cases: [], failure: null,
      comparison: 'Exact RGBA comparison after decoding the same encoded bytes',
    };
    let currentCase = null;
    // This read-only probe runs independently: unsupported APIs and probe
    // failures never change the existing decoder or pixel comparison result.
    const capability = readImageDecoderCapability();
    const deadline = setTimeout(() => stop('Image quality diagnostic exceeded 45s'), 45000);
    try {
      const [{ default: ImageResource }, { default: Base64 }] = await bounded(finish => {
        Promise.all([import('/novnc/core/image-resource.js'), import('/novnc/core/base64.js')])
          .then(value => finish(null, value), finish);
      }, 'Diagnostic module load');
      for (const spec of cases) {
        currentCase = spec.name;
        const result = await compareCase(spec, ImageResource, Base64);
        report.cases.push(result);
        if (!result.exactMatch) {
          report.failure ??= { case: spec.name, reason: 'Decoded pixel buffers differ' };
        }
      }
    } catch (error) {
      report.failure = { case: currentCase, reason: error.message };
    } finally {
      clearTimeout(deadline);
      report.finishedAt = performance.now();
      report.completedCases = report.cases.length;
      report.requestedCases = cases.length;
      report.exactMatch = report.failure === null && report.completedCases === cases.length;
      busy = false;
    }
    report.capability = await capability;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    try {
      const response = await fetch('http://127.0.0.1:15802/metrics', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(report), signal: controller.signal,
      });
      report.posted = response.ok;
      if (!response.ok) report.postError = `Metrics endpoint returned ${response.status}`;
    } catch { report.posted = false; report.postError = 'Metrics endpoint unavailable'; }
    finally { clearTimeout(timer); }
    return report;
  }

  return { run, stop };
}
