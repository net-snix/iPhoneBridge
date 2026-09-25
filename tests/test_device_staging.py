"""Reject stale native builds and preserve complete, reproducible source packages."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STAGE = runpy.run_path(str(ROOT / "scripts/stage-device-release"))
BUILD = runpy.run_path(str(ROOT / "scripts/build-device-deps"))
PACKAGE = runpy.run_path(str(ROOT / "scripts/package-release"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NativeDeviceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native device build ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "fixture"],
                       cwd=self.root, check=True)
        self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        for name in ("build-device-deps", "stage-device-release", "setup"):
            self.write("scripts/" + name, (ROOT / "scripts" / name).read_bytes())
        for name in ("Makefile", "src/MirrorServer.mm", "src/HEVCEncoder.mm", "vendor/COPYING",
                     "README.md", "trollvncserver.entitlements"):
            self.write("device/" + name, (name + " fixture\n").encode())
        for name in ("LICENSE", "THIRD_PARTY.md", "iphonebridge/device-session.sh"):
            self.write(name, (name + " fixture\n").encode())
        self.tree = self.root / "work/build/device"
        shutil.copytree(self.root / "device", self.tree)
        binary = self.write("work/build/device/.theos/obj/trollvncserver", b"built native daemon")
        self.project = {"schema_version": 1, "theos": {"commit": "c" * 40, "sdk": "iPhoneOS16.5.sdk",
                                                        "path": str(self.root / "toolchain")}}
        (self.root / "toolchain/sdks/iPhoneOS16.5.sdk").mkdir(parents=True)
        self.source_lock = {"schema_version": 2, "sources": {}, "retained_files_sha256": {
            "vendor/COPYING": digest((self.root / "device/vendor/COPYING").read_bytes())}}
        files = BUILD["source_files"](self.root / "device")
        self.build = {
            "schema_version": 1,
            "source": {"kind": "native", "path": str(self.tree), "commit": self.commit,
                       "files_sha256": files, "tree_sha256": BUILD["source_tree_sha"](files)},
            "binary": {"path": str(binary), "sha256": digest(binary.read_bytes())},
            "script_sha256": digest((self.root / "iphonebridge/device-session.sh").read_bytes()),
            "static_libraries": {}, "device_sources": copy.deepcopy(self.source_lock),
            "build_recipe_sha256": digest((self.root / "scripts/build-device-deps").read_bytes()),
            "supply_chain": "Native fixture with platform frameworks only",
            "toolchain": {"theos_commit": self.project["theos"]["commit"],
                          "sdk_path": str(self.root / "toolchain/sdks/iPhoneOS16.5.sdk"), "xcode": "Xcode fixture"},
        }
        self.save_inputs()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def save_inputs(self):
        for name, data in (("work/build-manifest.json", self.build), ("dependency-lock.json", self.project),
                           ("device-sources.lock.json", self.source_lock)):
            self.write(name, json.dumps(data).encode())

    def verify(self, build=None):
        return STAGE["verify_build_provenance"](self.build if build is None else build,
                                                 self.project, self.source_lock, self.root)

    def stage(self):
        return STAGE["stage"](self.root / "work/build-manifest.json", self.root / "payload",
                                self.root / "source package", self.root)


class DeviceStagingTests(NativeDeviceFixture):
    def test_matching_native_build_is_accepted(self):
        self.verify()

    def test_legacy_and_missing_source_identity_are_rejected(self):
        for field, value, message in (("kind", "trollvnc", "native device"),
                                     ("commit", "stale", "project HEAD"),
                                     ("files_sha256", {}, "Missing native source inventory"),
                                     ("tree_sha256", "stale", "tree hash")):
            with self.subTest(field=field):
                build = copy.deepcopy(self.build)
                build["source"][field] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify(build)

    def test_every_current_and_compiled_source_file_is_verified(self):
        for prefix, label in (("device", "Current"), ("work/build/device", "Compiled")):
            for name in self.build["source"]["files_sha256"]:
                with self.subTest(prefix=prefix, name=name):
                    path = self.root / prefix / name
                    original = path.read_bytes()
                    path.write_bytes(b"changed after compilation")
                    with self.assertRaisesRegex(RuntimeError, f"{label} native source differs"):
                        self.verify()
                    path.write_bytes(original)

    def test_added_and_removed_source_files_are_rejected(self):
        extra = self.write("device/src/Unrecorded.mm", b"unrecorded source")
        with self.assertRaisesRegex(RuntimeError, "Current native source differs"):
            self.verify()
        extra.unlink()
        (self.tree / "src/MirrorServer.mm").unlink()
        with self.assertRaisesRegex(RuntimeError, "Compiled native source differs"):
            self.verify()

    def test_unpinned_retained_source_is_rejected_even_with_rewritten_manifest(self):
        for tree in (self.root / "device", self.tree):
            (tree / "vendor/COPYING").write_bytes(b"changed retained source")
        self.build["source"]["files_sha256"] = BUILD["source_files"](self.root / "device")
        self.build["source"]["tree_sha256"] = BUILD["source_tree_sha"](self.build["source"]["files_sha256"])
        with self.assertRaisesRegex(RuntimeError, "Retained source bytes differ"):
            self.verify()

    def test_stale_toolchain_binary_lock_recipe_and_helper_are_rejected(self):
        for field, value, message in (("binary", {**self.build["binary"], "sha256": "stale"}, "binary differs"),
                                      ("device_sources", {}, "source lock"),
                                      ("static_libraries", {"libjpeg.a": "legacy"}, "static libraries"),
                                      ("build_recipe_sha256", "stale", "current recipe"),
                                      ("script_sha256", "stale", "session script")):
            with self.subTest(field=field):
                build = copy.deepcopy(self.build)
                build[field] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify(build)
        for field, value, message in (("theos_commit", "stale", "Theos revision"),
                                     ("sdk_path", "/sdk/iPhoneOS17.0.sdk", "different SDK")):
            build = copy.deepcopy(self.build)
            build["toolchain"][field] = value
            with self.assertRaisesRegex(RuntimeError, message):
                self.verify(build)

    def test_stale_manifest_is_rejected_before_outputs_exist(self):
        self.build["source"]["commit"] = "stale"
        self.save_inputs()
        with self.assertRaisesRegex(RuntimeError, "project HEAD"):
            self.stage()
        self.assertFalse((self.root / "payload").exists())
        self.assertFalse((self.root / "source package").exists())

    def test_staging_preserves_complete_native_source_and_relocatable_manifest(self):
        manifest = self.stage()
        source = self.root / "source package"
        self.assertEqual(BUILD["source_files"](source / "device"), self.build["source"]["files_sha256"])
        self.assertNotIn("path", manifest["source"])
        self.assertNotIn("sdk_path", manifest["toolchain"])
        self.assertEqual(manifest, json.loads((source / "build-manifest.json").read_text()))
        self.assertEqual(manifest, json.loads((self.root / "payload/manifest.json").read_text()))
        PACKAGE["verify_source_inventory"](source)
        self.assertFalse((source / "archives").exists())
        self.assertFalse((source / "patches").exists())

    def test_existing_staging_directory_is_preserved(self):
        self.write("payload/keep", b"previous output")
        with self.assertRaisesRegex(RuntimeError, "target already exists"):
            self.stage()
        self.assertEqual((self.root / "payload/keep").read_bytes(), b"previous output")
        self.assertFalse((self.root / "source package").exists())

    def test_nested_output_directories_are_rejected_before_writes(self):
        with self.assertRaisesRegex(RuntimeError, "must be separate"):
            STAGE["stage"](self.root / "work/build-manifest.json", self.root / "output/payload",
                            self.root / "output", self.root)
        self.assertFalse((self.root / "output").exists())

    def test_release_inventory_rejects_changes_and_unrecorded_files(self):
        self.stage()
        source = self.root / "source package"
        original = (source / "device/Makefile").read_bytes()
        (source / "device/Makefile").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "Changed corresponding source"):
            PACKAGE["verify_source_inventory"](source)
        (source / "device/Makefile").write_bytes(original)
        (source / "unrecorded").write_bytes(b"unrecorded")
        with self.assertRaisesRegex(RuntimeError, "inventory differs"):
            PACKAGE["verify_source_inventory"](source)


if __name__ == "__main__":
    unittest.main()
