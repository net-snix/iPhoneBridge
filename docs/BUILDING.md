# Build from source

The distributed app needs no development tools. These instructions are for
contributors and people rebuilding or modifying the bundled software.

## Requirements

- An Apple silicon Mac, macOS 26+, Xcode 26.3, and Swift 6.2 or newer.
- `uv`, CMake, Git, pkg-config, and the libimobiledevice/libusbmuxd USB utilities.
- Theos at `16362d3aa83a0acd56df4493d575d34306d42478`, with the
  `iPhoneOS16.5.sdk` in its `sdks/` directory, plus `ldid` for the device build.
- Python 3.13.11 is fetched by `uv`; packages are locked in `uv.lock`.

The device remains untouched during all build steps. Existing checkouts with
unexpected revisions or modifications are rejected rather than reset.

## Build the app

```sh
git clone https://github.com/net-snix/iPhoneBridge.git
cd iPhoneBridge
export THEOS="$HOME/Code/oss/theos"
./scripts/setup
./scripts/stage-device-release
./scripts/build-app
```

`setup` fetches pinned TrollVNC and noVNC sources into ignored `work/vendor/`, then
builds the device daemon and its JPEG, PNG, LZO, and LibVNCServer dependencies from
verified source archives. The platform SDK supplies zlib. The daemon is built
without OpenSSL/SASL because the product carries loopback RFB through SSH.
The default device build directory includes a hash of its inputs so a changed
patch gets a fresh build tree. An explicit `--build-dir` remains available.
`scripts/prepare-viewer` applies the pinned noVNC image-lifecycle patch and
verifies every modified file. It rejects unrelated source changes.

`build-app` compiles Swift, bundles the locked Python environment and USB
utilities with their dynamic dependencies, copies the viewer and verified phone
payload, audits runtime paths, and signs the new bundle locally. It produces
`iPhoneBridge.app`. The output is ad-hoc signed; the script does not use a
Developer ID identity or notarize. Existing output is retained under ignored
`work/previous-app.*`. Quit any running copy before replacing or installing it.

The build host's installed USB package versions become part of the app's
`runtime-manifest.json`; a release must stage corresponding source for those
exact versions. [THIRD_PARTY.md](../THIRD_PARTY.md) records the first release's
versions. Do not assume a newer Homebrew package matches an older source archive.

`IPHONEBRIDGE_TROLLVNC_SOURCE` and `IPHONEBRIDGE_NOVNC_SOURCE` can point to existing
checkouts at the exact locked revisions. `THEOS` selects an existing pinned Theos
checkout. `scripts/setup --check` verifies the current sources and built payload
without contacting the phone.

## Checks

```sh
uv sync --locked
uv run --frozen python -m unittest discover -s tests -v
python3 scripts/prepare-viewer
node --test viewer/*.test.mjs
swift build -c release
./scripts/setup --check
"iPhoneBridge.app/Contents/Helpers/bridge" self-test
```

Unit tests and CI do not require a phone. Live verification is separate; see
[VALIDATION.md](../VALIDATION.md). To rebuild the icon, run `scripts/build-icon`
with Xcode's Icon Composer installed; checked-in PNG/ICNS files are sufficient
for an ordinary app build.
Daemon tests extract complete production functions and headers from the patches;
they do not need vendor sources or downloads. Thirty-five behavior cases cover
publication coalescing, retained reader locks, frame replacement, dirty regions,
rotation, client start/stop and capture deadlines. On macOS, the actual frame
handler uses CoreVideo/vImage under UndefinedBehaviorSanitizer, and the timer
methods run against real Dispatch. Eleven separate pthread/UBSan cases exercise
LibVNCServer's actual snapshot and resize functions. Small attributed fixtures
contain only unchanged pinned upstream helpers; missing patch context fails the
tests. These host regressions do not establish iPhone performance.
Viewer tests exercise the actual prepared noVNC source, including
image failure, timeout, resource release, and disconnect during a pending flush.
Decoder-context tests also cover bounded document ownership, waiting jobs and
cancellation at a context boundary. Native pixel and sustained-use checks are
described in [renderer-memory-2026-09-09.md](renderer-memory-2026-09-09.md).

## Rebuild the released dependencies

Download and extract the matching `corresponding-source.tar.gz` from the release.
It includes the app project, device sources and patches, macOS runtime sources,
exact package formulas, full notices, and SHA-256 inventories.

`device/README.md` documents a fresh offline daemon rebuild. `macos/README.md`
documents Python, native dependencies, USB tools, and replacement of separately
bundled LGPL libraries. Apple SDKs and build-tool prerequisites are not included.
Source-identical builds on another toolchain need not produce byte-identical
binaries. The source archive must accompany the matching app ZIP.
