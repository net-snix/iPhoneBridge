"""Input ownership, cleanup and lossless orientation against a native fake client."""
import asyncio
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image

from iphonebridge import control, keyboard, mcp_server
from iphonebridge.mirror_protocol import Geometry, ProtocolError, RemoteError


class FakeClient:
    def __init__(self):
        self.timeout = 5
        self.geometry = Geometry(12, 24, 0, 1)
        self.events = []
        self.fail = None
        self.capture_count = 0
        self.probe_count = 0

    def probe(self):
        self.probe_count += 1
        return self.geometry

    def still(self):
        self.capture_count += 1
        if self.fail == "capture":
            raise TimeoutError("fake capture timeout")
        pixels = bytes((0, 0, 255, 255)) * (self.geometry.width * self.geometry.height)
        return self.geometry, 123456, pixels

    def close(self):
        self.events.append(("close",))

    def __getattr__(self, method):
        def action(*args):
            self.events.append((method, *args))
            if method == self.fail:
                raise TimeoutError("fake input timeout")
        return action


class ControlTests(unittest.TestCase):
    mutations = (
        (control.tap, (1, 2, 12, 24)),
        (control.drag, (1, 2, 10, 20, 12, 24, 0.1)),
        (control.type_text, ("test", 12, 24)),
        (control.key, ("enter", 12, 24)),
        (control.navigate, ("home", 12, 24)),
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.client = FakeClient()
        self.patches = [patch.object(control, "WORK", root / "work"),
                        patch.object(control, "SCREENSHOTS", root / "screens"),
                        patch.object(control, "Client", return_value=self.client)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_screenshot_dimensions_path_and_unique_files(self):
        first, second = control.screenshot(), control.screenshot()
        self.assertEqual((first["width"], first["height"]), self.client.geometry.size)
        self.assertTrue(Path(first["path"]).is_absolute())
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.events, [("close",), ("close",)])
        self.assertTrue(first["lossless"])
        self.assertEqual(first["color_space"], "sRGB")
        with Image.open(first["path"]) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(image.info["srgb"], 0)

    def test_stale_orientation_rejects_before_input_or_capture(self):
        with self.assertRaisesRegex(ValueError, "Framebuffer changed"):
            control.tap(1, 1, 24, 12, generation=1)
        self.assertEqual(self.client.events, [("acquire",), ("release",), ("close",)])
        self.assertEqual((self.client.probe_count, self.client.capture_count), (1, 0))

    def test_half_turn_since_screenshot_rejects_all_input_and_releases_lease(self):
        screenshot = control.screenshot()
        self.client.geometry = Geometry(12, 24, 2, 2)
        for operation, arguments in self.mutations:
            with self.subTest(operation=operation.__name__):
                self.client.events.clear()
                captures = self.client.capture_count
                with self.assertRaisesRegex(control.BridgeInputError, "generation changed: expected 1, actual 2"):
                    operation(*arguments, generation=screenshot["generation"])
                self.assertEqual(self.client.events, [("acquire",), ("release",), ("close",)])
                self.assertEqual(self.client.capture_count, captures)

    def test_same_generation_after_screenshot_allows_input(self):
        self.client.geometry = Geometry(12, 24, 2, 7)
        screenshot = control.screenshot()
        self.client.events.clear()
        result = control.tap(1, 2, screenshot["width"], screenshot["height"],
                             generation=screenshot["generation"])
        self.assertEqual(result["generation"], 7)
        self.assertEqual(self.client.events, [("acquire",), ("pointer", 7, 1, 1, 2),
                                             ("pointer", 7, 0, 1, 2), ("release",), ("close",)])

    def test_missing_or_invalid_generation_rejects_all_mutations_before_connection(self):
        for operation, arguments in self.mutations:
            with self.subTest(operation=operation.__name__, generation="missing"):
                with self.assertRaises(TypeError):
                    operation(*arguments)
            for generation in (None, False, True, 0, -1, 0x100000000, 1.0, "1"):
                with self.subTest(operation=operation.__name__, generation=generation):
                    with self.assertRaisesRegex(control.BridgeInputError, "generation must"):
                        operation(*arguments, generation=generation)
        control.Client.assert_not_called()

    def test_invalid_coordinates_reject_before_connection(self):
        for coordinates in [(-1, 4), (12, 4), (True, 4), (4.5, 4)]:
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                control.tap(*coordinates, 12, 24, generation=1)
        control.Client.assert_not_called()

    def test_busy_human_lease_sends_no_input(self):
        with patch.object(self.client, "acquire", side_effect=RemoteError(2)):
            with self.assertRaisesRegex(control.BridgeActionError, "input is in use"):
                control.tap(1, 1, 12, 24, generation=1)
        self.assertEqual(self.client.events, [("close",)])
        self.assertEqual(self.client.probe_count, 0)

    def test_tap_releases_touch_even_when_press_fails(self):
        self.client.fail = "pointer"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.tap(1, 2, 12, 24, generation=1)
        self.assertEqual(self.client.events, [("acquire",), ("pointer", 1, 1, 1, 2),
                                             ("pointer", 1, 0, 1, 2), ("release",), ("close",)])

    def test_drag_exact_endpoint_and_release(self):
        result = control.drag(1, 2, 10, 20, 12, 24, 0.1, generation=1)
        self.assertEqual(result["action"], "drag")
        self.assertEqual(self.client.events[-4:], [("pointer", 1, 2, 10, 20),
                                                  ("pointer", 1, 0, 10, 20), ("release",), ("close",)])
        self.assertEqual((self.client.probe_count, self.client.capture_count), (1, 1))

    def test_duration_bounded(self):
        for duration in [float("nan"), float("inf"), True, 0.01, 5.01]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                control.drag(1, 2, 3, 4, 12, 24, duration, generation=1)
        control.Client.assert_not_called()

    def test_typing_metadata_omits_text_and_releases_each_key(self):
        result = control.type_text("Ab-\n", 12, 24, generation=1)
        self.assertNotIn("text", result)
        self.assertEqual([event[2] for event in self.client.events if event[0] == "key" and not event[3]],
                         [4, 0xE1, 5, 0x2D, 0x28])

    def test_shifted_ascii_matches_us_keyboard_layout(self):
        session = control._Session(self.client)
        shifted = 'AZ~!@#$%^&*()_+{}|:"<>?'
        bases = 'az`1234567890-=[]\\;\',./'
        for char, base in zip(shifted, bases, strict=True):
            with self.subTest(char=char):
                self.client.events.clear()
                control._type_character(session, char)
                usage = keyboard.BASE[base]
                self.assertEqual(self.client.events, [("key", 1, keyboard.SHIFT, True),
                                  ("key", 1, usage, True), ("key", 1, usage, False),
                                  ("key", 1, keyboard.SHIFT, False)])

    def test_all_supported_ascii_has_a_mapping(self):
        for char in ''.join(chr(code) for code in range(32, 127)) + '\n\t':
            usage, shifted = keyboard.character(char)
            self.assertTrue(4 <= usage <= 0x38)
            self.assertIsInstance(shifted, bool)

    def test_key_and_shift_release_when_press_times_out(self):
        self.client.fail = "key"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.type_text("A", 12, 24, generation=1)
        self.assertEqual(self.client.events, [("acquire",), ("key", 1, keyboard.SHIFT, True),
                                             ("key", 1, keyboard.SHIFT, False), ("release",), ("close",)])

    def test_unsupported_text_rejected_before_connection(self):
        for text in ["", "ø", "hello\x00", "x" * 257]:
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                control.type_text(text, 12, 24, generation=1)
        control.Client.assert_not_called()

    def test_key_up_attempted_on_timeout(self):
        self.client.fail = "key"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.key("enter", 12, 24, generation=1)
        self.assertEqual(self.client.events[-3:], [("key", 1, 0x28, False), ("release",), ("close",)])

    def test_post_action_capture_failure_warns_against_retry(self):
        self.client.fail = "capture"
        with self.assertRaisesRegex(RuntimeError, "take a screenshot before retrying"):
            control.tap(1, 2, 12, 24, generation=1)
        self.assertIn(("pointer", 1, 0, 1, 2), self.client.events)
        self.assertEqual(self.client.events[-2:], [("release",), ("close",)])

    def test_generation_remains_pinned_during_action(self):
        def rotate_after_probe():
            self.client.geometry = Geometry(12, 24, 2, 2)
            return Geometry(12, 24, 0, 1)
        with patch.object(self.client, "probe", side_effect=rotate_after_probe), \
             patch.object(self.client, "pointer", side_effect=RemoteError(3)) as pointer:
            with self.assertRaisesRegex(control.BridgeActionError, "take a screenshot"):
                control.tap(1, 2, 12, 24, generation=1)
        self.assertTrue(all(call.args[0] == 1 for call in pointer.call_args_list))
        self.assertEqual(pointer.call_count, 2)
        self.assertEqual(self.client.events, [("acquire",), ("release",), ("close",)])
        self.assertEqual(self.client.capture_count, 0)

    def test_lossless_bgra_rotation_all_four_orientations(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (1, 2, 3), (4, 5, 6), (7, 8, 9)]
        pixels = b''.join(bytes((b, g, r, 255)) for r, g, b in colors)
        expected = [colors, [colors[i] for i in [4, 2, 0, 5, 3, 1]],
                    list(reversed(colors)), [colors[i] for i in [1, 3, 5, 0, 2, 4]]]
        session = control._Session(self.client)
        for turn in range(4):
            geometry = Geometry(2, 3, turn, 1)
            with patch.object(self.client, "still", return_value=(geometry, 123, pixels)):
                png, actual_geometry, _ = session.frame()
                with Image.open(io.BytesIO(png)) as image:
                    self.assertEqual(image.size, geometry.size)
                    self.assertEqual([image.getpixel((x, y)) for y in range(image.height)
                                      for x in range(image.width)], expected[turn])
                self.assertEqual(actual_geometry, geometry)

    def test_session_deadline_prevents_more_input(self):
        session = control._Session(self.client)
        session.deadline = 0
        with self.assertRaises(TimeoutError):
            session.pointer(1, 1, 2)
        self.assertEqual(self.client.events, [])

    def test_native_navigation_uses_dedicated_self_releasing_button(self):
        for name, button in [("home", 1), ("app-switcher", 2)]:
            self.client.events.clear()
            with patch.object(control.time, "sleep"):
                result = control.navigate(name, 12, 24, generation=1)
            self.assertEqual(result["action"], name)
            self.assertEqual(self.client.events, [("acquire",), ("button", 1, button), ("release",), ("close",)])

    def test_unknown_navigation_rejected_before_connection(self):
        with self.assertRaises(control.BridgeInputError):
            control.navigate("power", 12, 24, generation=1)
        control.Client.assert_not_called()

    def test_flock_serializes_threads(self):
        acquired = threading.Event()
        def contender():
            with control._locked():
                acquired.set()
        with control._locked():
            thread = threading.Thread(target=contender)
            thread.start()
            self.assertFalse(acquired.wait(0.1))
        thread.join(timeout=2)
        self.assertTrue(acquired.is_set())

    def test_mcp_exposes_same_tools_and_native_lossless_image(self):
        async def check():
            tools = await mcp_server.server.list_tools()
            self.assertEqual({tool.name for tool in tools},
                             {"screenshot", "tap", "drag", "type_text", "key", "health"})
            result = await mcp_server.server.call_tool("screenshot", {})
            self.assertEqual([block.type for block in result.content], ["text", "image"])
            self.assertEqual(result.content[1].mime_type, "image/png")
            self.assertEqual(result.structured_content["width"], 12)
            self.assertEqual(result.structured_content["generation"], 1)
        asyncio.run(check())

    def test_mcp_all_mutations_require_strict_bounded_generation(self):
        arguments = {
            "tap": {"x": 1, "y": 2},
            "drag": {"x1": 1, "y1": 2, "x2": 10, "y2": 20},
            "type_text": {"text": "test"},
            "key": {"name": "enter"},
        }
        async def check():
            tools = await mcp_server.server.list_tools()
            for tool in tools:
                if tool.name not in arguments:
                    continue
                self.assertIn("generation", tool.input_schema["required"])
                schema = tool.input_schema["properties"]["generation"]
                self.assertEqual((schema["type"], schema["minimum"], schema["maximum"]),
                                 ("integer", 1, 0xFFFFFFFF))
                base = {**arguments[tool.name], "width": 12, "height": 24}
                invalid = [base, *({**base, "generation": value} for value in
                                   (None, True, False, 0, -1, 0x100000000, 1.0, "1"))]
                for parameters in invalid:
                    with self.subTest(tool=tool.name, generation=parameters.get("generation", "missing")):
                        with self.assertRaisesRegex(ToolError, "generation"):
                            await mcp_server.server.call_tool(tool.name, parameters)
        asyncio.run(check())
        control.Client.assert_not_called()

    def test_mcp_half_turn_rejects_with_readable_generation_error(self):
        self.client.geometry = Geometry(12, 24, 2, 2)
        async def check():
            result = await mcp_server.server.call_tool("tap", {
                "x": 1, "y": 2, "width": 12, "height": 24, "generation": 1})
            self.assertTrue(result.is_error)
            self.assertIn("generation changed: expected 1, actual 2", result.content[0].text)
            self.assertIn("take a new screenshot", result.content[0].text)
        asyncio.run(check())
        self.assertEqual(self.client.events, [("acquire",), ("release",), ("close",)])

    def test_mcp_stale_size_error_is_readable_without_input(self):
        async def check():
            result = await mcp_server.server.call_tool("tap", {
                "x": 1, "y": 1, "width": 24, "height": 12, "generation": 1})
            self.assertTrue(result.is_error)
            self.assertIn("expected 24x12, actual 12x24", result.content[0].text)
            self.assertNotIn("Traceback", result.content[0].text)
        asyncio.run(check())
        self.assertEqual(self.client.events, [("acquire",), ("release",), ("close",)])

    def test_mcp_connection_failures_are_sanitized(self):
        async def check():
            for error in [ProtocolError("private detail"), TimeoutError("private detail"), PermissionError("private detail")]:
                with patch.object(control, "Client", side_effect=error):
                    result = await mcp_server.server.call_tool("screenshot", {})
                self.assertTrue(result.is_error)
                self.assertIn("bridge health", result.content[0].text)
                self.assertNotIn("private detail", result.content[0].text)
        asyncio.run(check())

    def test_programmer_errors_propagate_after_releasing_input(self):
        def broken(*args):
            raise RuntimeError("unexpected programmer error")
        with patch.object(self.client, "pointer", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "programmer error"):
                control.tap(1, 2, 12, 24, generation=1)
        self.assertEqual(self.client.events[-2:], [("release",), ("close",)])


if __name__ == "__main__":
    unittest.main()
