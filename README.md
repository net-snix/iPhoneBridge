# iPhoneBridge

Live iPhone mirroring in a standalone Mac window. Open the app, see the phone, click or drag its screen, and type using the Mac keyboard. The window follows portrait/landscape orientation and supports resizing and full screen. The connection runs locally over USB.

Built for Espen's jailbroken iPhone 13 Pro (`iPhone14,2`), iOS 15.1.1, and this Mac. This is not a general installer for stock iPhones. It provides screen and input mirroring; audio, notification integration, and Apple's locked-phone continuity features are not implemented.

## Everyday use

Double-click **iPhoneBridge.app** in this directory, or run:

```sh
./bridge
```

The app starts the bridge automatically and fills the window with the live phone screen. Keep the iPhone awake and unlocked. Use **View → Reconnect** after a disconnected cable, and **View → Enter Full Screen** for a larger view. Quitting the app stops the bridge, including any CLI/MCP connection using it. Capture targets 60 fps and sends precise changed regions without an added coalescing delay. The live test delivered about 55 fps during motion and 59 fps for sparse changes, with about 98 ms input-to-visible response; see [PERFORMANCE.md](PERFORMANCE.md) for the comparison and measurement limits.

The two buttons at the top right provide **Home Screen (⌘1)** and **App Switcher (⌘2)**, also available in the View menu. Focus the mirror window to use its shortcuts. To close an iPhone app, open App Switcher, drag left or right to find its card, then drag that card upward off the screen. Click a card to switch to it. Home sends the phone's native Home button event; from App Switcher it can first return to the selected app, so press again to reach the Home Screen. Controls are disabled while disconnected or reconnecting.

To build the local Mac app again:

```sh
./scripts/build-app
```

This SwiftPM build creates the local `.app` without invoking codesign or notarization. The app records this checkout's path in its Info.plist and needs the checkout's Python environment and sources. It is not a self-contained distribution bundle.

For diagnostics or agent-only operation:

```sh
./bridge start
./bridge health
./bridge screenshot
./bridge stop
```

