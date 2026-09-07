# Project scope

Provide a small, maintainable open-source USB bridge for a physical jailbroken
iPhone: a native Mac mirror, screenshots, single-finger input, keyboard entry,
phone navigation, and an MCP interface. Validate actual phone behavior separately
from builds and simulated tests.

The supported release configuration is documented in [README.md](README.md).
Preserve existing iOS, jailbreak, pairing, active app state, and unrelated tunnels.
Deploy and stop only bridge-owned processes. Existing SSH key access is required;
credentials and device identifiers do not belong in source control. No cloud
relay or public network listener is part of the architecture.

Dependency revisions, patches, complete notices, and corresponding source must
accompany redistributed binaries. Keep the native viewer and agent protocol
independently testable, with explicit coordinate dimensions and safe ownership
checks during cleanup.
