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
node --test viewer/*.test.mjs
swift build -c release
./scripts/setup --check
"iPhoneBridge.app/Contents/Helpers/bridge" self-test
```

Unit tests and CI do not require a phone. Live verification is separate; see
[VALIDATION.md](../VALIDATION.md). To rebuild the icon, run `scripts/build-icon`
with Xcode's Icon Composer installed; checked-in PNG/ICNS files are sufficient
for an ordinary app build.

## Rebuild the released dependencies

Download and extract the matching `corresponding-source.tar.gz` from the release.
It includes the app project, device sources and patches, macOS runtime sources,
exact package formulas, full notices, and SHA-256 inventories.

`device/README.md` documents a fresh offline daemon rebuild. `macos/README.md`
documents Python, native dependencies, USB tools, and replacement of separately
bundled LGPL libraries. Apple SDKs and build-tool prerequisites are not included.
Source-identical builds on another toolchain need not produce byte-identical
binaries. The source archive must accompany the matching app ZIP.
