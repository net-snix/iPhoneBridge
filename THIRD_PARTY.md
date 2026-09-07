# Third-party software and corresponding source

iPhoneBridge's original Swift, Python and shell code is MIT licensed. Changes to
TrollVNC remain GPL-2.0-only. Every dependency retains its own license; the MIT
license does not relicense third-party code. Complete retained notices are in
`licenses/`, with their source identities and SHA-256 hashes in
`licenses/provenance.json`.

## Device daemon

| Component | Exact source | License / notices |
| --- | --- | --- |
| TrollVNC | `a3e40816ea5b93a7c80c09625175893d15bd1070` | GPL-2.0-only; `licenses/TrollVNC/` |
| LibVNCServer | `42494999e6492aaab9c1db785ecd293ef10b3aed` | GPL-2.0-or-later; `licenses/LibVNCServer/` |
| libjpeg-turbo | 3.1.3 | IJG and BSD-3-Clause; `licenses/libjpeg-turbo/` |
| libpng | 1.6.58 | PNG Reference Library License; `licenses/libpng/` |
| LZO | 2.10 | GPL-2.0-or-later; `licenses/LZO/` |

The combined device daemon is distributed under GPLv2. This software is based
in part on the work of the Independent JPEG Group.

`scripts/build-device-deps` builds every non-platform device library from the
exact source archive hashes in `device-sources.lock.json`. It retains JPEG,
PNG, LZO, zlib, threading and arm64 SIMD support. LibVNCServer is pinned by
commit because the 0.9.15 release lacks the repeater API used by TrollVNC;
its version string alone does not identify the source. Stable libpng 1.6.58
replaces upstream's unidentified 1.8 development archive.

The build disables OpenSSL, GnuTLS, Gcrypt and SASL. The product transports
loopback VNC inside its SSH tunnel. No original TrollVNC prebuilt static
library is used. The build installs matching generated LibVNCServer headers
so that feature-dependent structure layouts match the linked library.
Apple's system zlib and frameworks are platform prerequisites; their SDKs
and binaries are not copied into the device payload. `licenses/zlib/LICENSE`
is an upstream license reference, not an assertion about the phone's zlib version.

`patches/trollvnc-loopback.patch` prevents an IPv6 wildcard listener when the
IPv4 loopback option is selected. It also maps XF86HomePage and XF86TaskPane
key-down events to TrollVNC's self-releasing Home press and double-press helpers.
`patches/trollvnc-source-deps.patch` updates the library link list for the source
build. `patches/libvncserver-darwin-endian.patch` prevents undefined GNU endian
macros in Darwin's header from overriding the correct little-endian target
configuration. All patches are included in the corresponding-source package.
The build verifies patch application outside enclosing Git repositories and
checks effective byte order with the iOS compiler. Release staging explicitly
checks the loopback and navigation behavior in the compiled source.

## Viewer and Python runtime

| Component | Pin | License / notices |
| --- | --- | --- |
| noVNC | 1.7.0, `63107bd06d9e1f6136ff21aeda8cd62cbf0d433e` | MPL-2.0 core, BSD HTML/CSS, OFL fonts, CC-BY-SA-3.0 images, MIT pako; `licenses/noVNC/` |
| vncdotool | 1.4.2 | MIT; `licenses/python/vncdotool/` |
| MCP Python SDK | 2.2.0 | MIT; `licenses/python/mcp/` |
| websockify | 0.13.0 | LGPL-3.0; `licenses/python/websockify/` |

The complete noVNC checkout is distributed as source, with its original notices,
AUTHORS and documentation. Python dependencies and transitive download hashes
are pinned in `uv.lock`. Their installed source, metadata and license files
remain in the bundled environment. Native runtime components retain their
own notices and source records alongside the release source package. Python
components run in a separate host process from the device daemon.

## macOS native runtime

The app includes CPython 3.13.11 from python-build-standalone's 20260114 Apple
Silicon build. Its full license bundle, including native dependencies, is in
`licenses/macOS-Python/`. `PYTHON.json` in the release source package identifies
the linked native components. Python's executable was matched to the official
stripped release; uv's installation-path change to libpython's library ID is
recorded separately from the unchanged executable code.

The USB runtime contains libusbmuxd 2.1.1 (including iproxy), libimobiledevice
1.4.0, libimobiledevice-glue 1.3.2, libplist 2.7.0 and OpenSSL 3.6.2. Original
notices are in `licenses/macOS-USB/`. iproxy grants GPL-2.0-or-later; the USB
libraries and selected libimobiledevice tools grant LGPL-2.1-or-later. This
release uses their later-version permission to distribute iproxy under GPLv3
and the libraries/tools under LGPLv3, compatible with Apache-2.0 OpenSSL.
Full GPLv3 and LGPLv3 texts accompany the retained original notices.

The release source package includes those exact upstream source archives,
Homebrew build formulas/receipts, every Python sdist in `uv.lock`, noVNC source,
CPython source and its build recipes/native dependency sources. Homebrew recipe
notices are in `licenses/Homebrew/`. Native libraries remain separate files
that users can replace with compatible modified builds; source-package rebuild
and replacement instructions explain local macOS signing requirements.

`scripts/bundle-runtime` records copied-library loader-path changes, including
libpython's library ID, and removal of an inert Pillow JPEG CI RPATH. Generated
Python sysconfig prefix entries resolve from the relocated interpreter at runtime.
Python resides under `Contents/Resources/runtime/python`; every copied macOS
Mach-O is signed and verified individually. The device daemon's existing
signature and entitlements are preserved. Original component notices remain
intact. The distributed bundle is an aggregation under these component licenses.

## Rebuild and release records

`scripts/setup` resolves pinned sources and builds the daemon using
`scripts/build-device-deps`. It never installs or launches anything on a phone.
`scripts/setup --check` verifies the existing artifact and source/recipe pins.
Theos revision `16362d3aa83a0acd56df4493d575d34306d42478`, iPhoneOS16.5 SDK,
Xcode, CMake and Python are build prerequisites; setup does not upgrade them.
Fresh checkouts default to `work/vendor/`. `IPHONEBRIDGE_TROLLVNC_SOURCE` and
`IPHONEBRIDGE_NOVNC_SOURCE` can select existing caches; `THEOS` overrides the
default `~/Code/oss/theos` toolchain location.

`scripts/stage-device-release` creates `work/device-release` and
`work/release-sources/device`. The device source directory includes all linked
library source archives, TrollVNC source without upstream prebuilt libraries,
all patches, build and lifecycle scripts, exact pins, retained original notices
and offline rebuild instructions. Its `SHA256SUMS.json` inventories the source
files. Source archives are provided alongside the app in the same public release,
with equivalent download access. They are the source distribution for GPLv2
section 3(a), rather than a promise to supply source later.

`work/build-manifest.json` records the local build's source identities, static
archive and final binary hashes, recipe, and observed toolchain. The staged
manifest omits machine paths. These records identify the build inputs and output;
they do not promise byte-identical output from different compiler/SDK versions.
