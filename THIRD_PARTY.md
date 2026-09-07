# Third-party software and provenance

iPhoneBridge's original Python and shell code is MIT licensed. The TrollVNC
patch is a modification of GPL-2.0-only source and remains under that license.
Third-party software retains its own licenses; this project's MIT license does
not relicense it. Exact retained notices are in `licenses/`, with source URLs
or installed-wheel identities and SHA-256 hashes in `licenses/provenance.json`.

## Runtime components

| Component | Pin | License and retained notices |
| --- | --- | --- |
| [TrollVNC](https://github.com/owngoal-dev/TrollVNC) | `a3e40816ea5b93a7c80c09625175893d15bd1070` (3.2-272) | GPL-2.0-only; `licenses/TrollVNC/COPYING`; original per-file notices remain in source |
| [noVNC](https://github.com/novnc/noVNC/releases/tag/v1.7.0) | `63107bd06d9e1f6136ff21aeda8cd62cbf0d433e` (1.7.0) | Primarily MPL-2.0; BSD HTML/CSS, OFL fonts, CC-BY-SA-3.0 images and MIT pako; `licenses/noVNC/` |
| [vncdotool](https://github.com/sibson/vncdotool) | 1.4.2 | MIT; `licenses/python/vncdotool/` |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 2.2.0 | MIT; `licenses/python/mcp/` |
| [websockify](https://github.com/novnc/websockify) | 0.13.0 | LGPL-3.0; `licenses/python/websockify/` |

noVNC 1.7.0 was the latest stable release shown by the official release page on
2026-09-07. It is served from the complete unmodified official checkout at
`/Users/espenmac/Code/oss/noVNC-iPhoneBridge`, including its source headers,
AUTHORS, documentation licenses and pako notice. TrollVNC's bundled viewer is
not used. Python dependencies and their transitive versions/download hashes
are pinned in `uv.lock`. Retained installed-wheel license files include the
transitive dependencies; their files remain intact in the virtual environment.

## Daemon build boundary

The TrollVNC CLI is compiled locally from the pinned source plus
`patches/trollvnc-loopback.patch`. That patch prevents the upstream IPv4
loopback option from leaving an IPv6 listener on all interfaces. It is plainly
marked in the modified source. Setup checks that the complete tracked source
diff equals that reviewed patch and refuses unrelated source changes.

This is **not a complete source rebuild of every linked dependency**. Upstream
TrollVNC supplies static archives in `lib/`; its Makefile links `crypto`,
`lzo2`, `turbojpeg`, `png18`, `sasl2`, `ssl`, and `vncserver`. Their bytes are
pinned in `dependency-lock.json` and recorded in `work/build-manifest.json`.
Header version declarations provide the following identities, but do not prove
the exact source revision or build recipe of each archive:

| Bundled library | Header declaration | Notices |
| --- | --- | --- |
| LibVNCServer | 0.9.15 | GPL-2.0; `licenses/LibVNCServer/` |
| libjpeg-turbo | 3.1.3 | IJG and BSD-3-Clause; `licenses/libjpeg-turbo/` |
| libpng | 1.8.0.git | PNG Reference Library License; exact bundled header notice in `licenses/libpng/COPYING` |
| LZO | 2.10 | GPL-2.0-or-later; exact bundled header notice and GPLv2 in `licenses/LZO/` |
| OpenSSL | 1.1.1l | OpenSSL and original SSLeay licenses; `licenses/OpenSSL/LICENSE` |
| Cyrus SASL | 2.1.28 | CMU BSD-style license; `licenses/Cyrus-SASL/COPYING` |

The Makefile also links platform zlib and Apple frameworks. The zlib license
reference in `licenses/zlib/LICENSE` is from official v1.3.1; it does not assert
the installed phone's system zlib version. Apple frameworks, Xcode and the SDK
are platform prerequisites, not vendored open-source project code.

Upstream points to [BuildVNCServer](https://github.com/Lessica/BuildVNCServer)
for its dependency build recipes. Exact corresponding source for every supplied
archive has not been reconstructed here. The local daemon is not included in
this repository as a binary release. Public binary redistribution needs a
separate corresponding-source and license compatibility review, including the
older OpenSSL license alongside GPL components. The current setup is for local
use on the owner's phone.

Required acknowledgments:

This software is based in part on the work of the Independent JPEG Group.

This product includes software developed by the OpenSSL Project for use in the
OpenSSL Toolkit (http://www.openssl.org/).

This product includes cryptographic software written by Eric Young
(eay@cryptsoft.com). This product includes software written by Tim Hudson
(tjh@cryptsoft.com).

## Setup records

`scripts/setup` runs `uv sync --locked`, verifies pinned source trees and the
patch, reuses the already reviewed binary when its hash matches, and invokes
only the CLI `make all` target when a rebuild is needed. Existing checkouts are
never reset, switched, cleaned, pulled or overwritten. Missing TrollVNC/noVNC
checkouts are cloned to the exact paths in `dependency-lock.json`.

Theos is an existing prerequisite pinned at
`16362d3aa83a0acd56df4493d575d34306d42478`, with `iPhoneOS16.5.sdk`. Setup
does not upgrade/install Theos, Xcode or the SDK. It does not run packaging,
installation hooks, device commands, background servers or viewers.

`work/build-manifest.json` records source commit/tree/content hash, patch hash,
binary path/hash, bundled archive hashes, viewer commit/tree/path and observed
Xcode/Theos/SDK identity. It is a local provenance record, not a claim of
bit-for-bit reproducibility across different toolchains. `scripts/setup --check`
verifies the existing state without writing or rebuilding.
