"""Navigation CLI parsing/dispatch without contacting the phone."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from iphonebridge import __main__ as cli, control


class NavigationCLITests(unittest.TestCase):
    def test_navigation_dispatches_exact_size_and_prints_json(self):
        for name in ("home", "app-switcher"):
            with self.subTest(name=name):
                result = {"action": name, "path": "/tmp/test.png", "width": 1170, "height": 2532}
                output = io.StringIO()
                with patch("sys.argv", ["bridge", "navigate", name, "--size", "1170", "2532"]), \
                     patch.object(control, "navigate", return_value=result) as navigate, \
                     redirect_stdout(output):
                    cli.main()
                navigate.assert_called_once_with(name, 1170, 2532)
                self.assertEqual(json.loads(output.getvalue()), result)

    def test_navigation_requires_size_and_known_action_before_dispatch(self):
        for arguments in (["navigate", "home"], ["navigate", "power", "--size", "1170", "2532"]):
            with self.subTest(arguments=arguments), patch("sys.argv", ["bridge", *arguments]), \
                 patch.object(control, "navigate") as navigate, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    cli.main()
                self.assertEqual(exited.exception.code, 2)
                navigate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
