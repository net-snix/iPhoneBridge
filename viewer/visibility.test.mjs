import test from 'node:test';
import assert from 'node:assert/strict';
import { observeVisibility } from './visibility.mjs';

test('captures a hidden interval even when both endpoint samples are visible', () => {
  let now = 0, listener;
  const doc = { hidden: false, hasFocus: () => false,
    addEventListener: (_type, callback) => { listener = callback; },
    removeEventListener: (_type, callback) => { assert.equal(callback, listener); listener = null; } };
  const observer = observeVisibility(doc, { now: () => now });
  now = 50; doc.hidden = true; listener();
  now = 150; doc.hidden = false; listener();
  now = 300;
  assert.deepEqual(observer.stop(), { available: true, hidden: false, focused: false,
    hadHidden: true, hiddenMs: 100, changes: 2 });
  assert.equal(listener, null);
});

test('counts initially hidden time and does not confuse missing visibility with visible', () => {
  let now = 10;
  const doc = { hidden: true, addEventListener() {}, removeEventListener() {} };
  const observer = observeVisibility(doc, { now: () => now });
  now = 40;
  assert.equal(observer.stop().hiddenMs, 30);
  const missing = observeVisibility(undefined, { now: () => now }).stop();
  assert.equal(missing.available, false);
  assert.equal(missing.hidden, null);
});
