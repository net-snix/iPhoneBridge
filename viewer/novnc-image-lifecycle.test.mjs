import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mountMirror } from './mirror.mjs';

// Exercise the prepared dependency itself. CI prepares only the pinned viewer
// sources; these tests need neither a phone nor a browser/package installation.
const source = resolve(process.env.IPHONEBRIDGE_NOVNC_SOURCE ?? 'work/vendor/noVNC');
const moduleURL = name => pathToFileURL(resolve(source, 'core', name)).href;

class Element extends EventTarget {
  constructor() {
    super();
    this.ownerDocument = globalThis.document;
    this.style = {};
    this.children = [];
    this.width = this.height = 0;
    this.offsetWidth = this.clientWidth = 100;
    this.offsetHeight = this.clientHeight = 100;
    this.context = {
      drawImage() {}, fillRect() {}, clearRect() {}, putImageData() {},
      getImageData: () => ({ data: new Uint8ClampedArray(0) }),
    };
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; }
  remove() { this.parentNode?.removeChild(this); }
  setAttribute(name, value) { this[name] = value; }
  getContext() { return this.context; }
  focus() {}
  querySelector() { return this.children.flatMap(child => child.children)[0] ?? null; }
}
class Observer { observe() {} disconnect() {} }
class Socket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor() {
    this.readyState = Socket.CONNECTING;
    this.protocol = '';
    this.binaryType = '';
    this.onopen = this.onclose = this.onmessage = this.onerror = null;
  }
  send() {}
  close() { this.readyState = Socket.CLOSED; queueMicrotask(() => this.onclose?.({})); }
}

globalThis.window = Object.assign(new EventTarget(), {
  isSecureContext: true, devicePixelRatio: 1, console,
  location: { hostname: '127.0.0.1', host: '127.0.0.1:15801', protocol: 'http:' },
});
globalThis.document = {
  documentElement: new Element(), body: new Element(),
  createElement(tag) {
    const element = new Element();
    if (tag === 'iframe') {
      decoderFrames.push(element);
      element.contentDocument = {
        frame: element,
        createElement(tag) {
          assert.equal(tag, 'img');
          const image = new Image();
          image.ownerDocument = this;
          return image;
        },
      };
    } else {
      assert.notEqual(tag, 'img', 'image decoding belongs to the explicitly owned document');
    }
    return element;
  },
  addEventListener() {}, removeEventListener() {},
};
document.documentElement.dataset = {};
globalThis.MutationObserver = globalThis.ResizeObserver = Observer;
globalThis.WebSocket = Socket;
if (!globalThis.navigator) globalThis.navigator = { userAgent: 'test', platform: 'MacIntel' };

const { default: Display } = await import(moduleURL('display.js'));
const { default: ImageResource } = await import(moduleURL('image-resource.js'));
const { default: ImageDecodeContext } = await import(moduleURL('image-context.js'));
const { default: RFB } = await import(moduleURL('rfb.js'));
const { initLogging } = await import(moduleURL('util/logging.js'));
initLogging('none');

