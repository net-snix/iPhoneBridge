"""Safety properties with a fake RFB client; real-phone evidence is separate."""
import asyncio
import io
from pathlib import Path
from struct import pack
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from iphonebridge import control, mcp_server


class FakeClient:
    def __init__(self):
        self.timeout = 5
        self.size = (1170, 2532)
        self.events = []
        self.fail = None
        self.capture_count = 0
        self.probe_count = 0

    def probeSize(self):
        self.probe_count += 1
        self.width, self.height = self.size
        return self

    def captureScreen(self, destination, **kwargs):
        self.capture_count += 1
        if self.fail == "after-capture":
            raise TimeoutError("fake framebuffer timeout")
        Image.new("RGB", self.size, "white").save(destination, format="PNG")

    def disconnect(self):
        self.events.append(("disconnect",))

    def __getattr__(self, method):
        def action(*args):
            self.events.append((method, *args))
            if method == self.fail:
                raise TimeoutError("fake input timeout")
        return action


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.client = FakeClient()
        self.patches = [patch.object(control, "WORK", root / "work"),
                        patch.object(control, "SCREENSHOTS", root / "screens"),
                        patch.object(control.api, "connect", return_value=self.client)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_screenshot_dimensions_path_and_unique_files(self):
        first = control.screenshot()
        second = control.screenshot()
        self.assertEqual((first["width"], first["height"]), self.client.size)
        self.assertTrue(Path(first["path"]).is_absolute())
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.events, [("disconnect",), ("disconnect",)])

    def test_stale_orientation_rejects_all_input(self):
        with self.assertRaisesRegex(ValueError, "Framebuffer changed"):
            control.tap(100, 100, 2532, 1170)
        self.assertEqual(self.client.events, [("disconnect",)])
        self.assertEqual((self.client.probe_count, self.client.capture_count), (1, 0),
                         "size check must not transfer a full frame")

    def test_invalid_coordinates_reject_before_connection(self):
        for coordinates in [(-1, 4), (1170, 4), (True, 4), (4.5, 4)]:
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                control.tap(*coordinates, 1170, 2532)
        control.api.connect.assert_not_called()

    def test_tap_releases_button_even_when_press_fails(self):
        self.client.fail = "mouseDown"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.tap(10, 20, 1170, 2532)
        self.assertEqual(self.client.events[-2:], [("mouseUp", 1), ("disconnect",)])

    def test_drag_exact_endpoint_and_release(self):
        result = control.drag(10, 20, 100, 200, 1170, 2532, 0.1)
        self.assertEqual(result["action"], "drag")
        self.assertEqual(self.client.events[-3:], [("mouseMove", 100, 200),
                                                  ("mouseUp", 1), ("disconnect",)])
        self.assertEqual((self.client.probe_count, self.client.capture_count), (1, 1))

    def test_duration_bounded(self):
        for duration in [float("nan"), float("inf"), True, 0.01, 5.01]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                control.drag(1, 2, 3, 4, 1170, 2532, duration)
        control.api.connect.assert_not_called()

    def test_typing_metadata_omits_text_and_releases_each_key(self):
        result = control.type_text("Ab-\n", 1170, 2532)
        self.assertNotIn("text", result)
        self.assertEqual([event for event in self.client.events if event[0] == "keyUp"],
                         [("keyUp", "a"), ("keyUp", "shift"), ("keyUp", "b"),
                          ("keyUp", "-"), ("keyUp", "enter")])

    def test_shifted_ascii_matches_us_keyboard_layout(self):
        session = control._Session(self.client)
        shifted = 'AZ~!@#$%^&*()_+{}|:"<>?'
        bases = 'az`1234567890-=[]\\;\',./'
        for char, base in zip(shifted, bases, strict=True):
            with self.subTest(char=char):
                self.client.events.clear()
                control._type_character(session, char)
                self.assertEqual(self.client.events,
                                 [("keyDown", "shift"), ("keyDown", base),
                                  ("keyUp", base), ("keyUp", "shift")])

    def test_unshifted_ascii_does_not_press_modifiers(self):
        session = control._Session(self.client)
        for char in " az09-=[]\\;',./`":
            with self.subTest(char=char):
                self.client.events.clear()
                control._type_character(session, char)
                self.assertEqual(self.client.events, [("keyDown", char), ("keyUp", char)])

    def test_shift_released_when_base_release_fails(self):
        self.client.fail = "keyUp"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.type_text("A", 1170, 2532)
        self.assertEqual(self.client.events[-3:],
                         [("keyUp", "a"), ("keyUp", "shift"), ("disconnect",)])

    def test_shift_released_when_shift_press_times_out(self):
        self.client.fail = "keyDown"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.type_text("!", 1170, 2532)
        self.assertEqual(self.client.events,
                         [("keyDown", "shift"), ("keyUp", "shift"), ("disconnect",)])

    def test_unsupported_text_rejected_before_input(self):
        for text in ["", "ø", "hello\x00", "x" * 257]:
            with self.subTest(text_length=len(text)), self.assertRaises(ValueError):
                control.type_text(text, 1170, 2532)
        control.api.connect.assert_not_called()

    def test_key_up_attempted_on_timeout(self):
        self.client.fail = "keyDown"
        with self.assertRaisesRegex(RuntimeError, "may have been applied"):
            control.key("enter", 1170, 2532)
        self.assertEqual(self.client.events[-2:], [("keyUp", "enter"), ("disconnect",)])

    def test_post_action_capture_failure_warns_against_retry(self):
        self.client.fail = "after-capture"
        with self.assertRaisesRegex(RuntimeError, "take a screenshot before retrying"):
            control.tap(10, 20, 1170, 2532)
        self.assertIn(("mouseUp", 1), self.client.events)

    def test_size_probe_requests_one_pixel_and_resets_screen(self):
        client = control._Client()
        client.transport = Mock()
        client.width, client.height = 2532, 1170
        client.screen = Image.new("RGB", (1, 1))
        results = []
        client.probeSize().addCallback(results.append)
        client.transport.write.assert_called_once_with(pack("!BBHHHH", 3, 0, 0, 0, 1, 1))
        self.assertEqual(results, [], "size resolves only after the server answers")
        client.commitUpdate([(0, 0, 1, 1)])
        self.assertEqual(results, [client])
        self.assertIsNone(client.screen, "the following full capture must not grow a 1x1 image")
        self.assertIs(control._Factory.protocol, control._Client)

    def test_threaded_proxy_preserves_protocol_after_size_probe(self):
        factory = control._Factory()
        client = control._Client()
        client.factory = factory
        client.transport = Mock()
        client.width, client.height = 8, 6
        factory.deferred.callback(client)
        proxy = control.api.ThreadedVNCClientProxy(factory, timeout=0.1)
        proxy.protocol = client

        def answer_update(x=0, y=0, width=None, height=None, **kwargs):
            dimensions = (width or client.width, height or client.height)
            client.screen = Image.new("RGB", dimensions, "white")
            client.commitUpdate([(x, y, *dimensions)])

        with patch.object(control.api.reactor, "callFromThread", side_effect=lambda f, *a, **kw: f(*a, **kw)), \
             patch.object(client, "framebufferUpdateRequest", side_effect=answer_update):
            session = control._Session(proxy)
            self.assertEqual(session.size(), (8, 6))
            png, size = session.frame()
            self.assertEqual(size, (8, 6))
            with Image.open(io.BytesIO(png)) as image:
                self.assertEqual(image.getpixel((7, 5)), (255, 255, 255))
            self.assertEqual(session.size(), (8, 6))
            self.assertIs(session.call("mouseMove", 3, 4), client)
            self.assertEqual((client.x, client.y), (3, 4))
            self.assertIs(factory.deferred.result, client)

    def test_session_deadline_prevents_more_input(self):
        session = control._Session(self.client)
        session.deadline = 0
        with self.assertRaises(TimeoutError):
            session.call("mouseDown", 1)
        self.assertEqual(self.client.events, [])

    def test_native_home_uses_menu_button_and_returns_screenshot(self):
        with patch.object(control.time, "sleep") as sleep:
            result = control.navigate("home", 1170, 2532)
        self.assertEqual(self.client.events, [("mouseDown", 3), ("mouseUp", 3), ("disconnect",)])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.5, 0.25])
        self.assertEqual(result["action"], "home")
        self.assertEqual((self.client.probe_count, self.client.capture_count), (1, 1))
        self.assertTrue(Path(result["path"]).is_file())

    def test_native_switcher_uses_two_released_home_presses(self):
        with patch.object(control.time, "sleep") as sleep:
            result = control.navigate("app-switcher", 1170, 2532)
        self.assertEqual(self.client.events, [("mouseDown", 3), ("mouseUp", 3),
                                             ("mouseDown", 3), ("mouseUp", 3), ("disconnect",)])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.15, 0.05, 0.5, 0.25])
        self.assertEqual(result["action"], "app-switcher")
        control.api.connect.assert_called_once()

    def test_navigation_stale_size_rejects_before_menu_press(self):
        with self.assertRaisesRegex(control.BridgeInputError, "Framebuffer changed"):
            control.navigate("app-switcher", 2532, 1170)
        self.assertEqual(self.client.events, [("disconnect",)])

    def test_navigation_menu_release_attempted_after_timeout(self):
        self.client.fail = "mouseDown"
        with self.assertRaisesRegex(control.BridgeActionError, "may have been applied"):
            control.navigate("app-switcher", 1170, 2532)
        self.assertEqual(self.client.events, [("mouseDown", 3), ("mouseUp", 3), ("disconnect",)])

    def test_unknown_navigation_rejected_before_connection(self):
        with self.assertRaises(control.BridgeInputError):
            control.navigate("power", 1170, 2532)
        control.api.connect.assert_not_called()

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

    def test_mcp_exposes_tools_and_native_image(self):
        async def check():
            tools = await mcp_server.server.list_tools()
            self.assertEqual({tool.name for tool in tools},
                             {"screenshot", "tap", "drag", "type_text", "key", "health"})
            result = await mcp_server.server.call_tool("screenshot", {})
            self.assertEqual([block.type for block in result.content], ["text", "image"])
            self.assertEqual(result.content[1].mime_type, "image/png")
            self.assertEqual(result.structured_content["width"], 1170)
        asyncio.run(check())

    def test_mcp_stale_size_error_is_readable_without_input(self):
        async def check():
            result = await mcp_server.server.call_tool("tap", {
                "x": 100, "y": 100, "width": 2532, "height": 1170})
            self.assertTrue(result.is_error)
            self.assertEqual(len(result.content), 1)
            self.assertIn("expected 2532x1170, actual 1170x2532", result.content[0].text)
            self.assertIn("take a new screenshot", result.content[0].text)
            self.assertNotIn("Traceback", result.content[0].text)
        asyncio.run(check())
        self.assertEqual(self.client.events, [("disconnect",)])

    def test_mcp_connection_failures_are_sanitized(self):
        async def check():
            for error in [control.twisted_error.ConnectError(string="private detail"),
                          control.twisted_error.ConnectionLost("private detail"),
                          TimeoutError("private detail"), PermissionError("private detail")]:
                with self.subTest(error=type(error).__name__):
                    with patch.object(control.api, "connect", side_effect=error):
                        result = await mcp_server.server.call_tool("screenshot", {})
                    self.assertTrue(result.is_error)
                    self.assertIn("bridge health", result.content[0].text)
                    self.assertNotIn("private detail", result.content[0].text)
                    self.assertNotIn("Traceback", result.content[0].text)
        asyncio.run(check())

    def test_mcp_programmer_errors_are_not_normalized(self):
        def broken():
            raise RuntimeError("unexpected programmer error")
        with self.assertRaisesRegex(RuntimeError, "unexpected programmer error"):
            asyncio.run(mcp_server._action(broken))

    def test_mutation_programmer_errors_propagate_after_button_release(self):
        with patch.object(self.client, "mouseDown", side_effect=RuntimeError("programmer error")):
            with self.assertRaisesRegex(RuntimeError, "programmer error"):
                control.tap(10, 20, 1170, 2532)
        self.assertEqual(self.client.events[-2:], [("mouseUp", 1), ("disconnect",)])


if __name__ == "__main__":
    unittest.main()
