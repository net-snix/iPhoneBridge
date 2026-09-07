import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iphonebridge import deployment, lifecycle, settings, transport
from iphonebridge.runtime import Paths


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = Paths.discover(self.root / "Relocated App.app/Contents/Resources/bridge",
                                    home=self.root / "Different User", environ={})

    def test_relocated_bundle_has_user_owned_state_and_local_tools(self):
        self.assertEqual(self.paths.data, self.root / "Different User/Library/Application Support/iPhoneBridge")
        self.assertEqual(self.paths.device, self.paths.contents / "Resources/device")
        self.assertEqual(self.paths.screenshots, self.paths.data / "screenshots")
        with self.assertRaisesRegex(RuntimeError, "missing its bundled"):
            self.paths.tool("iproxy")
        with self.assertRaisesRegex(RuntimeError, "absolute"):
            Paths.discover(self.root, environ={"IPHONEBRIDGE_DATA_DIR": "relative"})

    def test_ambiguous_device_never_silently_selects_first(self):
        first, second = "a" * 24, "b" * 24
        self.assertEqual(settings.select_device([first]), first)
        self.assertEqual(settings.select_device([first, second], second), second)
        with self.assertRaisesRegex(RuntimeError, "Several iPhones"):
            settings.select_device([first, second])
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            settings.select_device([first], second)

    def test_source_novnc_override_does_not_change_bundled_resources(self):
        source = self.root / "source"
        source.mkdir()
        (source / "dependency-lock.json").write_text(json.dumps({"novnc": {"path": "work/vendor/noVNC"}}))
        paths = Paths.discover(source, environ={})
        with patch.dict("os.environ", {"IPHONEBRIDGE_NOVNC_SOURCE": "custom/noVNC"}):
            self.assertEqual(paths.novnc, source.resolve() / "custom/noVNC")
            self.assertEqual(self.paths.novnc, self.paths.root / "novnc")

    def test_configuration_blocks_active_changes_and_never_copies_keys(self):
        key = self.root / "key with spaces"
        key.write_text("test-key-material")
        settings.configure(udid="a" * 24, identity=str(key), paths=self.paths)
        saved = json.loads((self.paths.data / "config.json").read_text())
        self.assertEqual(saved["identity"], str(key))
        self.assertNotIn("test-key-material", (self.paths.data / "config.json").read_text())
        (self.paths.data / "state.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "Stop the bridge"):
            settings.configure(clear_udid=True, paths=self.paths)

    def test_connection_snapshot_survives_later_configuration_change(self):
        original = {"udid": "a" * 24, "identity": None}
        saved = transport.connection_snapshot(original, original["udid"], self.paths)
        original["udid"] = "b" * 24
        args = transport.ssh_args(saved)
        self.assertIn("HostKeyAlias=iphonebridge-" + "a" * 24, args)
        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertIn(f'UserKnownHostsFile="{self.paths.data / "known_hosts"}"', args)
        self.assertEqual(args[args.index("-p") + 1], "15422")
        self.assertEqual(args[args.index("-F") + 1], "/dev/null")

    def test_unknown_usb_listener_is_never_reused_or_stopped(self):
        with patch.object(lifecycle, "usb_owned", return_value=False), \
             patch.object(lifecycle, "usb_listeners", return_value={123: {"127.0.0.1:15422"}}), \
             patch.object(lifecycle, "spawn") as spawn, patch.object(lifecycle.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "unverified process"):
                lifecycle.ensure_usb({"connection": {"udid": "a" * 24}})
            spawn.assert_not_called()
            kill.assert_not_called()

    def test_device_artifact_tamper_fails_before_deployment(self):
        self.paths.device.mkdir(parents=True)
        binary = self.paths.device / "trollvncserver"
        script = self.paths.device / "device-session.sh"
        binary.write_bytes(b"binary")
        script.write_bytes(b"script")
        manifest = {"schema_version": 1, "binary": {"sha256": deployment.digest(binary)},
                    "script_sha256": deployment.digest(script), "source": {"commit": "a" * 40}}
        (self.paths.device / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(deployment.artifacts(self.paths)[0], binary)
        binary.write_bytes(b"modified")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            deployment.artifacts(self.paths)


if __name__ == "__main__":
    unittest.main()
