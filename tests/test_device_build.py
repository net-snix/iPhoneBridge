"""Exercise native provenance, offline rebuild validation and setup rebuild gates."""
import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from unittest import mock

from test_device_staging import BUILD, NativeDeviceFixture


class NativeDeviceBuildTests(NativeDeviceFixture):
    def run_builder(self, source_package=None, during_make=None):
        base = source_package or self.root
        builder = runpy.run_path(str(base / "scripts/build-device-deps"))
        main = builder["main"]
        work = self.root / "rebuilt"
        args = [str(base / "scripts/build-device-deps"), "--build-dir", str(work),
                "--theos", str(self.root / "toolchain"), "--jobs", "2"]
        if source_package:
            args += ["--source-package", str(source_package)]

        def output(*command, cwd=None):
            if command[0] == "git":
                return self.project["theos"]["commit"] if Path(command[2]) == self.root / "toolchain" else self.commit
            if command[0] == "nm":
                return "_VTCompressionSessionCreate\n_CVPixelBufferCreate"
            if command[0] == "otool":
                return "daemon:\n\t/System/Library/Frameworks/VideoToolbox.framework/VideoToolbox (compatibility version 1.0.0)"
            if command[0] == "xcodebuild":
                return "Xcode fixture"
            self.fail(f"Unexpected build command: {command}")

        def make(command, cwd=None, check=None):
            self.assertEqual(command[:4], ["make", "-B", "-j2", "all"])
            self.assertTrue(check)
            self.assertEqual(BUILD["source_files"](cwd), BUILD["source_files"](base / "device"))
            binary = Path(cwd) / ".theos/obj/trollvncserver"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"fresh native daemon output")
            if during_make:
                during_make(Path(cwd))

        with mock.patch.object(sys, "argv", args), mock.patch.dict(main.__globals__, {"output": output}), \
                mock.patch.object(subprocess, "run", side_effect=make) as compiler, \
                contextlib.redirect_stdout(io.StringIO()):
            main()
        return work, compiler

    def test_build_records_all_native_inputs_and_platform_only_linkage(self):
        work, compiler = self.run_builder()
        self.assertEqual(compiler.call_count, 1)
        manifest = json.loads((work / "manifest.json").read_text())
        self.assertEqual(manifest["source"]["kind"], "native")
        self.assertEqual(manifest["source"]["files_sha256"], self.build["source"]["files_sha256"])
        self.assertEqual(manifest["device_sources"], self.source_lock)
        self.assertEqual(manifest["static_libraries"], {})
        self.assertNotIn("viewer", manifest)
        self.verify(manifest)

    def test_staged_source_package_rebuilds_offline_with_identical_source_identity(self):
        staged = self.stage()
        work, compiler = self.run_builder(self.root / "source package")
        rebuilt = json.loads((work / "manifest.json").read_text())
        self.assertEqual(compiler.call_count, 1)
        for field in ("kind", "commit", "files_sha256", "tree_sha256"):
            self.assertEqual(rebuilt["source"][field], staged["source"][field])
        for field in ("device_sources", "build_recipe_sha256", "script_sha256"):
            self.assertEqual(rebuilt[field], staged[field])
        self.assertEqual(rebuilt["binary"]["sha256"], BUILD["sha"](Path(rebuilt["binary"]["path"])))

    def test_source_package_rejects_recipe_helper_lock_and_toolchain_tampering(self):
        self.stage()
        base = self.root / "source package"
        changes = {
            "scripts/build-device-deps": lambda data: data + b"\n# modified recipe\n",
            "device-session.sh": lambda data: data + b"changed helper",
            "device-sources.lock.json": lambda data: json.dumps({**json.loads(data), "target": "changed"}).encode(),
            "dependency-lock.json": lambda data: json.dumps({"theos": {**json.loads(data)["theos"], "commit": "stale"}}).encode(),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                path = base / name
                original = path.read_bytes()
                path.write_bytes(change(original))
                with self.assertRaisesRegex(RuntimeError, "recipe, helper, lock or toolchain"):
                    self.run_builder(base)
                self.assertFalse((self.root / "rebuilt").exists())
                path.write_bytes(original)

    def test_source_package_rejects_unrecorded_native_source(self):
        self.stage()
        base = self.root / "source package"
        (base / "device/src/MirrorServer.mm").write_bytes(b"tampered daemon")
        with self.assertRaisesRegex(RuntimeError, "released device manifest"):
            self.run_builder(base)
        self.assertFalse((self.root / "rebuilt").exists())

    def test_source_change_during_compilation_cannot_produce_a_manifest(self):
        def change_source(compiled):
            (compiled / "src/MirrorServer.mm").write_bytes(b"changed during compilation")
        with self.assertRaisesRegex(RuntimeError, "changed during compilation"):
            self.run_builder(during_make=change_source)
        self.assertFalse((self.root / "rebuilt/manifest.json").exists())

    def test_native_inventory_rejects_symlinks_and_includes_hidden_source(self):
        hidden = self.write("device/.build-include", b"hidden build input")
        self.assertIn(hidden.name, BUILD["source_files"](self.root / "device"))
        (self.root / "device/linked-source").symlink_to(self.root / "LICENSE")
        with self.assertRaisesRegex(RuntimeError, "contains a symlink"):
            BUILD["source_files"](self.root / "device")

    def test_setup_check_is_read_only_and_accepts_complete_verified_build(self):
        setup = runpy.run_path(str(self.root / "scripts/setup"))
        prepare = setup["prepare"]
        with mock.patch.dict(prepare.__globals__, {"run": mock.Mock()}) as namespace, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepare(True, self.root), self.build)
            namespace["run"].assert_not_called()

    def test_setup_stale_binary_rebuilds_and_verifies_new_artifact(self):
        Path(self.build["binary"]["path"]).write_bytes(b"tampered binary")
        setup = runpy.run_path(str(self.root / "scripts/setup"))
        prepare = setup["prepare"]
        fresh = copy.deepcopy(self.build)
        fresh["binary"]["sha256"] = BUILD["sha"](Path(fresh["binary"]["path"]))

        def rebuild(*command, **kwargs):
            self.assertEqual(command[:2], (sys.executable, str(self.root / "scripts/build-device-deps")))
            self.write("work/build-manifest.json", json.dumps(fresh).encode())

        runner = mock.Mock(side_effect=rebuild)
        with mock.patch.dict(prepare.__globals__, {"run": runner}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "No verified native source-built daemon"):
                prepare(True, self.root)
            runner.assert_not_called()
            self.assertEqual(prepare(False, self.root), fresh)
        runner.assert_called_once()

    def test_setup_rejects_successful_build_process_without_verified_output(self):
        self.build["source"]["commit"] = "stale"
        self.save_inputs()
        setup = runpy.run_path(str(self.root / "scripts/setup"))
        prepare = setup["prepare"]
        runner = mock.Mock()
        with mock.patch.dict(prepare.__globals__, {"run": runner}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "project HEAD"):
                prepare(False, self.root)
        runner.assert_called_once()
