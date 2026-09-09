"""Reject stale daemon builds before staging mismatched corresponding source."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STAGE = runpy.run_path(str(ROOT / "scripts/stage-device-release"))
VERIFY = STAGE["verify_build_provenance"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


class DeviceStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = self.write("patches/trollvnc-loopback.patch", b"reviewed daemon patch")
        dependency = self.write("patches/trollvnc-source-deps.patch", b"dependency patch")
        recipe = self.write("scripts/build-device-deps", b"build recipe")
        binary = self.write("work/build/TrollVNC/.theos/obj/trollvncserver", b"built daemon")
        self.tree = self.root / "work/build/TrollVNC"
        modified = {}
        for name in ("Makefile", "src/trollvncserver.mm", "src/ScreenCapturer.mm",
                     "src/FrameUpdateRetry.h", "src/ClientSendLocks.h"):
            path = self.write(f"work/build/TrollVNC/{name}", name.encode())
            modified[name] = digest(path.read_bytes())
        self.project = {
            "trollvnc": {"commit": "a" * 40, "tree": "b" * 40,
                         "patch": "patches/trollvnc-loopback.patch",
                         "patch_sha256": digest(self.patch.read_bytes()),
                         "patched_files_sha256": modified},
            "theos": {"commit": "c" * 40, "sdk": "iPhoneOS16.5.sdk"},
        }
        self.source_lock = {"sources": {"libvncserver": {"commit": "d" * 40}}}
        spec = self.project["trollvnc"]
        self.build = {
            "source": {"path": str(self.tree), "commit": spec["commit"], "tree": spec["tree"],
                       "patch_sha256": spec["patch_sha256"],
                       "dependency_patch_sha256": digest(dependency.read_bytes())},
            "binary": {"path": str(binary), "sha256": digest(binary.read_bytes())},
            "device_sources": copy.deepcopy(self.source_lock),
            "build_recipe_sha256": digest(recipe.read_bytes()),
            "toolchain": {"theos_commit": self.project["theos"]["commit"],
                          "sdk_path": "/toolchain/sdks/iPhoneOS16.5.sdk"},
        }

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def verify(self, build=None):
        return VERIFY(self.build if build is None else build, self.project, self.source_lock, self.root)

    def test_matching_build_is_accepted(self):
        self.verify()

    def test_missing_or_stale_source_metadata_is_rejected(self):
        for field in ("commit", "tree", "patch_sha256"):
            for value in (None, "stale"):
                with self.subTest(field=field, value=value):
                    build = copy.deepcopy(self.build)
                    if value is None:
                        del build["source"][field]
                    else:
                        build["source"][field] = value
                    with self.assertRaisesRegex(RuntimeError, f"source metadata.*{field}"):
                        self.verify(build)

    def test_new_patch_and_pin_require_a_new_build(self):
        self.patch.write_bytes(b"new reviewed daemon patch")
        self.project["trollvnc"]["patch_sha256"] = digest(self.patch.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "source metadata.*patch_sha256"):
            self.verify()

    def test_unreviewed_patch_bytes_are_rejected(self):
        self.patch.write_bytes(b"unreviewed patch change")
        with self.assertRaisesRegex(RuntimeError, "patch bytes differ"):
            self.verify()

    def test_changed_dependency_patch_is_rejected(self):
        self.write("patches/trollvnc-source-deps.patch", b"different dependency patch")
        with self.assertRaisesRegex(RuntimeError, "dependency patch differs"):
            self.verify()

    def test_every_modified_source_file_is_verified(self):
        for name in self.project["trollvnc"]["patched_files_sha256"]:
            with self.subTest(name=name):
                path = self.tree / name
                original = path.read_bytes()
                path.write_bytes(b"edited after compilation")
                with self.assertRaisesRegex(RuntimeError, "Compiled source differs"):
                    self.verify()
                path.write_bytes(original)

    def test_stale_or_missing_toolchain_identity_is_rejected(self):
        for field, value, message in (("theos_commit", "stale", "Theos revision"),
                                      ("sdk_path", "/sdk/iPhoneOS17.0.sdk", "different SDK"),
                                      ("theos_commit", None, "Theos revision"),
                                      ("sdk_path", None, "different SDK")):
            with self.subTest(field=field, value=value):
                build = copy.deepcopy(self.build)
                if value is None:
                    del build["toolchain"][field]
                else:
                    build["toolchain"][field] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify(build)

    def test_binary_library_lock_and_recipe_checks_are_preserved(self):
        for field, value, message in (("binary", {**self.build["binary"], "sha256": "stale"}, "source lock"),
                                      ("device_sources", {}, "source lock"),
                                      ("build_recipe_sha256", "stale", "current recipe")):
            with self.subTest(field=field):
                build = copy.deepcopy(self.build)
                build[field] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify(build)

    def test_staging_rejects_stale_manifest_before_creating_outputs(self):
        self.build["source"]["patch_sha256"] = "stale"
        self.write("work/build-manifest.json", json.dumps(self.build).encode())
        self.write("dependency-lock.json", json.dumps(self.project).encode())
        self.write("device-sources.lock.json", json.dumps(self.source_lock).encode())
        main = STAGE["main"]
        with mock.patch.dict(main.__globals__, {"ROOT": self.root}):
            with self.assertRaisesRegex(RuntimeError, "source metadata.*patch_sha256"):
                main()
        self.assertFalse((self.root / "work/device-release").exists())
        self.assertFalse((self.root / "work/release-sources").exists())


if __name__ == "__main__":
    unittest.main()
