# Release checklist

1. Update `VERSION`, the native build number in `scripts/build-app`, and
   `CHANGELOG.md`. Review supported macOS/iOS versions and the README.
2. Run the checks in [BUILDING.md](BUILDING.md), including the native live gates
   in [native-hevc-implementation.md](native-hevc-implementation.md). Commit the
   reviewed public files before the final device build and refresh its manifest
   from that commit. Stage a fresh payload and corresponding device source with
   `scripts/stage-device-release`.
3. Prepare `work/release-sources/macos` **before** app assembly. Collect source for
   the exact Python runtime, native libraries (including Tcl/Tk), all locked
   Python sdists, USB libraries and their actual build formulas. Include the
   current lock, assembly scripts, notices and rebuild instructions; refresh
   `SHA256SUMS.json`. Audit paths and version/hash identities. Runtime assembly
   binds this complete source inventory to its manifest. Then run
   `scripts/build-app` after explicit authorization for its macOS ad-hoc signing.
4. Test the packaged app after copying it away from the checkout, with a clean
   PATH. Verify connection, actual mirror content, navigation, settings, cleanup,
   and restart. Record the daemon hash and distinguish old performance results.
5. Confirm no credentials, pairing records, device identifiers, local ownership
   state, screenshots of private apps, or developer paths enter source or assets.
6. Confirm the device source inventory and the project files consumed by runtime
   assembly match the intended public commit. Changes after assembly require a
   fresh manifest/source stage and app build. With explicit publication authorization,
   push the intended public branch.
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
