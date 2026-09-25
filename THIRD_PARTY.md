# Third-party software and corresponding source

iPhoneBridge's original Swift, Python and shell code is MIT licensed. The phone
code in `device/`, including new files combined with retained TrollVNC code, is
GPL-2.0-only. Every dependency retains its own license. Complete retained notices
are in `licenses/`, with source identities and hashes in `licenses/provenance.json`.

## Device daemon

The daemon retains private API declarations and jetsam support from TrollVNC
`a3e40816ea5b93a7c80c09625175893d15bd1070`. Its capture and input implementations
are derived from that same revision. `device-sources.lock.json` identifies each
retained or derived upstream file; source headers keep the attribution and
`device/vendor/COPYING` contains GPLv2. The build verifies unmodified retained
files against their recorded hashes.

`scripts/build-device-deps` compiles the complete `device/` tree with Theos and
Apple's iPhoneOS16.5 SDK. The video path uses system VideoToolbox hardware HEVC;
the daemon links no LibVNCServer, JPEG, PNG, LZO or other third-party static codec
library. Apple frameworks, SDKs and tools are platform prerequisites and are not
copied into the payload. `trollvncserver` remains the executable name to preserve
the existing ownership and deployment boundary; it serves only IPBM/1.

`scripts/stage-device-release` exports the exact compiled source inventory,
Makefile, entitlements, retained headers/notices, build recipe and session helper.
It rejects changed sources, locks, toolchain identities, helpers or binaries.
The source package includes offline rebuild instructions. Theos and Apple's SDK
remain explicit build prerequisites; no upstream prebuilt TrollVNC library is used.

## Native Mac and Python runtime

The Swift viewer uses Apple's AppKit, Network, VideoToolbox and AVFoundation
frameworks. Python uses the MCP SDK and Pillow. Python versions and transitive
artifact hashes are pinned in `uv.lock`; installed source, metadata and license
files remain in the bundled environment. Python runs in a separate host process
from the GPL phone daemon.

Pillow's wheel contains image codec libraries even though screenshots use lossless
PNG. Retain the complete Pillow notices, including the Independent JPEG Group,
libjpeg-turbo and libpng notices. Removing phone JPEG encoding does not remove
those Mac runtime dependencies. `licenses/libjpeg-turbo/` and `licenses/libpng/`
are historical upstream license references; the installed Pillow license bundle
and wheel provenance identify its actual native components.

The current runtime uses CPython 3.13.11 from python-build-standalone's 20260114
Apple silicon build. Its full license bundle, including native dependencies, is
in `licenses/macOS-Python/`. `PYTHON.json` in the release source package identifies
the linked native components. Python's executable was matched to the official
stripped release; uv's installation-path change to libpython's library ID is
recorded separately from the unchanged executable code.

The USB runtime contains libusbmuxd 2.1.1 (including iproxy), libimobiledevice
1.4.0, libimobiledevice-glue 1.3.2, libplist 2.7.0 and OpenSSL 3.6.2. Original
notices are in `licenses/macOS-USB/`. iproxy grants GPL-2.0-or-later; the USB
libraries and selected libimobiledevice tools grant LGPL-2.1-or-later. The release
uses their later-version permission to distribute iproxy under GPLv3 and the
libraries/tools under LGPLv3, compatible with Apache-2.0 OpenSSL. Full GPLv3 and
LGPLv3 texts accompany the retained original notices.

The release source package includes those exact upstream sources, Homebrew build
formulas/receipts, every Python sdist in `uv.lock`, CPython source and its build
recipes/native dependency sources. Homebrew recipe notices are in
`licenses/Homebrew/`. Native libraries remain separate files that users can
replace with compatible modified builds; replacement instructions explain local
macOS signing requirements.

`scripts/bundle-runtime` records loader-path changes, including libpython's library
ID and removal of an inert Pillow JPEG CI RPATH. Python sysconfig prefixes resolve
from the relocated interpreter. Python resides under
`Contents/Resources/runtime/python`; macOS Mach-O files are signed and verified
individually when an authorized app build runs. The phone daemon's signature and
entitlements are preserved. The bundle is an aggregation under these licenses.

## Rebuild and release records

`scripts/setup` builds local source and never installs or launches anything on a
phone. `scripts/setup --check` verifies the artifact and source/recipe pins.
Theos `16362d3aa83a0acd56df4493d575d34306d42478`, iPhoneOS16.5 SDK, Xcode and
Python are prerequisites. Setup does not upgrade them; `THEOS` selects an existing
checkout. The complete phone source is in the project, requiring no VNC source fetch.

The staged payload is `work/device-release`; corresponding source is
`work/release-sources/device`. `SHA256SUMS.json` inventories source files. Source
archives accompany the app in the same release with equivalent download access,
providing the GPLv2 section 3(a) source distribution.

`work/build-manifest.json` records source identities and inventory, final binary
hash, build recipe, helper and observed toolchain. The staged manifest omits local
paths. These records identify inputs and outputs; they do not promise identical
binary output across different compilers/SDKs. See [the release checklist](docs/RELEASING.md).
