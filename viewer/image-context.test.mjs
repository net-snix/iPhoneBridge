import assert from 'node:assert/strict';
import test from 'node:test';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const source = resolve(process.env.IPHONEBRIDGE_NOVNC_SOURCE ?? 'work/vendor/noVNC');
const { default: ImageDecodeContext } = await import(pathToFileURL(resolve(source, 'core/image-context.js')).href);
const { default: ImageResource } = await import(pathToFileURL(resolve(source, 'core/image-resource.js')).href);
const LIMIT = 4096;

function rig() {
  const frames = [], attached = new Set();
  let maximumAttached = 0;
  const document = {
    createElement(tag) {
      assert.equal(tag, 'iframe');
      if (document.createFailure) throw new Error('iframe creation failed');
      const frame = {
        hidden: false, attributes: {}, removed: 0,
        contentDocument: {
          id: frames.length,
          createElement(tag) { assert.equal(tag, 'img'); return document.makeImage(this); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        remove() { this.removed++; attached.delete(this); document.onRemove?.(); },
      };
      frames.push(frame);
      if (document.unavailableDocument) frame.contentDocument = null;
      return frame;
    },
    body: {
      appendChild(frame) {
        assert.equal(frame.hidden, true);
        assert.equal(frame.attributes['aria-hidden'], 'true');
        assert.equal(frame.tabIndex, -1);
        assert.equal(frame.src, undefined, 'use the initial same-origin about:blank document');
        attached.add(frame);
        maximumAttached = Math.max(maximumAttached, attached.size);
        document.onAppend?.();
        if (document.appendFailure) throw new Error('iframe attachment failed');
      },
    },
  };
  const context = new ImageDecodeContext(document);
  return { context, document, frames, attached, maximumAttached: () => maximumAttached };
}

async function fill(context, count = LIMIT) {
  return Promise.all(Array.from({ length: count }, () => context.acquire()));
}
async function tick() { for (let i = 0; i < 3; i++) await Promise.resolve(); }

test('context ownership is explicit and a frame is created only for an admission', async () => {
  assert.throws(() => new ImageDecodeContext(), /owner document/);
  const { context, frames, attached } = rig();
  assert.equal(frames.length, 0);
  const lease = await context.acquire();
  assert.equal(frames.length, 1);
  assert.equal(lease.document, frames[0].contentDocument);
  assert.equal(attached.size, 1);
  lease.release(); lease.release();
  assert.equal(lease.document, null, 'a released lease cannot retain its document');
  assert.equal(lease.signal, null);
  context.dispose(); context.dispose();
  assert.equal(attached.size, 0);
  assert.equal(frames[0].removed, 1);
});

test('the 4096-job boundary waits for every lease before replacing the only frame', async () => {
  const { context, frames, attached, maximumAttached } = rig();
  const leases = await fill(context);
  const firstDocument = leases[0].document;
  assert.ok(leases.every(lease => lease.document === firstDocument));
  let admitted = false;
  const next = context.acquire().then(lease => { admitted = true; return lease; });
  await tick();
  assert.equal(admitted, false);
  for (const lease of leases.slice(1)) { lease.release(); lease.release(); }
  await tick();
  assert.equal(admitted, false, 'out-of-order completion cannot retire an active document');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].removed, 0);
  leases[0].release();
  const resumed = await next;
  assert.equal(frames[0].removed, 1);
  assert.notEqual(resumed.document, firstDocument);
  assert.equal(attached.size, 1);
  assert.equal(maximumAttached(), 1);
  resumed.release(); context.dispose();
});

test('an exhausted idle document is removed and its successor is lazy', async () => {
  const { context, frames, attached } = rig();
  for (const lease of await fill(context)) lease.release();
  assert.equal(attached.size, 0);
  assert.equal(frames.length, 1);
  await tick();
  assert.equal(frames.length, 1);
  const lease = await context.acquire();
  assert.equal(frames.length, 2);
  lease.release(); context.dispose();
});

