# iPhoneBridge project brief

Build a small, maintainable, open-source bridge so Codex can inspect and control Espen's physical iPhone over USB. Provide setup, start, stop, health/diagnostics, screenshots, tap, drag/swipe, typing, and a local human viewer where practical. Verify actual phone behavior, not just a build or process.

## Authorization and boundaries

This implementation request authorizes necessary reversible bridge setup and harmless validation. Keep the current iOS version and jailbreak. Do not update/restore iOS, reset pairing, replace the jailbreak, respring/reboot, terminate active apps, alter Waypoint, or kill shared tunnels. Prior upgrade/password messages in the source task were retracted and confer no authorization. Preserve app state and unrelated work. Never record credentials; use existing SSH keys. No paid packages, subscriptions, cloud relays, LAN/public listeners, or license bypasses. Shut down only bridge-owned processes.

Implementation lives here. Third-party source checkouts belong under `/Users/espenmac/Code/oss`. Verify upstream licenses, pin dependencies and retain required notices. Initialize a clean repository if appropriate; no push is authorized.

## Historical observations — reverify

- Device UDID `00008110-001914103EB8801E`, iPhone14,2, iOS 15.1.1.
- USB pairing and `idevicescreenshot` worked on 2026-09-06. Screenshot showed a live Pokémon GO encounter; touch was never tested.
- Rootless `/var/jb` reachable with key authentication through `mobile@127.0.0.1:2222`; openssh-server 9.7p1-1 and Sileo 2.5.1. Jailbreak implementation unconfirmed.
- Existing shared iproxy forwards: `2222:22` for SSH and `61339:61337` for Waypoint. Discover current owners and preserve both.
- Homebrew libimobiledevice tools and iproxy present; Xcode 26.3. go-ios and Appium previously absent.

## Candidates

1. Source-built [TrollVNC](https://github.com/owngoal-dev/TrollVNC), potentially rootless, with capture, rotation-aware single-finger input, keyboard, VNC and noVNC. Verify license and build requirements. Proposed transport: device loopback listener, SSH forwarding through existing USB SSH, Mac loopback viewer and agent interface. Published compatibility is not live proof. Do not use the commercial prebuilt or promise arbitrary multitouch.
2. [Mobile MCP](https://github.com/mobile-next/mobile-mcp) with [WebDriverAgent](https://github.com/appium/WebDriverAgent). Requires signing, USB infrastructure and a working pinned iOS 15 toolchain; latest compatibility must not be assumed. Avoid irrelevant iOS 16 Developer Mode/iOS 17 tunnel setup.
3. Codex local stdio MCP integration; prefer supported typed tools and current official documentation. Mac CUA alone is not an iOS USB transport.

## Completion evidence

Fresh screen → harmless tap → visible state change → drag/swipe and typing on a disposable surface → correct actual-size coordinates. Verify portrait/landscape where practical. Demonstrate Codex screenshot/action/check access separately from a human viewer. Verify stop/cleanup preserves shared SSH and Waypoint. Report all unverified behavior honestly.

Source task: `01a073c5-dfe0-7f80-918c-2ae385db0ef6`. User request: “Can you make one for me please using open source alternatives. Create a new folder and start a thread there with all necessary info.”