The same live view is available in a [local browser](http://127.0.0.1:15801/) after starting. `./bridge viewer` prints its URL. Keep human input and agent actions separate in time; the bridge serializes its own agent actions, but cannot serialize someone touching the phone or using the viewer.

`stop` terminates only processes whose recorded identity still matches this bridge. It also stops the optional test surface. Existing USB SSH and Waypoint forwards are preserved. Services are started manually and do not persist through a phone reboot. If USB disconnects, reconnect and run `stop`, then `start`; ownership state is retained when remote cleanup cannot be confirmed. Never delete `work/state.json` to bypass a cleanup failure.

## Codex

The local `iphonebridge` MCP server has been registered on this Mac with:

```sh
codex mcp add iphonebridge -- /Users/espenmac/Code/iPhoneBridge/bridge mcp
```

New Codex sessions should load it. An already open session may need reopening to refresh its tool list. Start the bridge with the CLI first; starting MCP alone does not launch phone services.

Tools: `screenshot`, `tap`, `drag`, `type_text`, `key`, and `health`. Screenshots and successful input calls return a native PNG plus its path and exact dimensions. The MCP `health` tool checks the local RFB endpoint; the CLI `health` additionally checks USB, pairing, iOS, SSH and owned processes.

Always screenshot first. Use **raw framebuffer pixels** and supply the returned width and height with every input action. Do not use coordinates from a resized preview. Rotation can change dimensions; stale dimensions are rejected before input. After an action, inspect its image to confirm the intended effect. If verification fails, take another screenshot before deciding whether to retry.

```sh
# Example only: replace coordinates and dimensions using a fresh screenshot.
./bridge tap 270 920 --size 1172 2536
./bridge drag 150 1365 1010 1365 --duration 0.8 --size 1172 2536
printf '%s' 'Bridge test 123' | ./bridge type --size 1172 2536
./bridge key enter --size 1172 2536
./bridge navigate home --size 1172 2536
./bridge navigate app-switcher --size 1172 2536
```

`swipe` is a CLI alias for `drag`. Input supports one finger, 0.1–5 second drags, and up to 256 printable ASCII characters plus tab/newline using a US hardware keyboard layout. Arbitrary Unicode and multitouch/pinch are unsupported. `key home` is a keyboard navigation key; `navigate home` sends the iPhone Home button event, and `navigate app-switcher` sends a double press. The CLI waits for a fresh screenshot. The native toolbar sends these gestures directly over the existing live connection for a faster response. The six MCP tools remain the same. Do not send secrets; input text is omitted from bridge metadata/logs but may appear in screenshots and the calling client's history.

## Setup and deployment

Already prepared here. Prerequisites for rebuilding are Homebrew `uv`, libimobiledevice/usbmuxd tools, `ldid`, Xcode, and the pinned existing Theos checkout with `iPhoneOS16.5.sdk`. Source paths, commits, static archive hashes and build command are in `dependency-lock.json`; Python packages are locked in `uv.lock`.

```sh
./bridge setup            # Creates venv, verifies/clones sources, builds CLI if needed
./scripts/setup --check   # Read-only source/artifact verification
./bridge deploy           # While stopped: copy and checksum only bridge-owned files
./bridge start
```

The device must already be paired and jailbroken, with working key authentication as `mobile` through the existing device-specific USB forward:

```text
iproxy -u 00008110-001914103EB8801E 2222:22
```

The bridge verifies that exact existing forward and iOS 15.1.1. It does not create or replace the shared tunnel, reset pairing, install a jailbreak, update iOS, install a package, or configure launchd. Device files are confined to `/var/mobile/Media/iPhoneBridge`. Deployment uses no root password. If the phone or version changes, review compatibility and update the explicit configuration before use.

TrollVNC is compiled from its pinned GPL source with an IPv6 listener fix and two navigation key mappings. Its upstream supplied static dependencies are hash pinned, not rebuilt from source here. No compiled daemon is committed or publicly distributed. Original bridge code is MIT; see [THIRD_PARTY.md](THIRD_PARTY.md) and retained [licenses](licenses/) for the separate upstream terms and build provenance limitations.

## Transport

```text
Codex MCP / CLI ── RFB 127.0.0.1:15901 ─┐
Browser ── noVNC 127.0.0.1:15801 ───────┤
                                       └─ SSH through USB iproxy :2222
                                            └─ phone 127.0.0.1:15901 TrollVNC
```

New listeners are loopback only. The daemon's IPv6 RFB listener is disabled, Bonjour/clipboard/file transfer and built-in HTTP are off. RFB has no separate password; SSH protects the USB hop, and local processes on either device can access its loopback endpoint. There is no network discovery or cloud relay.

## Validation and diagnostics

```sh
.venv/bin/python -m unittest discover -s tests -v
node --test viewer/mirror.test.mjs
node --test viewer/benchmark.test.mjs
.venv/bin/python scripts/check-mcp   # Real MCP handshake and native PNG; no input
./bridge health
```

See [VALIDATION.md](VALIDATION.md) for actual phone evidence and its limits. Screenshots are private files in `outputs/screenshots/`. Local ownership, deployment and build records, plus service logs, are in ignored `work/`. `work/device.log` includes the remote daemon output; `work/viewer.log` contains viewer connection diagnostics.

For a disposable test page, run `./bridge test-surface`, then open `http://127.0.0.1:15802/test.html` in Safari on the phone. It serves only the bundled fixture through a loopback USB reverse forward, with no external resources or saved form data. Opening a page is an explicit separate action. The test server and tunnel are removed by `stop`; the Safari tab itself remains.

Latency diagnostics are opt-in (`open iPhoneBridge.app --args --benchmark`) and use the separate disposable `latency.html` page. The normal app neither loads the benchmark module nor sends test input. Reproduction commands and calibration safeguards are in [PERFORMANCE.md](PERFORMANCE.md).
