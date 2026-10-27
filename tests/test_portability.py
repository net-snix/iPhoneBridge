import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iphonebridge import deployment, lifecycle, processes, settings, transport
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

    def test_relocated_fixture_helper_uses_isolated_bundled_entry(self):
        entry = self.paths.root / "iphonebridge/entry.py"
        entry.parent.mkdir(parents=True)
        shutil.copy2(Path(__file__).resolve().parents[1] / "iphonebridge/entry.py", entry)
        command = self.paths.python_module("iphonebridge.fixture_server", "--port", 18423)
        self.assertEqual(command, [sys.executable, "-I", "-B", str(entry),
                                   "--module", "iphonebridge.fixture_server", "--port", "18423"])
        result = subprocess.run(self.paths.python_module("websockify"), cwd=self.root,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported bridge helper module", result.stderr)

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
        moved = transport.connection_snapshot({"udid": "a" * 24, "identity": None, "ssh_port": 15423},
                                              "a" * 24, self.paths)
        self.assertEqual(moved["ssh_port"], 15423)
        args = transport.ssh_args(moved)
        self.assertEqual(args[args.index("-p") + 1], "15423")
        for bad in (None, "15422", 80, 15901):
            with self.subTest(port=bad), self.assertRaisesRegex(RuntimeError, "USB SSH port"):
                transport.ssh_args({**saved, "ssh_port": bad})

    def test_usb_port_is_configurable_and_bounded(self):
        self.assertEqual(settings.ssh_port(settings.load(self.paths)), 15422)
        saved = settings.configure(ssh_port=15423, paths=self.paths)["settings"]
        self.assertEqual(saved["ssh_port"], 15423)
        self.assertEqual(settings.ssh_port(settings.load(self.paths)), 15423)
        for port in (80, 65536, 15901, 15802, "15424"):
            with self.subTest(port=port), self.assertRaisesRegex(RuntimeError, "USB SSH port"):
                settings.configure(ssh_port=port, paths=self.paths)
        self.assertEqual(settings.ssh_port(settings.load(self.paths)), 15423)
        (self.paths.data / "state.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "Stop the bridge"):
            settings.configure(clear_ssh_port=True, paths=self.paths)
        (self.paths.data / "state.json").unlink()
        self.assertIsNone(settings.configure(clear_ssh_port=True, paths=self.paths)["settings"]["ssh_port"])
        self.assertEqual(settings.ssh_port(settings.load(self.paths)), 15422)
        # A config without a chosen port stays readable by builds that predate the setting.
        self.assertNotIn("ssh_port", json.loads((self.paths.data / "config.json").read_text()))

    def test_usb_forward_uses_the_recorded_port(self):
        iproxy = self.paths.contents / "Helpers/usb/bin/iproxy"
        iproxy.parent.mkdir(parents=True)
        iproxy.write_text("#!/bin/sh\n")
        iproxy.chmod(0o755)
        info = {"connection": {"udid": "a" * 24, "ssh_port": 15423}}
        with patch.object(lifecycle, "PATHS", self.paths), \
             patch.object(transport, "identifiers", return_value=["a" * 24]), \
             patch.object(lifecycle, "usb_owned", side_effect=[False, True]), \
             patch.object(lifecycle, "usb_listeners", return_value={}) as listeners, \
             patch.object(lifecycle, "available") as available, patch.object(lifecycle, "spawn") as spawn:
            lifecycle.ensure_usb(info)
        listeners.assert_called_once_with(15423)
        available.assert_called_once_with(15423)
        self.assertEqual(spawn.call_args.args[0],
                         [str(iproxy), "-l", "-s", "127.0.0.1", "-u", "a" * 24, "15423:22"])
        self.assertEqual(lifecycle.usb_port({}), 15422)

    def test_unknown_usb_listener_is_never_reused_or_stopped(self):
        for identity in ("", "Sun Oct 18 00:10:21 2026     /usr/bin/nc -l 15422"):
            with self.subTest(identity=identity), \
                 patch.object(lifecycle, "usb_owned", return_value=False), \
                 patch.object(lifecycle, "usb_listeners", return_value={123: {"127.0.0.1:15422"}}), \
                 patch.object(processes, "process_identity", return_value=identity), \
                 patch.object(processes, "process_cwd", return_value="/tmp"), \
                 patch.object(lifecycle, "spawn") as spawn, patch.object(lifecycle.os, "kill") as kill:
                with self.assertRaisesRegex(RuntimeError, "unverified process"):
                    lifecycle.ensure_usb({"connection": {"udid": "a" * 24}})
                spawn.assert_not_called()
                kill.assert_not_called()

    def test_foreign_bridge_forward_is_named_but_never_stopped(self):
        identity = ("Sun Oct 18 00:10:21 2026     /opt/homebrew/bin/iproxy -l -s 127.0.0.1 -u "
                    + "a" * 24 + " 15422:22")
        cases = (("/tmp/checkout", "the bridge checkout at /tmp/checkout; run ./bridge stop in that checkout"),
                 ("/Applications/iPhoneBridge.app/Contents/Resources/bridge",
                  "the iPhoneBridge app at /Applications/iPhoneBridge.app; quit that app or use its Reconnect"))
        for cwd, expected in cases:
            with self.subTest(cwd=cwd), \
                 patch.object(lifecycle, "usb_owned", return_value=False), \
                 patch.object(lifecycle, "usb_listeners", return_value={41125: {"127.0.0.1:15422"}}), \
                 patch.object(processes, "process_identity", return_value=identity), \
                 patch.object(processes, "process_cwd", return_value=cwd), \
                 patch.object(lifecycle, "spawn") as spawn, patch.object(lifecycle.os, "kill") as kill:
                with self.assertRaises(RuntimeError) as caught:
                    lifecycle.ensure_usb({"connection": {"udid": "a" * 24}})
                message = str(caught.exception)
                for fragment in ("USB port 15422", "USB forward", "pid 41125", "started Sun Oct 18 00:10:21 2026",
                                 expected, "nothing was stopped"):
                    self.assertIn(fragment, message)
                spawn.assert_not_called()
                kill.assert_not_called()

    def test_foreign_forward_on_a_moved_port_is_still_named(self):
        identity = "Tue Oct 27 09:45:02 2026     iproxy -u " + "a" * 24 + " -l -s 127.0.0.1 15423:22"
        with patch.object(lifecycle, "usb_owned", return_value=False), \
             patch.object(lifecycle, "usb_listeners", return_value={3476: {"127.0.0.1:15423"}}) as listeners, \
             patch.object(processes, "process_identity", return_value=identity), \
             patch.object(processes, "process_cwd", return_value="/Users/x/Code/Other"), \
             patch.object(lifecycle, "spawn") as spawn, patch.object(lifecycle.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "USB port 15423 is held by an iPhoneBridge USB forward "
                                                      r"\(pid 3476.*/Users/x/Code/Other.*nothing was stopped"):
                lifecycle.ensure_usb({"connection": {"udid": "a" * 24, "ssh_port": 15423}})
            listeners.assert_called_once_with(15423)
            spawn.assert_not_called()
            kill.assert_not_called()

    def test_occupied_mirror_port_names_the_bridge_that_holds_it(self):
        identity = ("Sun Oct 18 00:10:30 2026     /usr/bin/ssh -F /dev/null -p 15422 -o ExitOnForwardFailure=yes "
                    "-L 127.0.0.1:15901:127.0.0.1:15901 mobile@127.0.0.1 exec "
                    "/var/mobile/Media/iPhoneBridge/device-session.sh run " + "b" * 32 + " iphonebridged")
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            port = held.getsockname()[1]
            with patch.object(lifecycle, "listeners", return_value={777: {f"127.0.0.1:{port}"}}), \
                 patch.object(processes, "process_identity", return_value=identity), \
                 patch.object(processes, "process_cwd", return_value="/x"), \
                 patch.object(lifecycle.os, "kill") as kill:
                with self.assertRaisesRegex(RuntimeError,
                                            "iPhoneBridge device session .*checkout at /x.*nothing was stopped"):
                    lifecycle.available(port)
                kill.assert_not_called()

    def test_source_runs_share_the_user_data_directory(self):
        source = self.root / "source"
        source.mkdir()
        home = self.root / "Different User"
        paths = Paths.discover(source, home=home, environ={})
        self.assertEqual(paths.data, home / "Library/Application Support/iPhoneBridge")
        self.assertEqual(paths.instance, source.resolve())
        self.assertEqual(paths.legacy_data, source.resolve() / "work")
        self.assertEqual(paths.screenshots, source.resolve() / "outputs/screenshots")
        for private in (source / "work", self.root / "private"):
            sandbox = Paths.discover(source, home=home, environ={"IPHONEBRIDGE_DATA_DIR": str(private)})
            self.assertEqual(sandbox.data, private)
            self.assertIsNone(sandbox.legacy_data)  # A sandbox never adopts the checkout's records.
        explicit = Paths.discover(source, home=home, environ={"IPHONEBRIDGE_DATA_DIR": str(paths.data)})
        self.assertEqual(explicit.legacy_data, source.resolve() / "work")
        self.assertEqual(self.paths.instance, (self.root / "Relocated App.app").resolve())
        self.assertIsNone(self.paths.legacy_data)

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
