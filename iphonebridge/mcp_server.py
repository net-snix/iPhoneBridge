"""Local stdio MCP entry point; each action returns native PNG and metadata."""
from __future__ import annotations

import asyncio
import json
from mcp.server.mcpserver import MCPServer, Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import control

server = MCPServer(
    "iPhoneBridge", version="0.1.0", log_level="WARNING",
    instructions=("Local USB iPhone control. Start the bridge with its CLI first. "
                  "Take a screenshot before input; pass its exact width and height. "
                  "Coordinates are raw framebuffer pixels, not resized display pixels. "
                  "Recheck after every action. Text supports ASCII only; never send secrets. "
                  "Only single-finger input is supported. A returned image is evidence, "
                  "not proof an app accepted input."),
)


def _result(metadata):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(metadata)),
                 Image(path=metadata["path"]).to_image_content()],
        structured_content=metadata,
    )


async def _action(function, *args):
    try:
        return _result(await asyncio.to_thread(function, *args))
    except control.BridgeError as error:
        message = str(error)  # Only bridge-authored messages are safe to echo.
    except control.CONNECTION_ERRORS:
        message = ("Cannot communicate with iPhoneBridge at 127.0.0.1:15901. "
                   "Check bridge health, the USB connection, and permission for local network access.")
    except TimeoutError:
        message = ("The iPhoneBridge connection timed out. Check bridge health and take "
                   "a fresh screenshot before retrying input.")
    except OSError:
        message = ("Cannot access the bridge connection or screenshot files. Check bridge "
                   "health, local network access, and output-directory permissions.")
    # Programmer errors deliberately propagate to the SDK's unexpected-error
    # handling. Expected failures omit raw dependency messages and input values.
    return CallToolResult(is_error=True, content=[TextContent(type="text", text=message)])


@server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False))
async def screenshot() -> CallToolResult:
    """Inspect the physical phone. Returns PNG plus exact width/height for input."""
    return await _action(control.screenshot)


@server.tool()
async def tap(x: int, y: int, width: int, height: int) -> CallToolResult:
    """Tap screenshot pixel x,y; reject changed framebuffer dimensions; return fresh PNG."""
    return await _action(control.tap, x, y, width, height)


@server.tool()
async def drag(x1: int, y1: int, x2: int, y2: int, width: int, height: int,
               duration: float = 0.5) -> CallToolResult:
    """Single-finger drag/swipe in raw screenshot pixels, lasting 0.1–5 seconds."""
    return await _action(control.drag, x1, y1, x2, y2, width, height, duration)


@server.tool()
async def type_text(text: str, width: int, height: int) -> CallToolResult:
    """Type 1–256 ASCII characters into the focused field; return fresh PNG. No secrets."""
    return await _action(control.type_text, text, width, height)


@server.tool()
async def key(name: str, width: int, height: int) -> CallToolResult:
    """Navigation key: enter/tab/escape/backspace/delete/left/right/up/down/home/end/pageup/pagedown."""
    return await _action(control.key, name, width, height)


@server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False))
async def health() -> dict:
    """Check the local VNC endpoint without input or service changes."""
    return await asyncio.to_thread(control.health)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