test('queued cancellation removes that job without consuming a successor admission', async () => {
  const { context, frames } = rig();
  const leases = await fill(context);
  const controller = new AbortController();
  const cancelled = assert.rejects(context.acquire(controller.signal), /job cancelled/);
  const next = context.acquire();
  controller.abort(new Error('job cancelled'));
  await cancelled;
  for (const lease of leases) lease.release();
  const admitted = [await next, ...await fill(context, LIMIT - 1)];
  assert.equal(frames.length, 2, 'cancelled work did not consume an image job');
  for (const lease of admitted) lease.release();
  assert.equal(frames[1].removed, 1);
  context.dispose();
});

test('an already aborted request creates no frame and does not poison later work', async () => {
  const { context, frames } = rig();
  const controller = new AbortController();
  controller.abort(new Error('already cancelled'));
  await assert.rejects(context.acquire(controller.signal), /already cancelled/);
  assert.equal(frames.length, 0);
  const lease = await context.acquire();
  lease.release(); context.dispose();
});

test('request cancellation after admission leaves source ownership with the lease', async () => {
  const { context, frames } = rig();
  const controller = new AbortController();
  const lease = await context.acquire(controller.signal);
  controller.abort();
  assert.equal(lease.signal.aborted, false, 'only the resource can release its active source');
  assert.equal(frames[0].removed, 0);
  lease.release(); context.dispose();
});

test('context disposal rejects all waiters and aborts active leases before removing the frame', async () => {
  const { context, frames, attached } = rig();
  const leases = await fill(context);
  const signals = leases.map(lease => lease.signal);
  const pending = Array.from({ length: 5 }, () => assert.rejects(context.acquire(), /owner stopped/));
  let released = 0;
  for (const lease of leases) {
    lease.signal.addEventListener('abort', () => {
      assert.equal(frames[0].removed, 0, 'source cleanup precedes frame removal');
      lease.release(); released++;
    }, { once: true });
  }
  context.dispose(new Error('owner stopped')); context.dispose();
  await Promise.all(pending);
  assert.equal(released, LIMIT);
  assert.ok(signals.every(signal => signal.aborted));
  assert.equal(attached.size, 0);
  assert.equal(frames[0].removed, 1);
  await assert.rejects(context.acquire(), /owner stopped/);
  assert.equal(frames.length, 1);
});

test('disposal between promise resolution and its consumer invalidates the lease', async () => {
  const { context, frames, attached } = rig();
  const pending = context.acquire();
  context.dispose();
  const lease = await pending;
  assert.equal(lease.signal.aborted, true);
  lease.release(); lease.release();
  assert.equal(attached.size, 0);
  assert.equal(frames.length, 1);
});

for (const failure of ['createFailure', 'appendFailure', 'unavailableDocument']) {
  test(`${failure} rejects current and future work and removes any partial frame`, async () => {
    const { context, document, frames, attached } = rig();
    document[failure] = true;
    await assert.rejects(context.acquire(), /failed|unavailable/);
    await assert.rejects(context.acquire(), /failed|unavailable/);
    context.dispose();
    assert.equal(attached.size, 0);
    assert.ok(frames.every(frame => frame.removed === 1));
    assert.ok(frames.length <= 1);
  });
}

test('reentrant cancellation during frame attachment does not fail the context', async () => {
  const { context, document, frames } = rig();
  const controller = new AbortController();
  document.onAppend = () => controller.abort(new Error('cancelled while attaching'));
  await assert.rejects(context.acquire(controller.signal), /cancelled while attaching/);
  const lease = await context.acquire();
  assert.equal(frames.length, 1);
  lease.release(); context.dispose();
});

test('reentrant owner teardown during attachment cannot publish a surviving frame', async () => {
  const { context, document, frames, attached } = rig();
  document.onAppend = () => context.dispose(new Error('stopped while attaching'));
  await assert.rejects(context.acquire(), /stopped while attaching/);
  assert.equal(attached.size, 0);
  assert.equal(frames[0].removed, 1);
});

test('replacement-frame failure rejects the entire admission queue', async () => {
  const { context, document, frames, attached } = rig();
  const leases = await fill(context);
  const pending = Array.from({ length: 5 }, () => assert.rejects(context.acquire(), /creation failed/));
  document.createFailure = true;
  for (const lease of leases) lease.release();
  await Promise.all(pending);
  assert.equal(frames.length, 1);
  assert.equal(attached.size, 0);
  await assert.rejects(context.acquire(), /creation failed/);
});