let images, objectURLs, revokedURLs, timers, nextTimer, nextURL, decoderFrames, owners;
globalThis.createImageBitmap ??= () => {};
beforeEach(t => {
  images = []; objectURLs = new Map(); revokedURLs = [];
  timers = new Map(); nextTimer = 0; nextURL = 0;
  decoderFrames = []; owners = [];
  t.after(() => {
    for (const owner of owners) owner.dispose();
    assert.ok(decoderFrames.every(frame => !frame.parentNode), 'all display/context owners remove their frame');
  });
  class Image extends EventTarget {
    constructor() {
      super(); this._src = ''; this.naturalWidth = this.naturalHeight = 0;
      this.listeners = new Set(); this.cleared = 0; images.push(this);
    }
    get src() { return this._src; }
    set src(value) {
      assert.notEqual(value, '', 'remove the source attribute without scheduling an empty-URL error');
      if (value && Image.failAssignment) throw new Error('image source unavailable');
      this._src = value;
      if (value && Image.immediateEvent) this[Image.immediateEvent]();
    }
    addEventListener(type, callback) {
      this.listeners.add(callback); super.addEventListener(type, callback);
    }
    removeEventListener(type, callback) {
      this.listeners.delete(callback); super.removeEventListener(type, callback);
    }
    removeAttribute(name) {
      assert.equal(name, 'src');
      assert.equal(this.listeners.size, 0, 'detach callbacks before clearing the source');
      assert.ok(this.ownerDocument.frame.parentNode, 'clear the source before its document is removed');
      assert.equal(++this.cleared, 1, 'each source is cleared exactly once');
      this._src = '';
    }
    load(width = 4, height = 4) {
      this.naturalWidth = width; this.naturalHeight = height;
      this.dispatchEvent(new Event('load'));
    }
    fail() { this.dispatchEvent(new Event('error')); }
  }
  globalThis.Image = Image;
  t.mock.method(globalThis, 'createImageBitmap', () => assert.fail('draw native HTML images without an intermediate bitmap'));
  t.mock.method(URL, 'createObjectURL', blob => {
    const url = `blob:mock-${++nextURL}`;
    objectURLs.set(url, blob); return url;
  });
  t.mock.method(URL, 'revokeObjectURL', url => {
    assert.ok(objectURLs.delete(url), 'each explicit URL is revoked exactly once');
    revokedURLs.push(url);
  });
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    timers.set(++nextTimer, { callback, delay }); return nextTimer;
  });
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id));
});

function displayRig() {
  const display = new Display(new Element());
  owners.push(display);
  display.resize(4, 4);
  const drawn = [], errors = [];
  display.drawImage = image => {
    assert.ok(image instanceof Image, 'draw the original native HTML image');
    assert.ok(objectURLs.has(image.src), 'the encoded source stays valid until drawing');
    assert.ok(image.ownerDocument.frame.parentNode, 'the source document stays attached through drawing');
    drawn.push(images.indexOf(image));
  };
  display.onerror = error => errors.push(error);
  return { display, drawn, errors };
}
async function enqueue(display, data = new Uint8Array([1, 2, 3, 4])) {
  display.imageRect(0, 0, 4, 4, 'image/jpeg', data);
  await tick();
  return images.at(-1);
}
function fireTimer(delay) {
  const entry = [...timers].find(([, task]) => task.delay === delay);
  assert.ok(entry, `expected a ${delay} ms deadline`);
  timers.delete(entry[0]); entry[1].callback();
}
function assertReleased() {
  assert.equal(timers.size, 0, 'no decode deadlines remain');
  assert.ok(images.every(image => image.src === ''), 'decoded image sources are cleared');
  assert.ok(images.every(image => image.listeners.size === 0), 'image listeners are removed');
  assert.equal(objectURLs.size, 0, 'no encoded object URLs remain');
  assert.ok(images.every(image => image.cleared === 1), 'every source is cleared exactly once');
}
async function tick() { for (let i = 0; i < 5; i++) await Promise.resolve(); }
function complete(image) { image.load(); }
async function settleRemaining() {
  for (const image of images) image.load();
  await tick();
}

test('object URLs snapshot exact bytes and ordered drawing retains ready image sources and leases', async () => {
  const { display, drawn } = displayRig();
  const bytes = new Uint8Array([9, 1, 2, 3, 8]);
  const first = await enqueue(display, bytes.subarray(1, 4));
  bytes.fill(0); // The real RFB socket reuses its receive storage.
  const blob = objectURLs.get(first.src);
  assert.equal(blob.type, 'image/jpeg');
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), new Uint8Array([1, 2, 3]));
  const second = await enqueue(display);
  const flushed = display.flush();
  second.load();
  await tick();
  assert.deepEqual(drawn, [], 'a later rectangle cannot overtake an earlier one');
  assert.notEqual(second.src, '', 'ready but queued native images keep their source');
  assert.equal(objectURLs.size, 2, 'both encoded URLs remain owned until drawing');
  assert.equal(display._imageContext._current.active, 2, 'ready images retain their document lease');
  assert.equal(second.listeners.size, 0, 'completed loading retains no event listeners');
  complete(first);
  await flushed;
  assert.deepEqual(drawn, [0, 1]);
  assertReleased();
});

