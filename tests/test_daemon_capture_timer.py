"""Exercise the patched capture timer with real host Dispatch, without a phone."""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys
import tempfile
import unittest

from daemon_patch_sources import timer_source, write_headers

HERE = Path(__file__).resolve().parent
DEFAULT_PATCH = HERE.parent / "patches/trollvnc-loopback.patch"
PATCH_OVERRIDE = None


@unittest.skipUnless(sys.platform == "darwin", "Real Dispatch timer tests require macOS")
class CaptureTimerTests(unittest.TestCase):
    def test_coalescing_cancellation_generations_and_owner_lifetime(self):
        patch = (PATCH_OVERRIDE or DEFAULT_PATCH).read_text()
        harness = timer_source(patch)
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "Clang is required for the native capture timer regression")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_headers(patch, directory)
            source = directory / "timer.mm"
            source.write_text(harness)
            binary = directory / "timer-tests"
            result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-pthread", "-fobjc-arc", "-fblocks", "-framework", "Foundation",
                            "-I", str(directory), str(source), "-o", str(binary)],
                           capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("8 timer lifecycle cases passed", result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--patch", type=Path)
    options, remaining = parser.parse_known_args()
    PATCH_OVERRIDE = options.patch
    unittest.main(argv=[sys.argv[0], *remaining])
