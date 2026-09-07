// Original iPhoneBridge code, MIT. noVNC remains unmodified in /novnc/.
export function mountMirror(RFB, environment = {}) {
  const win = environment.window ?? window;
  const doc = environment.document ?? document;
  const Observer = environment.MutationObserver ?? MutationObserver;
  const schedule = environment.setTimeout ?? setTimeout;
  const cancel = environment.clearTimeout ?? clearTimeout;
  const screen = doc.getElementById('screen');
  const connection = doc.getElementById('connection');
  const message = doc.getElementById('message');
  const button = doc.getElementById('reconnect');
  const native = win.webkit?.messageHandlers?.mirror;
  doc.documentElement.dataset.native = String(Boolean(native));

  let client = null;
  let connected = false;
  let stopped = false;
  let retries = 0;
  let retryTimer = null;
  let connectTimer = null;
  let lastSize = '';
  let failure = '';

  function post(payload) {
    native?.postMessage(payload);
  }

  function status(text, isConnected, canRetry = false) {
    connected = isConnected;
    message.textContent = text;
    connection.hidden = isConnected;
    button.hidden = !canRetry || Boolean(native);
    post({ type: 'status', connected: isConnected, message: text });
  }

  function reportSize() {
    const canvas = screen.querySelector('canvas');
    if (!connected || !canvas || canvas.width <= 0 || canvas.height <= 0) return;
    const key = `${canvas.width}x${canvas.height}`;
    if (key === lastSize) return;
    lastSize = key;
    post({ type: 'resize', width: canvas.width, height: canvas.height });
  }

  // noVNC v1.7.0 exposes no desktopresize event. With clipping disabled,
  // core/display.js viewportChangeSize keeps canvas attributes equal to the
  // framebuffer size. Local scaling changes CSS dimensions only. Observe
  // attributes, not element layout, so resizing the Mac window cannot loop.
  const observer = new Observer(reportSize);
  observer.observe(screen, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['width', 'height'],
  });

  function clearTimers() {
    cancel(retryTimer);
    cancel(connectTimer);
    retryTimer = null;
    connectTimer = null;
  }

  function retry() {
    if (stopped || retryTimer !== null) return;
    const delay = Math.min(1000 * 2 ** Math.min(retries++, 4), 10000);
    status('Reconnecting…', false, true);
    retryTimer = schedule(() => {
      retryTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (stopped) return;
    clearTimers();
    // A retired connection cannot schedule retries or update this session.
    const previous = client;
    client = null;
    previous?.disconnect();
    lastSize = '';
    failure = '';
    status(retries ? 'Reconnecting…' : 'Connecting…', false);
    try {
      if (!['127.0.0.1', 'localhost', '[::1]'].includes(win.location.hostname)) {
        throw new Error('Open the local iPhoneBridge viewer.');
      }
      const protocol = win.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const current = new RFB(screen, `${protocol}//${win.location.host}/websockify`, { shared: true });
      client = current;
      current.viewOnly = false;
      current.scaleViewport = true;
      current.resizeSession = false;
      current.clipViewport = false;
      current.focusOnClick = true;
      current.background = '#000';
      // Preserve the qualified image settings when upstream defaults change.
      current.qualityLevel = 6;
      current.compressionLevel = 2;

      current.addEventListener('connect', () => {
        if (client !== current || stopped) return;
        cancel(connectTimer);
        connectTimer = null;
        retries = 0;
        status('Connected', true);
        reportSize();
        current.focus();
      });
      current.addEventListener('disconnect', () => {
        if (client !== current || stopped) return;
        client = null;
        cancel(connectTimer);
        connectTimer = null;
        if (failure) status(failure, false, true);
        else retry();
      });
      for (const event of ['credentialsrequired', 'securityfailure', 'serververification']) {
        current.addEventListener(event, () => {
          if (client !== current || stopped) return;
          failure = 'Bridge authentication failed.';
          status(failure, false, true);
          current.disconnect();
        });
      }
      connectTimer = schedule(() => {
        if (client !== current || connected || stopped) return;
        client = null;
        current.disconnect();
        retry();
      }, 12000);
    } catch (error) {
      status(error.message || 'Unable to connect.', false, true);
      if (['127.0.0.1', 'localhost', '[::1]'].includes(win.location.hostname)) retry();
    }
  }

  function reconnect() {
    retries = 0;
    connect();
  }

  function navigate(destination) {
    // Standard XF86 keysyms handled by the pinned phone daemon patch. These
    // are complete button gestures on key-down, so a lost key-up cannot hold Home.
    const keysym = destination === 'home' ? 0x1008FF18 : destination === 'app-switcher' ? 0x1008FF7F : 0;
    if (!keysym || !connected || !client || stopped) return false;
    client.sendKey(keysym, null);
    client.focus();
    return true;
  }

  function stop() {
    stopped = true;
    clearTimers();
    observer.disconnect();
    const previous = client;
    client = null;
    previous?.disconnect();
  }

  button.addEventListener('click', reconnect);
  win.addEventListener('pagehide', stop);
  connect();
  return { reconnect, stop, navigate, currentClient: () => connected ? client : null };
}
