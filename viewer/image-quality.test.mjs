import assert from 'node:assert/strict';
import test from 'node:test';
import { comparePixels, readImageDecoderCapability } from './image-quality.mjs';

test('pixel comparison includes RGB and alpha with exact coordinates and channel differences', () => {
  const before = new Uint8ClampedArray([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
  assert.deepEqual(comparePixels(before, before.slice(), 2, 2), {
    exactMatch: true, differentPixels: 0, differentChannels: 0,
    maxChannelDifference: 0, firstDifference: null,
  });
  const after = before.slice();
  after[7] = 255; // Alpha of x=1, y=0.
  after[8] = 12; after[9] = 13;
  assert.deepEqual(comparePixels(before, after, 2, 2), {
    exactMatch: false, differentPixels: 2, differentChannels: 3,
    maxChannelDifference: 248, firstDifference: { x: 1, y: 0, channel: 3, before: 7, after: 255 },
  });
});

test('pixel comparison rejects wrong dimensions rather than reporting a false match', () => {
  assert.throws(() => comparePixels(new Uint8Array(4), new Uint8Array(8), 1, 1), /dimensions/);
  assert.throws(() => comparePixels(new Uint8Array(4), new Uint8Array(4), 2, 1), /dimensions/);
});

test('missing ImageDecoder is reported without attempting a decode or failing the diagnostic', async () => {
  assert.deepEqual(await readImageDecoderCapability({}), {
    imageDecoderType: 'undefined', jpeg: null, png: null, error: null,
  });
});

test('ImageDecoder capability queries both MIME types without constructing a decoder', async () => {
  const queried = [];
  class ImageDecoder {
    constructor() { assert.fail('capability probing must never decode an image'); }
    static async isTypeSupported(type) {
      assert.equal(this, ImageDecoder);
      queried.push(type);
      return type === 'image/jpeg';
    }
  }
  assert.deepEqual(await readImageDecoderCapability({ ImageDecoder }), {
    imageDecoderType: 'function', jpeg: true, png: false, error: null,
  });
  assert.deepEqual(queried, ['image/jpeg', 'image/png']);
});

test('an incomplete ImageDecoder API reports a capability error without rejecting', async () => {
  assert.deepEqual(await readImageDecoderCapability({ ImageDecoder: class {} }), {
    imageDecoderType: 'function', jpeg: null, png: null,
    error: 'ImageDecoder.isTypeSupported is unavailable',
  });
});

for (const failure of ['throw', 'reject']) {
  test(`ImageDecoder ${failure} is isolated while preserving the other MIME type result`, async () => {
    class ImageDecoder {
      static isTypeSupported(type) {
        if (type === 'image/png') return Promise.resolve(true);
        if (failure === 'throw') throw new Error('capability unavailable');
        return Promise.reject(new Error('capability unavailable'));
      }
    }
    assert.deepEqual(await readImageDecoderCapability({ ImageDecoder }), {
      imageDecoderType: 'function', jpeg: null, png: true,
      error: 'image/jpeg: capability unavailable',
    });
  });
}

test('ImageDecoder capability timeout is bounded and ignores late completion', async t => {
  let deadline, cleared = false;
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    assert.equal(delay, 2000); deadline = callback; return 42;
  });
  t.mock.method(globalThis, 'clearTimeout', timer => { assert.equal(timer, 42); cleared = true; });
  const completions = [];
  class ImageDecoder {
    static isTypeSupported() { return new Promise(resolve => completions.push(resolve)); }
  }
  const pending = readImageDecoderCapability({ ImageDecoder });
  await Promise.resolve();
  assert.equal(completions.length, 2);
  deadline();
  const capability = await pending;
  assert.deepEqual(capability, {
    imageDecoderType: 'function', jpeg: null, png: null,
    error: 'ImageDecoder capability check timed out',
  });
  assert.equal(cleared, true);
  for (const complete of completions) complete(true);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(capability.jpeg, null);
  assert.equal(capability.png, null);
});
