"""Regression coverage for source patches silently skipped by nested git apply."""
import pathlib
import runpy
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPLY = runpy.run_path(str(ROOT / "scripts/build-device-deps"))["apply_reviewed_patch"]
VERIFY = runpy.run_path(str(ROOT / "scripts/stage-device-release"))["verify_daemon_source"]
PATCH = """diff --git a/src/example.c b/src/example.c
--- a/src/example.c
+++ b/src/example.c
@@ -1 +1 @@
-int loopback = 0;
+int loopback = 1;
"""


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        self.tree = root / "work/vendor"
        (self.tree / "src").mkdir(parents=True)
        self.source = self.tree / "src/example.c"
        self.source.write_text("int loopback = 0;\n")
        self.patch = root / "source.patch"
        self.patch.write_text(PATCH)

    def test_applies_inside_an_enclosing_git_checkout(self):
        APPLY(self.tree, self.patch)
        self.assertEqual(self.source.read_text(), "int loopback = 1;\n")

    def test_already_applied_patch_is_verified_without_reversing(self):
        APPLY(self.tree, self.patch)
        APPLY(self.tree, self.patch)
        self.assertEqual(self.source.read_text(), "int loopback = 1;\n")

    def test_unexpected_source_is_rejected(self):
        self.source.write_text("int loopback = 7;\n")
        with self.assertRaises(RuntimeError):
            APPLY(self.tree, self.patch)
        self.assertEqual(self.source.read_text(), "int loopback = 7;\n")

    def test_release_rejects_source_without_loopback_and_navigation_patch(self):
        (self.tree / "src/trollvncserver.mm").write_text("/* original source */\n")
        with self.assertRaisesRegex(RuntimeError, "Required daemon behavior is absent"):
            VERIFY(self.tree)


if __name__ == "__main__":
    unittest.main()
