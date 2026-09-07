# CLI and MCP

For an installed app, use `/Applications/iPhoneBridge.app/Contents/Helpers/bridge`.
For a source checkout, use `./bridge` after following [BUILDING.md](BUILDING.md).
The examples below use the source launcher; both expose the same commands.

```sh
./bridge devices
./bridge configure --udid YOUR_USB_DEVICE_ID --identity "$HOME/.ssh/id_ed25519"
./bridge connect
./bridge health
./bridge screenshot
./bridge stop
```

`configure --clear-udid --clear-identity` restores automatic single-device selection
and default SSH keys/agent. Stop before changing connection settings. No private
key is included with the app or copied onto the phone.

Use coordinates and dimensions from a fresh screenshot:

```sh
./bridge tap 270 920 --size 1172 2536
./bridge drag 150 1365 1010 1365 --duration 0.8 --size 1172 2536
printf '%s' 'Bridge test 123' | ./bridge type --size 1172 2536
./bridge key enter --size 1172 2536
./bridge navigate home --size 1172 2536
./bridge navigate app-switcher --size 1172 2536
```

These are examples, not coordinates to reuse blindly. `swipe` aliases `drag`.
Drags last 0.1–5 seconds. Typing accepts up to 256 printable ASCII characters plus
tab/newline. `key home` is a keyboard key; `navigate home` presses the phone's Home
button. CLI input returns a fresh image; native navigation uses the existing live
viewer socket without spawning a CLI process.

MCP tools are `screenshot`, `tap`, `drag`, `type_text`, `key`, and `health`.
Images include their actual dimensions and a local path. MCP health checks RFB;
CLI health also checks USB, pairing, SSH, and owned processes. A fresh screenshot
is required after an ambiguous input failure before deciding whether to retry.
Input text is omitted from bridge metadata, but can appear in screenshots or the
calling client's history.

## Recovery and diagnostics

The app writes to `~/Library/Application Support/iPhoneBridge/`. Source runs use
ignored `work/` and `outputs/screenshots/`. Use `status`, `health`, and logs under
`logs/` to diagnose a connection. The standalone `self-test` checks bundled imports
and payload integrity without contacting the phone.

After a cable interruption, reconnect the same phone and choose Reconnect. If
cleanup cannot be confirmed remotely, ownership records remain for safe recovery.
Do not delete `state.json` to bypass a cleanup failure. The bridge owns USB SSH
port 15422, RFB port 15901, viewer port 15801, and optional fixture port 15802.
An occupied unrelated port is an error, not permission to terminate its owner.

`test-surface` serves a disposable HTML fixture through an optional USB reverse
forward. Open `http://127.0.0.1:15802/test.html` in phone Safari as a separate action.
The server and tunnel stop with the bridge; the Safari tab remains. Opt-in latency
measurement uses `latency.html` and the app's `--benchmark` flag; see
[PERFORMANCE.md](../PERFORMANCE.md).
