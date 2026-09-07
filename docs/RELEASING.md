# Release checklist

1. Update `VERSION`, the native build number in `scripts/build-app`, and
   `CHANGELOG.md`. Review supported macOS/iOS versions and the README.
2. Run the checks in [BUILDING.md](BUILDING.md), then stage a fresh device payload
   with `scripts/stage-device-release` and assemble the app with `scripts/build-app`.
3. Audit all bundled executable/library paths and notices. Collect corresponding
   source for the exact versions in the resulting runtime and device manifests,
   including Python native libraries, all locked Python sdists, USB libraries,
   noVNC, patches, and the complete app project. Refresh source inventories.
4. Test the packaged app after copying it away from the checkout, with a clean
   PATH. Verify connection, actual mirror content, navigation, settings, cleanup,
   and restart. Record the daemon hash and distinguish old performance results.
5. Confirm no credentials, pairing records, device identifiers, local ownership
   state, screenshots of private apps, or developer paths enter source or assets.
6. Commit only the reviewed public files and push the intended public branch.
   Wait for hosted CI to pass. Create a matching version tag.
7. Run `.venv/bin/python scripts/package-release --source-ref PUBLIC_COMMIT`
   after preparing `work/release-sources/{device,macos}`. This verifies their
   inventories, exports the public project source, and ZIPs the app with
   `ditto -c -k --sequesterRsrc --keepParent`. Publish it
   alongside a `corresponding-source.tar.gz` containing `project/`, `device/`,
   and `macos/`, plus a `SHA256SUMS` covering both assets. Never ship a binary
   without the corresponding source required by its components' licenses.
8. Verify the public repository, README artwork/links, release downloads, sizes,
   and checksums. State signing/notarization status explicitly in release notes.

The initial release uses ad-hoc signatures and is not notarized. Adding Developer
ID signing later requires an appropriate identity and notarization credentials;
do not use an Apple Development identity as a substitute for public distribution.
