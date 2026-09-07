// Explicit diagnostics only: the normal mirror never imports this module.
import { installBenchmark } from './benchmark.mjs';

export async function coordinateBenchmark(mirror) {
  let previous = '';
  let stopped = false;
  let active = null;
  addEventListener('pagehide', () => { stopped = true; active?.stop(); }, { once: true });
  while (!stopped) {
    try {
      const request = await fetch('http://127.0.0.1:15802/benchmark-run', { cache: 'no-store' }).then(r => r.json());
      const client = mirror.currentClient();
      if (client && request.id && request.id !== previous) {
        previous = request.id;
        active = installBenchmark({ client, screen: document.getElementById('screen') });
        if (request.mode === 'input') await active.runInput(request);
        else if (request.mode === 'animation') await active.runAnimation(request);
        active.stop();
        active = null;
      }
    } catch (error) {
      // A stopped USB fixture server is normal between profile changes.
      console.warn('Benchmark coordination:', error.message);
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
}
