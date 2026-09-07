# Validation — 2026-09-07

Test hardware: USB-connected iPhone 13 Pro (`iPhone14,2`), jailbroken iOS 15.1.1,
and an Apple silicon Mac. Device identifiers and private screenshots are omitted.
Build toolchain: Xcode 26.3 and Swift 6.2 language mode.

## Development-session evidence

The following checks preceded the standalone release's dependency rebuild:

- The native window displayed the actual phone in portrait and landscape, changed
  aspect ratio after rotation, resized, and entered full screen. An independent
  USB screenshot matched the mirror.
- A human click on the fixture's **Right target** changed its label in the native
  mirror. Some synthetic computer-use clicks/drags did not affect WebKit, so those
  attempts were not counted as proof of human input.
- Real MCP calls changed a counter with a tap, moved a slider from 0 to 98, and
  typed exact mixed-case ASCII and punctuation. After rotation, old dimensions
  were rejected and a fresh landscape tap changed the expected label.
- Portrait framebuffer dimensions were 1172×2536; one landscape capture was
  2532×1170. The server can align buffers: nominal hardware dimensions must never
  replace the dimensions returned by a fresh screenshot.
- Toolbar Home/App Switcher actions worked on the phone. App-local ⌘1/⌘2 handling
  before WebKit dispatch was verified by visible Home/Switcher transitions.
- A CLI upward drag closed a disposable Calculator card while other app cards
  remained. No unrelated app was terminated for cleanup or deployment.
- Stop/restart removed owned phone and Mac processes and released bridge ports.
  Pre-existing USB forwards kept their original process identities.

The native navigation path uses the current RFB connection. CLI navigation waits
for a screenshot; the toolbar does not launch Python or capture a new image for
each press. Historical navigation daemon SHA-256:
`a631bf9c1492114f819d15e6d4e897c1976c233257350ef39bb3e839ad0ceb43`.
Performance measurements and their exact scope are in [PERFORMANCE.md](PERFORMANCE.md).

## Standalone release evidence

The app was copied into `/Applications`, launched from there, and ran entirely
from its bundled runtime with an Apple-only PATH. The final source-built daemon
SHA-256 is `7d8fac46d4844bbfe5e25105bce786e2cb7b023d001b15babafd69d414240fb2`.

- The installed app displayed the actual animated phone screen. Toolbar Home
  and App Switcher, plus ⌘1/⌘2, produced visible phone transitions and returned
  to the active app without terminating it.
- Native Settings discovered the USB phone, saved its selection, and reconnected
  successfully. The saved SSH-key field remained empty and default keys worked.
- A real MCP client launched the installed helper, listed all six tools, and
  decoded its screenshot PNG at the reported 1172×2536 dimensions.
- Installed CLI navigation passed size probe, actual input, and post-action
  capture in sequence, visibly opening App Switcher and returning to the app.
- The viewer served real HTTP responses and WebSocket traffic. Health checks
  now test an actual viewer page rather than just a listening port.
- A temporary SSH probe to the phone's IPv6 RFB endpoint returned **Connection
  refused**. The device source explicitly disables that listener for loopback
  mode; native navigation mappings are verified in the compiled source.
- Native Quit completed cleanup; all three owned services stopped. Existing
  shared USB forwards retained their exact process/start identities.

Live testing found and fixed the bundled Python multiprocessing entrypoint,
a skipped source patch, and Darwin endian detection. Regression checks cover
the failure modes rather than relying on a successful build alone.

## Automated and distribution checks

- 48 Python tests cover coordinate bounds, stale orientation, serialization,
  release on failure, typing, useful MCP errors, process ownership, cleanup,
  native navigation, CLI validation, device selection, runtime portability,
  spawned HTTP/WebSocket requests, source patching, and byte-order checks.
- 17 viewer/benchmark tests cover live-session navigation, connection handling,
  orientation reporting, and measurement validation.
- Swift release compilation passes with concurrency checking.
- An independent fresh offline rebuild verifies the device source package.
- Locked source and notice inventories are verified before packaging: 128
  retained notice entries and all 55 locked Python source distributions.
- A relocated runtime passes imports, device-payload hash verification, USB
  utility execution, and an MCP initialize/list-tools handshake with six tools.

## Limits

Single-finger input and ASCII/US hardware-keyboard mapping are qualified.
Arbitrary Unicode, multitouch, all special keys, other iOS versions, and every
phone model are not. Human input when launching directly into landscape has not
been separately qualified. There is no audio forwarding. Tests are not a claim
of notarization or independent security review.
