"""Production publication/prepare regressions, with no vendor checkout or phone.

Normal discovery reads the canonical patch; direct execution accepts --patch.
The macOS case exercises real CoreVideo buffers, vImage and production handlers.
These deterministic correctness tests make no daemon throughput claim.
"""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys
import tempfile
import unittest

from daemon_patch_sources import overlap_source, write_headers

HERE = Path(__file__).resolve().parent
DEFAULT_PATCH = HERE.parent / "patches/trollvnc-loopback.patch"
PATCH_OVERRIDE = None


class DaemonPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="iphonebridge-overlap-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.patch = (PATCH_OVERRIDE or DEFAULT_PATCH).read_text()
        write_headers(cls.patch, cls.directory)
        compiler = shutil.which("c++")
        if compiler is None:
            raise RuntimeError("A C++ compiler is required for daemon correctness regressions")
        cls.binaries = {}
        for name in ("daemon_retry_harness", "daemon_client_locks_harness",
                     "daemon_capture_schedule_harness", "daemon_capture_governor_harness"):
            binary = cls.directory / name
            # Match the daemon Makefile's -std=c++20 so these headers are
            # compiled here exactly as they are in the device build.
            command = [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                       "-I", str(cls.directory), str(HERE / (name + ".cpp")), "-o", str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError("C++ regression compile failed:\n" + result.stdout + result.stderr)
            cls.binaries[name] = binary

    def check_binary(self, binary, expected):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)
        self.assertEqual(result.stderr, "")

    def test_completion_coalescing_session_restart_and_failed_prepare(self):
        self.check_binary(self.binaries["daemon_retry_harness"], "4 publication state cases passed")

    def test_snapshot_trylocks_failure_removal_and_teardown(self):
        self.check_binary(self.binaries["daemon_client_locks_harness"], "5 retained snapshot try-lock cases passed")

    def test_capture_cap_jitter_generations_fps_change_and_concurrent_request(self):
        self.check_binary(self.binaries["daemon_capture_schedule_harness"], "4 cap cases passed")

    def test_governor_backoff_recovery_bounds_and_session_reset(self):
        self.check_binary(self.binaries["daemon_capture_governor_harness"], "8 capture governor cases passed")

    @unittest.skipUnless(sys.platform == "darwin", "CoreVideo/vImage production regression requires macOS")
    def test_actual_prepare_publish_dirty_regions_rotation_and_lifecycle(self):
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "Clang is required for the macOS production regression")
        source = self.directory / "overlap.mm"
        source.write_text(overlap_source(self.patch))
        binary = self.directory / "overlap"
        command = [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                   "-Wno-missing-field-initializers", "-pthread", "-fobjc-arc", "-fblocks",
                   "-fsanitize=undefined", "-fno-sanitize-recover=all", "-I", str(self.directory),
                   "-framework", "Foundation", "-framework", "Accelerate",
                   "-framework", "CoreVideo", "-framework", "CoreMedia", str(source), "-o", str(binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.check_binary(binary, "overlap_cases=14")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--patch", type=Path)
    options, remaining = parser.parse_known_args()
    PATCH_OVERRIDE = options.patch
    unittest.main(argv=[sys.argv[0], *remaining])