test('forty thousand jobs keep one owner and at most one attached decoder document', async () => {
  const { context, frames, attached, maximumAttached } = rig();
  const uses = new Map();
  for (let job = 0; job < 40000; job++) {
    const lease = await context.acquire();
    uses.set(lease.document.id, (uses.get(lease.document.id) ?? 0) + 1);
    lease.release();
    assert.ok(attached.size <= 1);
  }
  assert.equal(maximumAttached(), 1);
  assert.equal(frames.length, Math.ceil(40000 / LIMIT));
  assert.ok([...uses.values()].every(count => count <= LIMIT));
  assert.equal(frames.filter(frame => frame.removed === 0).length, 1);
  context.dispose();
  assert.equal(attached.size, 0);
  assert.ok(frames.every(frame => frame.removed === 1));
});

// Compose the real resource and owner to cover the asynchronous admission,
// source cleanup, and late bitmap edges without a browser or phone.
function resourceRig(t) {
  const state = rig();
  const images = [], resources = [], bitmaps = [], errors = [];
  const urls = new Map(), timers = new Map();
  let nextURL = 0, nextTimer = 0;
  state.document.makeImage = ownerDocument => {
    const image = new EventTarget();
    Object.assign(image, {
      ownerDocument, src: '', naturalWidth: 4, naturalHeight: 4, listeners: new Set(),
      addEventListener(type, callback, options) {
        this.listeners.add(callback);
        EventTarget.prototype.addEventListener.call(this, type, callback, options);
      },
      removeEventListener(type, callback) {
        this.listeners.delete(callback);
        EventTarget.prototype.removeEventListener.call(this, type, callback);
      },
      removeAttribute(name) {
        assert.equal(name, 'src');
        assert.equal(this.listeners.size, 0);
        this.src = '';
      },
      load() { this.dispatchEvent(new Event('load')); },
    });
    images.push(image);
    return image;
  };
  globalThis.createImageBitmap ??= () => {};
  t.mock.method(globalThis, 'createImageBitmap', (...args) => {
    assert.equal(args.length, 1);
    const [image] = args;
    assert.ok(images.includes(image));
    assert.ok(urls.has(image.src));
    return new Promise((resolve, reject) => { image.conversion = { resolve, reject }; });
  });
  t.mock.method(URL, 'createObjectURL', blob => {
    const url = `blob:context-test-${++nextURL}`;
    urls.set(url, blob); return url;
  });
  t.mock.method(URL, 'revokeObjectURL', url => assert.equal(urls.delete(url), true));
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    assert.equal(delay, 10000);
    timers.set(++nextTimer, callback); return nextTimer;
  });
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id));
  const resource = (bytes = new Uint8Array([1, 2, 3])) => {
    const result = new ImageResource(bytes, 'image/jpeg', 4, 4, () => {}, error => errors.push(error), state.context);
    resources.push(result); return result;
  };
  const convert = image => {
    const bitmap = { width: 4, height: 4, closed: 0, close() { this.closed++; } };
    bitmaps.push(bitmap);
    image.conversion.resolve(bitmap); image.conversion = null;
    return bitmap;
  };
  t.after(() => {
    for (const value of resources) value.dispose();
    state.context.dispose();
    assert.equal(urls.size, 0);
    assert.equal(timers.size, 0);
    assert.equal(state.attached.size, 0);
    assert.ok(images.every(image => image.src === '' && image.listeners.size === 0));
    assert.ok(bitmaps.every(bitmap => bitmap.closed === 1));
  });
  return { ...state, images, resources, errors, urls, timers, resource, convert };
}

test('an image rectangle cannot silently create a global decoder when its owner is missing', () => {
  assert.throws(() => new ImageResource(new Uint8Array([1]), 'image/jpeg', 1, 1, () => {}, () => {}), /decode context/);
});

