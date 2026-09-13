"""Native runtime packaging preserves lossless control and dependency notices."""
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = runpy.run_path(str(ROOT / "scripts/bundle-runtime"))


class NativeRuntimePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native bundle with spaces ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.contents = self.root / "Example App.app/Contents"
        self.python = self.contents / "Resources/runtime/python"
        self.version = "python3.13"
        self.packages = self.python / "lib" / self.version / "site-packages"

    def write(self, path, text="fixture"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def distribution(self, name, notices=("LICENSE",)):
        folder = self.packages / f"{name}-1.0.dist-info"
        self.write(folder / "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
        files = []
        for notice in notices:
            path = folder / "licenses" / notice
            self.write(path, f"{name} {notice}")
            files.append(f"{path.relative_to(self.packages).as_posix()},,\n")
        self.write(folder / "RECORD", "".join(files))

    def inventory(self):
        return BUNDLE["python_inventory"](self.python, self.version, self.contents)

    def prepare_runtime(self):
        self.distribution("mcp")
        self.distribution("pillow", ("LICENSE", "third-party-COPYING", "native-NOTICE"))
        self.write(self.python / "lib" / self.version / "LICENSE.txt", "CPython license")

    def test_bridge_assets_exclude_obsolete_browser_video_and_preserve_spaces(self):
        source = self.root / "project source"
        for name in ("iphonebridge/control.py", "iphonebridge/mirror_protocol.py",
                     "fixtures/latency.html", "viewer/main.js", "novnc/core/rfb.js",
                     "iphonebridge/__pycache__/control.pyc"):
            self.write(source / name)
        bridge = self.contents / "Resources/bridge"
        BUNDLE["copy_bridge_assets"](bridge, source)
        actual = {path.relative_to(bridge).as_posix() for path in bridge.rglob("*") if path.is_file()}
        self.assertEqual(actual, {"iphonebridge/control.py", "iphonebridge/mirror_protocol.py",
                                  "fixtures/latency.html"})

    def test_all_pillow_native_notices_and_runtime_notices_are_recorded(self):
        self.prepare_runtime()
        result = self.inventory()
        self.assertEqual(result["distributions"]["pillow"]["version"], "1.0")
        notices = result["distributions"]["pillow"]["notices"]
        self.assertEqual(len(notices), 3)
        for notice in notices + result["runtime_notices"]:
            self.assertEqual(notice["sha256"], BUNDLE["sha"](self.contents / notice["path"]))

    def test_old_device_payload_cannot_enter_native_app(self):
        legacy = {"source": {"commit": "old daemon"}, "static_libraries": {"libvncserver.a": "hash"}}
        with self.assertRaisesRegex(RuntimeError, "requires a verified native daemon"):
            BUNDLE["require_native_payload"](legacy)
        native = {"source": {"kind": "native", "files_sha256": {"src/MirrorServer.mm": "hash"}},
                  "static_libraries": {}}
        BUNDLE["require_native_payload"](native)

    def test_obsolete_dependencies_require_a_clean_locked_sync(self):
        for name in ("vncdotool", "websockify"):
            with self.subTest(name=name):
                self.prepare_runtime()
                self.distribution(name)
                with self.assertRaisesRegex(RuntimeError, f"Obsolete mirror package.*{name}"):
                    self.inventory()
                shutil.rmtree(self.packages / f"{name}-1.0.dist-info")

    def test_pillow_is_required_and_cannot_lose_its_native_notices(self):
        self.distribution("mcp")
        with self.assertRaisesRegex(RuntimeError, "Missing required.*pillow"):
            self.inventory()
        self.distribution("pillow", ())
        with self.assertRaisesRegex(RuntimeError, "Pillow and its bundled codec notices"):
            self.inventory()

    def test_missing_recorded_notice_is_rejected(self):
        self.prepare_runtime()
        (self.packages / "pillow-1.0.dist-info/licenses/LICENSE").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing or external Python notice"):
            self.inventory()

    def test_external_license_symlink_is_rejected(self):
        self.prepare_runtime()
        notice = self.packages / "pillow-1.0.dist-info/licenses/LICENSE"
        notice.unlink()
        outside = self.root / "outside-license"
        self.write(outside)
        notice.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "Missing or external Python notice"):
            self.inventory()

    def test_bundle_audit_rejects_external_symlinks(self):
        self.contents.mkdir(parents=True)
        (self.contents / "external").symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, "External symlink"):
            BUNDLE["audit_bundle"](self.contents)


if __name__ == "__main__":
    unittest.main()
