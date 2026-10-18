# Project status

Use [README.md](README.md) for supported configurations and operation,
[VALIDATION.md](VALIDATION.md) for live evidence, and
[docs/BUILDING.md](docs/BUILDING.md) for source builds.

Before device work, inspect `health` and `status`. Preserve existing iOS,
jailbreak, pairing, active apps, and unrelated tunnels. Runtime ownership records
are required for safe cleanup; do not discard them while a session may be active.
The app and every source checkout share `~/Library/Application Support/iPhoneBridge/`,
so `status` shows the one session per Mac and the checkout or app that started it.
Do not `stop` a session you did not start unless asked; `stop --own` is the model.
