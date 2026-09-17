# CLI and MCP

For an installed app, use `/Applications/iPhoneBridge.app/Contents/Helpers/bridge`.
For a source checkout, use `./bridge` after following [BUILDING.md](BUILDING.md).
The examples below use the source launcher. Input requires the `generation` from
the screenshot as well as its dimensions. Older installed builds can expose the
same commands without this check; use the updated source MCP adapter until a
matching app is packaged.

Screenshots preserve the captured RGB values exactly and declare `color_space:
sRGB` in metadata and the PNG. The retained capture API produces SDR sRGB;
independent Apple USB screenshots can use Display P3. Exact P3 pixel parity
remains an unpassed qualification check. Video and stills share capture geometry;
stills are read directly from the capture surface and never decoded from HEVC.

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

Use coordinates, dimensions and generation from a fresh screenshot. In these
examples `2` stands for that screenshot's generation, not a fixed device value:

```sh
./bridge tap 270 920 --size 1170 2532 --generation 2
./bridge drag 150 1365 1010 1365 --duration 0.8 --size 1170 2532 --generation 2
printf '%s' 'Bridge test 123' | ./bridge type --size 1170 2532 --generation 2
./bridge key enter --size 1170 2532 --generation 2
./bridge navigate home --size 1170 2532 --generation 2
./bridge navigate app-switcher --size 1170 2532 --generation 2
```

These are examples, not coordinates to reuse blindly. `swipe` aliases `drag`.
Drags last 0.1–5 seconds. Typing accepts up to 256 printable ASCII characters plus
tab/newline. `key home` is a keyboard key; `navigate home` presses the phone's Home
button. CLI input returns a fresh image; native navigation uses the existing live
native socket without spawning a CLI process. The native portrait size on the
tested phone is 1170×2532, replacing the old aligned 1172×2536 RFB framebuffer.
Landscape dimensions are 2532×1170. Always use the returned dimensions and
generation. Even a 180° rotation keeps the dimensions but invalidates the old
generation; stale input is rejected before a touch or key is sent.

MCP tools are `screenshot`, `tap`, `drag`, `type_text`, `key`, and `health`.
Images include their actual dimensions, generation, capture timestamp and a local
path. They are lossless stills from capture surfaces, independent of HEVC video.
MCP health reads the IPBM/1 greeting without subscribing or acquiring input;
CLI health also checks USB, pairing, SSH, and owned processes. A fresh screenshot
is required after an ambiguous input failure before deciding whether to retry.
Input text is omitted from bridge metadata, but can appear in screenshots or the
calling client's history. Agent actions hold an exclusive input lease through
post-action capture. Human gestures and typing use the same ownership policy;
an overlapping action reports that input is busy. Disconnect and lease timeout
release held keys and touches. Rotation during a gesture rejects its generation.

## GPT-6 Astra through Codex

The native checkout's `.codex/config.toml` selects `gpt-6-astra`, `xhigh` reasoning
and Standard processing. Codex loads project configuration only for trusted
projects; confirm the model selected by the actual task as well. iPhoneBridge
does not call an OpenAI API or need its own model credential.

Keep the installed native app running and register this native checkout's adapter
from the repository root after installing the locked Python dependencies:

```sh
uv sync --locked
codex mcp add iphonebridge -- "$PWD/bridge" mcp
codex mcp get iphonebridge --json
```

The adapter talks to the existing `IPBM/1` endpoint and does not start or replace
phone services. The old RFB checkout is incompatible with that endpoint. Restart
the MCP connection or open a new Codex task after changing its registration.
Keep the configured source checkout available while it is registered.

Use one screenshot → action → image inspection sequence at a time. Pass `width`,
`height` and `generation` from the inspected image on every input tool call. If
input is busy, wait for the current operator. If an action may have been applied,
inspect a new screenshot before deciding whether another action is appropriate.
Do not parallelize phone input across workers. A successful transport response
alone does not establish the requested visible result.

Check discovery and an actual lossless PNG without sending input:

```sh
.venv/bin/python scripts/check-mcp --command "$PWD/bridge"
```

The native app and source adapter are separate artifacts. Once a matching app is
packaged and qualified, register its installed helper as shown in the README.
Keep the native protocol aligned when rolling back configuration; pointing an
RFB adapter at a running native daemon is not a valid rollback.

See [Astra migration guidance](https://developers.openai.com/api/docs/guides/latest-model#gpt-6-astra-update-api-and-model-parameters),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
and [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).

## Recovery and diagnostics

The app writes to `~/Library/Application Support/iPhoneBridge/`. Source runs use
ignored `work/` and `outputs/screenshots/`. Use `status`, `health`, and logs under
`logs/` to diagnose a connection. The standalone `self-test` checks bundled imports
and payload integrity without contacting the phone.

After a cable interruption, reconnect the same phone and choose Reconnect. If
cleanup cannot be confirmed remotely, ownership records remain for safe recovery.
Do not delete `state.json` to bypass a cleanup failure. The bridge owns USB SSH
port 15422, native mirror port 15901, and optional fixture port 15802.
An occupied unrelated port is an error, not permission to terminate its owner.

`test-surface` serves a disposable HTML fixture through an optional USB reverse
forward. Open `http://127.0.0.1:15802/test.html` in phone Safari as a separate action.
The server and tunnel stop with the bridge; the Safari tab remains. Opt-in latency
measurement uses `latency.html` and the app's `--benchmark` flag; see
[PERFORMANCE.md](../PERFORMANCE.md).