test('legacy byte arrays retain the same encoded bytes as typed-array rectangles', async () => {
  const { display } = displayRig();
  const image = await enqueue(display, [0, 127, 255]);
  assert.deepEqual(new Uint8Array(await objectURLs.get(image.src).arrayBuffer()), new Uint8Array([0, 127, 255]));
  complete(image);
  await display.flush();
  assertReleased();
});

test('disposal releases unstarted, loading and ready image ownership despite late callbacks', async () => {
  for (const stage of ['unstarted', 'loading', 'ready']) {
    let ready = 0, failed = 0;
    const onReady = () => ready++;
    const onError = () => failed++;
    const bytes = new Uint8Array([1, 2, 3]);
    const context = new ImageDecodeContext(document);
    owners.push(context);
    const resource = new ImageResource(bytes, 'image/jpeg', 4, 4, onReady, onError, context);
    const lateLoad = resource._loaded, lateError = resource._failed;
    if (stage !== 'unstarted') {
      resource.start(); resource.start();
      await tick();
      assert.equal(timers.size, 1);
    }
    const image = resource.image;
    if (stage === 'ready') image.load();
    resource.dispose(); resource.dispose();
    lateLoad(); lateError();
    await tick();
    resource.start();
    assert.equal(ready, stage === 'ready' ? 1 : 0);
    assert.equal(failed, 0);
    assert.equal(resource.image, null);
    assert.ok(Object.values(resource).every(value =>
      (image === null || value !== image) && value !== bytes && value !== onReady && value !== onError &&
      typeof value !== 'string' && typeof value !== 'function'),
    'a retired resource retains no image, bytes, URL or callback');
    assertReleased();
  }
});

test('synchronous load during source assignment can draw and dispose safely', async () => {
  const { display, drawn, errors } = displayRig();
  Image.immediateEvent = 'load';
  await enqueue(display);
  await display.flush();
  assert.deepEqual(drawn, [0]);
  assert.deepEqual(errors, []);
  assertReleased();
});

test('synchronous error during source assignment fails and disposes safely', async () => {
  const { display, drawn, errors } = displayRig();
  Image.immediateEvent = 'fail';
  await enqueue(display);
  await assert.rejects(display.flush(), /decode/);
  assert.deepEqual(drawn, []);
  assert.equal(errors.length, 1);
  assertReleased();
});

for (const failure of ['decode', 'dimensions', 'timeout']) {
  test(`${failure} failure releases the whole queue and rejects pending and future flushes`, async () => {
    const { display, drawn, errors } = displayRig();
    const first = await enqueue(display);
    const second = await enqueue(display);
    const rejected = assert.rejects(display.flush(), failure.includes('dimensions') ? /dimensions/ : failure.includes('timeout') ? /timed out/ : /decode/);
    complete(second);
    await tick();
    if (failure === 'decode') first.fail();
    else if (failure === 'dimensions') first.load(3, 4);
    else fireTimer(10000);
    await rejected;
    await assert.rejects(display.flush());
    assert.deepEqual(drawn, []);
    assert.equal(errors.length, 1, 'one session failure, without silently dropping a rectangle');
    first.load(); first.fail(); second.load();
    await settleRemaining();
    assert.deepEqual(drawn, [], 'late events cannot resume a failed display');
    display.dispose();
    assertReleased();
  });
}

test('a later queued decode error fails immediately without waiting for the head', async () => {
  const { display, errors } = displayRig();
  await enqueue(display);
  const second = await enqueue(display);
  const rejected = assert.rejects(display.flush(), /decode/);
  second.fail();
  await rejected;
  assert.equal(errors.length, 1);
  await settleRemaining();
  assertReleased();
});

test('a synchronous image source error terminates rendering without a fallback', async () => {
  const { display, errors } = displayRig();
  Image.failAssignment = true;
  await enqueue(display);
  await assert.rejects(display.flush(), /image source unavailable/);
  assert.equal(errors.length, 1);
  assertReleased();
});

