import assert from 'node:assert/strict';
import test from 'node:test';
import { mountMirror } from './mirror.mjs';

function harness(native = true) {
  const messages = [];
  const clients = [];
  const timers = new Map();
  let observer;
  let timerId = 0;
  const canvas = { width: 0, height: 0 };
  const elements = Object.fromEntries(
    ['screen', 'connection', 'message', 'reconnect'].map(name => [name, new EventTarget()]),
  );
  elements.screen.querySelector = () => canvas;
  const win = new EventTarget();
  win.location = { hostname: '127.0.0.1', host: '127.0.0.1:15801', protocol: 'http:' };
  if (native) win.webkit = { messageHandlers: { mirror: { postMessage: value => messages.push(value) } } };
  class FakeRFB extends EventTarget {
    constructor(screen, url, options) {
      super();
      this.url = url;
      this.options = options;
      clients.push(this);
    }
    disconnect() { this.dispatchEvent(new Event('disconnect')); }
    focus() { this.focused = true; }
    sendKey(...args) { (this.keys ??= []).push(args); }
  }
  class FakeObserver {
    constructor(callback) { this.callback = callback; observer = this; }
    observe(target, options) { this.options = options; }
    disconnect() { this.stopped = true; }
  }
  const api = mountMirror(FakeRFB, {
    window: win,
    document: { getElementById: id => elements[id], documentElement: { dataset: {} } },
    MutationObserver: FakeObserver,
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
  });
  return { api, messages, clients, timers, canvas, observer, elements, win };
}

test('native mirror uses interactive scaled shared session and reports framebuffer orientation', () => {
  const h = harness();
  const c = h.clients[0];
  assert.equal(c.url, 'ws://127.0.0.1:15801/websockify');
  assert.deepEqual(c.options, { shared: true });
  assert.equal(c.viewOnly, false);
  assert.equal(c.scaleViewport, true);
  assert.equal(c.resizeSession, false);
  assert.equal(c.clipViewport, false);
  Object.assign(h.canvas, { width: 1170, height: 2532 });
  c.dispatchEvent(new Event('connect'));
  assert.equal(h.elements.connection.hidden, true);
  assert.equal(c.focused, true);
  assert.deepEqual(h.messages.at(-1), { type: 'resize', width: 1170, height: 2532 });
  const count = h.messages.length;
  h.observer.callback();
  assert.equal(h.messages.length, count, 'CSS resizing must not emit duplicate native sizing');
  Object.assign(h.canvas, { width: 2532, height: 1170 });
  h.observer.callback();
  assert.deepEqual(h.messages.at(-1), { type: 'resize', width: 2532, height: 1170 });
  assert.deepEqual(h.observer.options.attributeFilter, ['width', 'height']);
  h.api.stop();
});

test('reconnection retires stale sessions and page close cancels timers', () => {
  const h = harness(false);
  const first = h.clients[0];
  first.dispatchEvent(new Event('connect'));
  first.disconnect();
  assert.equal(h.elements.reconnect.hidden, false);
  assert.equal(h.timers.size, 1);
  const pending = [...h.timers.entries()][0];
  assert.equal(pending[1].delay, 1000);
  h.timers.delete(pending[0]);
  pending[1].callback();
  assert.equal(h.clients.length, 2);
  first.dispatchEvent(new Event('connect'));
  assert.equal(h.elements.connection.hidden, false, 'stale connect event ignored');
  h.clients[1].dispatchEvent(new Event('connect'));
  assert.equal(h.timers.size, 0);
  h.win.dispatchEvent(new Event('pagehide'));
  assert.equal(h.observer.stopped, true);
  assert.equal(h.timers.size, 0);
});

test('authentication errors wait for explicit retry without collecting credentials', () => {
  const h = harness(false);
  h.clients[0].dispatchEvent(new Event('credentialsrequired'));
  assert.equal(h.elements.message.textContent, 'Bridge authentication failed.');
  assert.equal(h.elements.reconnect.hidden, false);
  assert.equal(h.timers.size, 0);
  h.elements.reconnect.dispatchEvent(new Event('click'));
  assert.equal(h.clients.length, 2);
  h.api.stop();
});

test('navigation sends one gesture over the existing live session and refuses stale sessions', () => {
  const h = harness();
  const c = h.clients[0];
  assert.equal(h.api.navigate('home'), false, 'no input before connection');
  c.dispatchEvent(new Event('connect'));
  assert.equal(h.api.navigate('home'), true);
  assert.equal(h.api.navigate('app-switcher'), true);
  assert.equal(h.api.navigate('power'), false);
  assert.deepEqual(c.keys, [[0x1008FF18, null], [0x1008FF7F, null]]);
  assert.equal(h.clients.length, 1, 'navigation reuses the video connection');
  c.disconnect();
  assert.equal(h.api.navigate('home'), false);
  h.api.stop();
  assert.equal(h.api.navigate('app-switcher'), false);
  assert.equal(c.keys.length, 2, 'disconnect/stop never queue input for a later connection');
});