for (const cancellation of ['dispose', 'timeout']) {
  test(`a resource waiting at the context boundary is cancellable by ${cancellation}`, async t => {
    const state = resourceRig(t);
    const leases = await fill(state.context);
    const resource = state.resource();
    resource.start();
    await tick();
    assert.equal(state.images.length, 0, 'waiting work does not create a source or URL');
    assert.equal(state.urls.size, 0);
    if (cancellation === 'dispose') resource.dispose();
    else state.timers.values().next().value();
    for (const lease of leases) lease.release();
    await tick();
    assert.equal(state.images.length, 0);
    assert.equal(state.frames.length, 1, 'cancelled work cannot create a successor document');
    assert.equal(state.timers.size, 0);
    assert.equal(state.errors.length, cancellation === 'timeout' ? 1 : 0);
    if (cancellation === 'timeout') assert.match(state.errors[0].message, /timed out/);
  });
}

test('waiting for a decoder document still snapshots bytes before the socket reuses them', async t => {
  const state = resourceRig(t);
  const leases = await fill(state.context);
  const bytes = new Uint8Array([9, 1, 2, 3, 8]);
  const resource = state.resource(bytes.subarray(1, 4));
  resource.start(); bytes.fill(0);
  for (const lease of leases.slice(1)) lease.release();
  await tick();
  assert.equal(state.images.length, 0);
  leases[0].release();
  await tick();
  const [image] = state.images;
  assert.deepEqual(new Uint8Array(await state.urls.get(image.src).arrayBuffer()), new Uint8Array([1, 2, 3]));
  assert.equal(state.frames[0].removed, 1);
  assert.equal(image.ownerDocument, state.frames[1].contentDocument);
  image.load(); state.convert(image);
  await tick();
  assert.equal(resource.ready, true);
  resource.dispose();
  assert.equal(state.errors.length, 0);
});

test('the last source lease waits for bitmap completion while the independent bitmap survives retirement', async t => {
  const state = resourceRig(t);
  for (const lease of await fill(state.context, LIMIT - 1)) lease.release();
  const first = state.resource(), second = state.resource();
  first.start(); second.start();
  await tick();
  assert.equal(state.images.length, 1);
  const source = state.images[0];
  source.load();
  assert.equal(state.frames[0].removed, 0, 'a pending conversion keeps its owner document');
  const bitmap = state.convert(source);
  await tick();
  assert.equal(source.src, '', 'source cleanup finishes before document retirement');
  assert.equal(state.frames[0].removed, 1);
  assert.equal(state.images.length, 2);
  assert.equal(first.image, bitmap);
  assert.equal(bitmap.closed, 0, 'a queued ready bitmap is independent of the retired document');
  state.images[1].load(); state.convert(state.images[1]);
  await tick();
  first.dispose(); second.dispose();
  assert.equal(state.maximumAttached(), 1);
  assert.equal(state.errors.length, 0);
});

test('owner teardown during conversion clears the source, fails once, and closes a late bitmap', async t => {
  const state = resourceRig(t);
  const resource = state.resource();
  resource.start(); await tick();
  const [image] = state.images;
  image.load();
  state.context.dispose(new Error('decoder owner stopped'));
  assert.equal(image.src, '');
  assert.equal(state.attached.size, 0);
  assert.equal(state.urls.size, 0);
  assert.equal(state.errors.length, 1);
  assert.match(state.errors[0].message, /owner stopped/);
  const late = state.convert(image);
  await tick();
  assert.equal(late.closed, 1);
  assert.equal(resource.ready, false);
  assert.equal(state.errors.length, 1);
  await assert.rejects(state.context.acquire(), /owner stopped/);
});

test('owner teardown before an admission callback prevents creating an image in the retired frame', async t => {
  const state = resourceRig(t);
  const resource = state.resource();
  resource.start();
  state.context.dispose(new Error('owner stopped before decode'));
  await tick();
  assert.equal(state.images.length, 0);
  assert.equal(state.urls.size, 0);
  assert.equal(state.errors.length, 1);
  assert.match(state.errors[0].message, /before decode/);
});

test('a resource reports context-creation failure through its normal decode error path', async t => {
  const state = resourceRig(t);
  state.document.unavailableDocument = true;
  const resource = state.resource();
  resource.start();
  await tick();
  assert.equal(state.images.length, 0);
  assert.equal(state.urls.size, 0);
  assert.equal(state.errors.length, 1);
  assert.match(state.errors[0].message, /document is unavailable/);
  assert.equal(state.attached.size, 0);
});
