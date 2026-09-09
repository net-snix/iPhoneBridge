"""Ensure builds apply only the pinned viewer patch, including new modules."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = runpy.run_path(str(ROOT / "scripts/prepare-viewer"))["prepare"]


class ViewerSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-q")
        (self.source / "display.js").write_text("export const revision = 1;\n")
        self.git("add", "display.js")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        (self.source / "display.js").write_text("export const revision = 2;\n")
        (self.source / "image.js").write_text("export const release = () => {};\n")
        self.git("add", "-N", "image.js")
        patch = self.root / "viewer.patch"
        patch.write_bytes(self.git("diff", "--binary", "HEAD"))
        self.spec = {
            "commit": self.git("rev-parse", "HEAD").decode().strip(),
            "tree": self.git("rev-parse", "HEAD^{tree}").decode().strip(),
            "patch": "viewer.patch", "patch_sha256": self.digest(patch),
            "patched_files_sha256": {name: self.digest(self.source / name)
                                     for name in ("display.js", "image.js")},
        }
        # Return this task-owned synthetic repository to its original source.
        self.git("reset", "--", "image.js")
        (self.source / "image.js").unlink()
        (self.source / "display.js").write_text("export const revision = 1;\n")

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.source), *arguments], stderr=subprocess.PIPE)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_check_is_read_only_and_requires_prepared_source(self):
        with self.assertRaisesRegex(RuntimeError, "not prepared"):
            PREPARE(self.source, self.spec, self.root)
        self.assertEqual((self.source / "display.js").read_text(), "export const revision = 1;\n")
        self.assertFalse((self.source / "image.js").exists())

    def test_applies_and_rechecks_exact_patch_with_new_module(self):
        PREPARE(self.source, self.spec, self.root, check_only=False)
        PREPARE(self.source, self.spec, self.root)
        self.assertEqual((self.source / "display.js").read_text(), "export const revision = 2;\n")
        self.assertTrue((self.source / "image.js").exists())

    def test_rejects_unrelated_changes_without_overwriting(self):
        (self.source / "display.js").write_text("user work\n")
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            PREPARE(self.source, self.spec, self.root, check_only=False)
        self.assertEqual((self.source / "display.js").read_text(), "user work\n")

    def test_rejects_changed_patch_and_source_hash(self):
        with self.assertRaisesRegex(RuntimeError, "patch changed"):
            PREPARE(self.source, {**self.spec, "patch_sha256": "0" * 64}, self.root, False)
        PREPARE(self.source, self.spec, self.root, False)
        broken = {**self.spec, "patched_files_sha256": {"display.js": "0" * 64}}
        with self.assertRaisesRegex(RuntimeError, "reviewed bytes"):
            PREPARE(self.source, broken, self.root)


if __name__ == "__main__":
    unittest.main()