test('late image error after timeout reports only the original failure', async () => {
  const { display, errors } = displayRig();
  const image = await enqueue(display);
  const rejected = assert.rejects(display.flush(), /timed out/);
  fireTimer(10000);
  image.fail();
  await rejected;
  await tick();
  assert.equal(errors.length, 1);
  assertReleased();
});

test('canvas draw failures reject flush and free the current and following resources', async () => {
  const { display, errors } = displayRig();
  const first = await enqueue(display);
  await enqueue(display);
  display.drawImage = () => { throw new Error('canvas unavailable'); };
  const rejected = assert.rejects(display.flush(), /canvas unavailable/);
  complete(first);
  await rejected;
  assert.equal(errors.length, 1);
  await settleRemaining();
  assertReleased();
});

test('a queued canvas flip failure also terminates the flush instead of hanging it', async () => {
  const { display, errors } = displayRig();
  const first = await enqueue(display);
  display.flip();
  display._targetCtx.drawImage = () => { throw new Error('canvas flip failed'); };
  display._damage(0, 0, 4, 4);
  const rejected = assert.rejects(display.flush(), /canvas flip failed/);
  complete(first);
  await rejected;
  assert.equal(errors.length, 1);
  assertReleased();
});

test('disconnect releases loading and ready image sources before removing their document', async () => {
  const { display, drawn, errors } = displayRig();
  const first = await enqueue(display);
  const second = await enqueue(display);
  complete(second);
  await tick();
  const rejected = assert.rejects(display.flush(), /disconnected/);
  display.dispose(); display.dispose();
  first.load(); first.fail(); second.load();
  await rejected;
  await tick();
  assert.deepEqual(drawn, []);
  assert.deepEqual(errors, [], 'intentional disconnect is not a new rendering error');
  assert.equal(display._target.width + display._backbuffer.width, 0);
  assertReleased();
});

test('disconnect cancels pending admission before releasing an exhausted decoder document', async () => {
  const { display, drawn, errors } = displayRig();
  // A loading head, ready successor and admission waiting across the boundary.
  for (let i = 0; i < 4094; i++) {
    (await display._imageContext.acquire()).release();
  }
  const head = await enqueue(display);
  const ready = await enqueue(display);
  ready.load();
  await enqueue(display);
  assert.equal(images.length, 2, 'the next rectangle is waiting for drawing');
  assert.equal(display._imageContext._current.active, 2);
  assert.equal(display._imageContext._waiters.size, 1);
  assert.equal(decoderFrames.length, 1);
  const rejected = assert.rejects(display.flush(), /disconnected/);
  display.dispose();
  head.load(); ready.load();
  await rejected;
  await tick();
  assert.equal(decoderFrames.length, 1, 'teardown must not create a successor decoder document');
  assert.ok(decoderFrames.every(frame => !frame.parentNode));
  assert.deepEqual(drawn, []);
  assert.deepEqual(errors, [], 'intentional disconnect is not a new rendering error');
  assertReleased();
});

test('context failure releases ready queue entries and rejects the display flush once', async () => {
  const { display, drawn, errors } = displayRig();
  const head = await enqueue(display);
  const ready = await enqueue(display);
  ready.load();
  const rejected = assert.rejects(display.flush(), /decoder owner failed/);
  display._imageContext.dispose(new Error('decoder owner failed'));
  await rejected;
  await assert.rejects(display.flush(), /decoder owner failed/);
  head.load(); ready.load();
  display.dispose();
  assert.equal(display._renderQ.length, 0);
  assert.deepEqual(drawn, []);
  assert.equal(errors.length, 1);
  assertReleased();
});

