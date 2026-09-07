# App icon

Original artwork generated for iPhoneBridge with OpenAI image generation on
2026-09-07. No Apple logo or third-party app artwork was used. The artwork is
covered by this repository's MIT license.

Final design prompt: a polished macOS app icon for a USB iPhone mirroring app,
with a silver phone in front of a small desktop display, joined by a bright cyan
U-shaped USB cable on a deep blue background; clean, dimensional, legible at small
sizes, no text or logos. The final edit made the source a full-bleed square so
Apple's icon tools could apply the actual macOS mask and transparent padding.

Files:

- `icon-source.png`: generated full-bleed master.
- `AppIcon.icon/`: editable Icon Composer source, including 1024 px artwork.
- `icon.png`: masked and padded 1024 px preview used by the README.
- `AppIcon.icns`: app bundle icon at all standard macOS sizes.

`./scripts/build-icon` regenerates the preview and ICNS with Xcode's `ictool`,
`sips`, and `iconutil`. It does not call an image generator or need an API key.
