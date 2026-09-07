# Live validation — 2026-09-07

Tested on the physical USB-connected iPhone `00008110-001914103EB8801E`, iPhone14,2, iOS 15.1.1. Pairing, mobile SSH and RFB 3.8 passed. The Mac app was built with Xcode 26.3 / Swift 6.2 language mode and launched as `iPhoneBridge.app`.

## Native mirror

- Launch opens a standalone AppKit/WebKit window and starts the bridge automatically.
- The window showed the actual landscape phone screen at 2532×1170 and the portrait screen at 1172×2536, scaling into an 850 pixel wide landscape window and 380 pixel wide portrait window.
- Fresh libimobiledevice screenshots independently matched the mirrored phone content. The portrait clock and page updates appeared in the Mac window.
- Espen clicked `Right target` in the native Mac window and confirmed that the label changed to `Target: right`; the updated label was then independently observed in the native window. Synthetic CUA clicks had not produced this effect, so they were not counted as human-input proof.
- The app's native Quit menu completed owned cleanup, removed `work/state.json`, and exited. Reopening connected successfully.
- Initial capture was configured for 30 fps. The subsequent latency pass qualified 60 fps capture with precise changed-region updates; measured delivery and latency are in [PERFORMANCE.md](PERFORMANCE.md). No audio implementation.
- Human interaction was confirmed in portrait. MCP landscape input was confirmed after physical rotation during the earlier running session; human input after launching directly in landscape has not been separately qualified.

## Home and App Switcher controls

- The native toolbar's Home Screen button returned Safari to the Home Screen. App Switcher opened the actual phone's app cards. The View menu action also reached the same native navigation command.
- The first implementation launched the CLI for each button press; Espen reported it was slow. Native buttons now use the existing viewer connection and public `RFB.sendKey` API, with standard XF86HomePage/TaskPane keysyms. The pinned daemon patch maps key-down to upstream self-releasing Home/Home-double-press helpers. No CLI process, screenshot capture or completion wait is involved in native navigation.
- Navigation-enabled daemon SHA-256 `a631bf9c1492114f819d15e6d4e897c1976c233257350ef39bb3e839ad0ceb43` was deployed and verified running at full scale with the qualified `-F 60 -P 60 -d 0` settings. A native Home toolbar press on this build visibly changed the phone's Home page.
- An upward CLI drag on the visible Calculator card removed it from the switcher; the resulting native window retained Safari and Pokémon GO cards. Local evidence: `20260907T191918.062868Z-4ed82b22-drag.png`. CUA's synthetic drag inside WebKit did not act on the phone and is not counted as proof of human dragging; human clicking was verified separately above.
- Home follows the phone's native behavior: pressing it in App Switcher returned to Safari, and a second press returned to Home. This is documented in everyday use.
- Native menu shortcuts are configured as ⌘1 and ⌘2. Espen reported that the first menu-only implementation did not work. The corrected build uses an app-local key-down monitor before WebKit, scoped to the focused mirror window. After raising that window, CUA ⌘2 visibly opened App Switcher and ⌘1 visibly returned to the Home Screen. The native keyboard path is now verified on the phone; a human repeat of this final check remains optional.

## Physical input through MCP

These were actual stdio MCP calls made using the official MCP client, with native PNG responses inspected after input. They are separate from the unit tests and from the human viewer.

| Action | Observed result | Local evidence |
| --- | --- | --- |
| Portrait tap `(270,920)` at 1172×2536 | Counter changed from 0 to 1 | `20260907T181422.995976Z-c50a998a-tap.png` |
| Portrait drag `(150,1365)` → `(1010,1365)` | Slider changed from 0 to 98 | `20260907T181450.759442Z-b203e052-drag.png` |
| Typing with explicit Shift | Exact ` AZaz09!@#_+?:` appeared in the field | `20260907T182019.768140Z-5520bf14-type.png` |
| User physically rotated phone | Capture changed to 2532×1170; old portrait dimensions rejected | `20260907T181930.348492Z-4777252c-screenshot.png` |
| Landscape tap `(1850,550)` | Label changed to `Target: right` | `20260907T182220.741703Z-1c1e271a-tap.png` |
| Landscape upward swipe | Scrolled toward the bottom marker, subsequently visible in native mirror | `20260907T182600.979015Z-cb1b6731-drag.png` |
| Portrait tap after restart `(339,1441)` | Label changed to `Target: left` | `20260907T184117.095968Z-5f2b5186-tap.png` |

Images remain local and ignored under `outputs/screenshots/`; they are not repository fixtures. Exact sizes are authoritative: the server aligns portrait buffers, so callers must not substitute the nominal 1170×2532 hardware dimensions. Input is single-finger and ASCII with a US hardware keyboard mapping. An arbitrary app, every special key, Unicode, and multitouch have not been qualified.

## Lifecycle and boundaries

The hardened device session helper was deployed, started, stopped, and restarted. The cleanup probe verified that device daemon PID 11151 exited, remote `active/` disappeared, recorded local processes ended, and ports 15901/15801/15802 were released. Shared iproxy PIDs 86023 (USB SSH) and 95937 (Waypoint) retained their exact start/command identities. Local evidence: `work/cleanup-validation.json`.

New Mac listeners bind to 127.0.0.1. The patched daemon binds device IPv4 loopback and disables its IPv6 RFB listener; a connection to device `[::1]:15901` was refused. No LAN relay, package installation, launchd change, root password, jailbreak change, pairing reset, iOS update, respring or reboot was performed. The later app-navigation test closed Calculator through its switcher card; it did not terminate Pokémon GO.

## Build and checks

- 33 Python tests passed: coordinate bounds, stale orientation, serialized input, release on failure, shifted typing, useful MCP errors, PID ownership, cleanup, native Home event sequences and navigation CLI validation.
- 4 viewer tests passed: configuration/orientation messages, reconnect cleanup, authentication failure handling, and navigation over the current connection without sending into disconnected or retired sessions.
- Native release build passed without warnings after matching current WebKit delegate concurrency annotations.
- `scripts/setup --check` passed pinned source, archive, patch, viewer and binary checks.
- `scripts/check-mcp` verified six tools and decoded the returned native PNG against its reported dimensions.
- Codex `iphonebridge` MCP registration is enabled. Existing sessions may need reopening to refresh the callable tool catalog; live protocol testing here used a real stdio client.

See `dependency-lock.json`, `uv.lock`, `work/build-manifest.json`, and `THIRD_PARTY.md` for exact source/build/license identity. No public binary release or push was performed.