test('late decode callbacks cannot fail a ready image while it waits to be drawn', async () => {
  const { display, drawn, errors } = displayRig();
  const head = await enqueue(display);
  const image = await enqueue(display);
  const resource = display._renderQ[1].img;
  const lateLoad = resource._loaded, lateError = resource._failed;
  const lateTimeout = timers.get(resource._timer).callback;
  image.load();
  lateLoad(); lateError(); lateTimeout();
  assert.equal(resource.ready, true);
  assert.equal(display._imageContext._current.active, 2);
  assert.deepEqual(errors, []);
  head.load();
  await display.flush();
  assert.deepEqual(drawn, [0, 1]);
  assertReleased();
});

test('ordered drawing drains an exhausted document before admitting the next rectangle', async () => {
  const { display, drawn, errors } = displayRig();
  for (let i = 0; i < 4094; i++) (await display._imageContext.acquire()).release();
  const head = await enqueue(display);
  const ready = await enqueue(display);
  ready.load();
  await enqueue(display);
  const flushed = display.flush();
  assert.equal(images.length, 2);
  head.load();
  await tick();
  assert.deepEqual(drawn, [0, 1]);
  assert.equal(decoderFrames[0].parentNode, null);
  assert.equal(decoderFrames.length, 2);
  assert.equal(images.length, 3);
  images[2].load();
  await flushed;
  assert.deepEqual(drawn, [0, 1, 2]);
  assert.deepEqual(errors, []);
  assertReleased();
});

test('repeated image rectangles release URLs, sources, listeners and decode timers', async () => {
  const { display, drawn } = displayRig();
  for (let i = 0; i < 1000; i++) {
    complete(await enqueue(display));
    await display.flush();
    assertReleased();
  }
  assert.equal(drawn.length, 1000);
  display.dispose();
});

function connectedRFB(target = new Element()) {
  const client = new RFB(target, 'ws://127.0.0.1/websockify', { shared: true });
  owners.push(client._display);
  client._sock._websocket.readyState = Socket.OPEN;
  client._updateConnectionState('connected');
  client._display.resize(4, 4);
  return client;
}
function waitForPreviousFrame(client) {
  // A complete FramebufferUpdate header arriving while an image is pending.
  client._sock._recvMessage({ data: new Uint8Array([0, 0, 0, 1]).buffer });
  assert.equal(client._flushing, true);
}

test('real RFB rendering failure rejects its flush and emits one unclean disconnect', async () => {
  const client = connectedRFB();
  const disconnects = [];
  client.addEventListener('disconnect', event => disconnects.push(event.detail));
  const pending = await enqueue(client._display);
  waitForPreviousFrame(client);
  pending.fail();
  await tick();
  assert.deepEqual(disconnects, [{ clean: false }]);
  assert.equal(client._flushing, false);
  assert.equal(client._sock.readyState, 'closed');
  assertReleased();
});

test('real RFB normal disconnect while flushing consumes cancellation without retrying failure', async () => {
  const client = connectedRFB();
  const disconnects = [];
  client.addEventListener('disconnect', event => disconnects.push(event.detail));
  const pending = await enqueue(client._display);
  waitForPreviousFrame(client);
  client.disconnect();
  pending.load();
  await tick();
  assert.deepEqual(disconnects, [{ clean: true }]);
  assert.equal(client._flushing, false);
  assertReleased();
});

test('the viewer reconnects automatically after an actual RFB image-decode failure', async () => {
  const nodes = Object.fromEntries(['screen', 'connection', 'message', 'reconnect'].map(name => [name, new Element()]));
  const clients = [];
  class ObservedRFB extends RFB {
    constructor(...args) { super(...args); clients.push(this); }
  }
  const mirror = mountMirror(ObservedRFB, {
    window, document: { documentElement: document.documentElement, getElementById: id => nodes[id] },
    MutationObserver: Observer,
  });
  const first = clients[0];
  first._sock._websocket.readyState = Socket.OPEN;
  first._updateConnectionState('connected');
  first._display.resize(4, 4);
  (await enqueue(first._display)).fail();
  await tick();
  assert.equal(mirror.currentClient(), null);
  fireTimer(1000);
  assert.equal(clients.length, 2);
  assert.equal(nodes.screen.children.length, 1, 'the retired canvas is removed');
  mirror.stop();
  await tick();
  assertReleased();
});
