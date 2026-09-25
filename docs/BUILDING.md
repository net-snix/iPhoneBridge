# Build from source

Use an Apple silicon Mac, macOS 26+, Xcode 26.3 / Swift 6.2, `uv`, Git,
libimobiledevice/libusbmuxd USB tools, and the pinned Theos checkout at
`16362d3aa83a0acd56df4493d575d34306d42478` with `iPhoneOS16.5.sdk` and `ldid`.
The phone remains on jailbroken iOS 15.1.1. Build steps do not contact it.

```sh
uv sync --locked
./scripts/setup
./scripts/setup --check
./scripts/stage-device-release
```

The native phone sources are checked in under `device/`. Retained TrollVNC
private API declarations and their provenance are pinned in
`device-sources.lock.json`; `dependency-lock.json` pins the toolchain. The build
uses Apple's VideoToolbox HEVC encoder with default encoder selection and platform
libraries. Hardware acceleration is the intended path, but the qualified iOS 15.1.1
phone did not provide a Boolean encoder-list hardware flag, so the daemon does not claim
runtime hardware verification. It does not fetch or compile a VNC server, browser
viewer, or software video codec. See [native-hevc-implementation.md](native-hevc-implementation.md)
for the encoder-identity probe and its limits.

Every build records the complete source-file inventory, source tree digest,
recipe, lock, session script, toolchain and final binary hash in
`work/build-manifest.json`. Changed inputs use a fresh build directory.
`THEOS` or `scripts/build-device-deps --theos` selects the existing toolchain.
Unknown revisions or source modifications are rejected; setup never resets an
unrelated checkout. Binary reproducibility across toolchains is not promised.

## Native app development

```sh
swift build -c release
IPHONEBRIDGE_HELPER="$PWD/bridge" IPHONEBRIDGE_DATA_DIR="$PWD/work" \
  .build/release/iPhoneBridge
```

The standalone SwiftPM executable supports source development without assembling
or signing a Mac app bundle. `--attach` connects to an already-running native
endpoint and leaves its service lifecycle to the caller. It is useful when
comparing or qualifying a candidate through `./bridge start` / `./bridge stop`.
Normal app launches own their connection and stop it on quit.

`./scripts/build-app` assembles the self-contained app, including Python, USB
utilities, original notices and the verified phone payload, and ad-hoc signs the
new bundle. Prepare the exact Mac corresponding source and its checksum inventory
at `work/release-sources/macos` first, following [RELEASING.md](RELEASING.md).
Runtime assembly verifies and records that inventory; packaging checks it against
the reviewed commit and actual runtime identities. Follow the repository's signing authorization requirements before
running it. It preserves previous output under `work/previous-app.*`. Do not
replace a running installed copy. The bundle ID remains `net.snix.iPhoneBridge`.

## Checks

```sh
uv run --frozen python -m unittest discover -s tests -v
swift test
swift build -c release
./scripts/setup --check
.venv/bin/python scripts/check-mcp
```

The first four checks are local. `check-mcp` requires the native phone endpoint
and verifies an actual lossless screenshot through the six-tool stdio MCP API.
The tests cover wire byte vectors and malformed/partial messages, capture-slot
ownership, socket backpressure, rotation, input leases/releases, native decoding,
benchmark guards, lifecycle ownership and build/source tampering. They establish
software behavior; sustained phone performance requires the live gates in
[VALIDATION.md](../VALIDATION.md).

## Corresponding source

`stage-device-release` creates a verified payload and a complete offline source
package. Its defaults are `work/device-release` and
`work/release-sources/device`; both must be new directories. Use `--payload-dir`
and `--source-dir` to stage another candidate without overwriting earlier work.
The package includes every native source/header, entitlements, build recipe,
locks, session helper, source inventory and retained notices.

```sh
python3 /path/to/device-source/scripts/build-device-deps \
  --source-package /path/to/device-source \
  --theos "$HOME/Code/oss/theos" --build-dir /tmp/native-device-rebuild
```

Mac runtime source must also match the exact distributions and USB libraries
actually bundled. Follow [RELEASING.md](RELEASING.md); a device-only source
package is insufficient for distributing the self-contained app.
