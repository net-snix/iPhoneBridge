// Opt-in benchmark guard: covered/minimized WebKit views are throttled.
export function observeVisibility(doc, clock) {
  const available = typeof doc?.hidden === 'boolean';
  let hiddenSince = available && doc.hidden ? clock.now() : null;
  let hiddenMs = 0, changes = 0, hadHidden = hiddenSince !== null;
  function changed() {
    const now = clock.now();
    if (hiddenSince !== null) hiddenMs += now - hiddenSince;
    hiddenSince = doc.hidden ? now : null;
    hadHidden ||= doc.hidden;
    changes++;
  }
  if (available) doc.addEventListener('visibilitychange', changed);
  function snapshot() {
    return { available, hidden: available ? doc.hidden : null,
      focused: doc?.hasFocus?.() ?? null, hadHidden,
      hiddenMs: hiddenMs + (hiddenSince === null ? 0 : clock.now() - hiddenSince), changes };
  }
  return { snapshot, stop() {
    if (available) doc.removeEventListener('visibilitychange', changed);
    return snapshot();
  } };
}
