"""Navigation CLI parsing/dispatch without contacting the phone."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from iphonebridge import __main__ as cli, control


class NavigationCLITests(unittest.TestCase):
    def test_navigation_dispatches_exact_size_and_generation_and_prints_json(self):
        for name in ("home", "app-switcher"):
            with self.subTest(name=name):
                result = {"action": name, "path": "/tmp/test.png", "width": 1170, "height": 2532}
                output = io.StringIO()
                with patch("sys.argv", ["bridge", "navigate", name, "--size", "1170", "2532", "--generation", "7"]), \
                     patch.object(control, "navigate", return_value=result) as navigate, \
                     redirect_stdout(output):
                    cli.main()
                navigate.assert_called_once_with(name, 1170, 2532, generation=7)
                self.assertEqual(json.loads(output.getvalue()), result)

    def test_navigation_requires_size_generation_and_known_action_before_dispatch(self):
        for arguments in (["navigate", "home", "--generation", "1"],
                          ["navigate", "home", "--size", "1170", "2532"],
                          ["navigate", "power", "--size", "1170", "2532", "--generation", "1"]):
            with self.subTest(arguments=arguments), patch("sys.argv", ["bridge", *arguments]), \
                 patch.object(control, "navigate") as navigate, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    cli.main()
                self.assertEqual(exited.exception.code, 2)
                navigate.assert_not_called()

    def test_all_mutations_dispatch_required_generation(self):
        actions = ((["tap", "1", "2"], "tap", (1, 2, 1170, 2532)),
                   (["drag", "1", "2", "3", "4"], "drag", (1, 2, 3, 4, 1170, 2532, 0.5)),
                   (["swipe", "1", "2", "3", "4"], "drag", (1, 2, 3, 4, 1170, 2532, 0.5)),
                   (["type"], "type_text", ("test", 1170, 2532)),
                   (["key", "enter"], "key", ("enter", 1170, 2532)))
        for arguments, method, expected in actions:
            with self.subTest(command=arguments[0]), patch.object(control, method, return_value={}) as action:
                with patch("sys.argv", ["bridge", *arguments, "--size", "1170", "2532"]), \
                     redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as exited:
                        cli.main()
                    self.assertEqual(exited.exception.code, 2)
                    action.assert_not_called()
                with patch("sys.argv", ["bridge", *arguments, "--size", "1170", "2532", "--generation", "7"]), \
                     patch("sys.stdin", io.StringIO("test")), redirect_stdout(io.StringIO()):
                    cli.main()
                action.assert_called_once_with(*expected, generation=7)


if __name__ == "__main__":
    unittest.main()
