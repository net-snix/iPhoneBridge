import fcntl
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from iphonebridge import control, lifecycle
from iphonebridge.runtime import Paths


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # A throwaway checkout and data directory: never the real checkout's work/ or the user's records.
        self.root = Path(self.temp.name) / "checkout"
        self.work = Path(self.temp.name) / "data"
        self.root.mkdir()
        p = patch.object(lifecycle, "PATHS", Paths(self.root, None, self.work, self.root / "work"))
        p.start()
        self.addCleanup(p.stop)
        for target in ("iphonebridge.lifecycle.WORK", "iphonebridge.control.WORK"):
            p = patch(target, self.work)
            p.start()
            self.addCleanup(p.stop)
        p = patch("iphonebridge.lifecycle.STATE", self.work / "state.json")
        p.start()
        self.addCleanup(p.stop)

    def save(self, **entries):
        lifecycle.save_state({"schema_version": 2, "token": "a" * 32,
                              "remote_started": False, "connection": {"udid": "a" * 24}, **entries})

    def legacy(self, **entries):
        """A record as bridge code before 0.2.2 left it in the checkout's own work/."""
        legacy = self.root / "work"
        legacy.mkdir(exist_ok=True)
        path = legacy / "state.json"
        path.write_text(json.dumps({"schema_version": 2, "token": "b" * 32, "remote_started": False,
                                    "connection": {"udid": "a" * 24}, **entries}))
        return path

    def test_reused_pid_is_not_owned(self):
        with patch.object(lifecycle, "process_identity", return_value="new process"):
            self.assertFalse(lifecycle.owned_process({"pid": 123, "identity": "old process"}))

    def test_stop_waits_for_inflight_control(self):
        entered = threading.Event()
        completed = threading.Event()
        with patch.object(lifecycle, "_stop_services", side_effect=lambda: entered.set()):
            with control._locked():
                def stop():
                    lifecycle.stop()
                    completed.set()
                worker = threading.Thread(target=stop)
                worker.start()
                self.assertFalse(entered.wait(0.15))
            self.assertTrue(completed.wait(2))
            self.assertTrue(entered.is_set())
            worker.join(2)

    def test_disconnected_usb_preserves_remote_ownership(self):
        entry = {"pid": 123, "identity": "bridge ssh"}
        self.save(ssh=entry, remote_started=True)
        with patch.object(lifecycle, "ensure_usb"), \
             patch.object(lifecycle, "remote", side_effect=subprocess.TimeoutExpired("ssh", 15)), \
             patch.object(lifecycle, "owned_process", side_effect=[True, False]), \
             patch.object(lifecycle.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "reconnect USB"):
                lifecycle.stop()
            kill.assert_called_once_with(123, 15)
        self.assertTrue(lifecycle.STATE.exists())

    def test_cleanup_never_signals_unowned_process(self):
        self.save(ssh={"pid": 123, "identity": "old identity"})
        with patch.object(lifecycle, "remote"), patch.object(lifecycle, "owned_process", return_value=False), \
             patch.object(lifecycle.os, "kill") as kill:
            result = lifecycle.stop()
            kill.assert_not_called()
        self.assertTrue(result["stopped"])
        self.assertFalse(lifecycle.STATE.exists())

    def test_partial_start_state_is_cleanable(self):
        self.save()
        with patch.object(lifecycle, "remote"), patch.object(lifecycle.os, "kill") as kill:
            result = lifecycle.stop()
            kill.assert_not_called()
        self.assertTrue(result["stopped"])

    def test_connected_is_false_when_owned_viewer_resets_http_requests(self):
        self.save()
        with patch.object(lifecycle, "owned_process", return_value=True), \
             patch.object(lifecycle, "usb_owned", return_value=True), \
             patch.object(lifecycle, "handshake", return_value="RFB 003.008"), \
             patch.object(lifecycle.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.side_effect = ConnectionResetError("reset")
            result = lifecycle.status()
            self.assertTrue(result["running"])
            self.assertFalse(result["viewer_ready"])
            self.assertFalse(result["connected"])
            connection.return_value.request.assert_called_once_with(
                "GET", "/", headers={"Connection": "close"})
            connection.return_value.close.assert_called_once()

    def test_connect_recovers_a_session_whose_device_link_ended(self):
        usb = {"pid": 41125, "identity": "bridge usb"}
        self.save(usb=usb, ssh={"pid": 41156, "identity": "bridge ssh"}, remote_started=True,
                  owner={"root": "/tmp/other", "started": "2026-10-18 00:10:21"})
        killed = []
        with patch.object(lifecycle, "owned_process", side_effect=lambda entry: entry == usb and not killed), \
             patch.object(lifecycle, "status", return_value={"connected": False}), \
             patch.object(lifecycle, "ensure_usb"), patch.object(lifecycle, "remote") as remote, \
             patch.object(lifecycle.os, "kill", side_effect=lambda pid, signal: killed.append(pid)), \
             patch.object(lifecycle.deployment, "artifacts", side_effect=RuntimeError("sentinel")):
            with self.assertRaisesRegex(RuntimeError, "sentinel"):
                lifecycle.connect()
        self.assertEqual(killed, [41125])
        remote.assert_called_once()
        self.assertIn("device-session.sh stop " + "a" * 32, remote.call_args.args[0])
        self.assertFalse(lifecycle.STATE.exists())

    def test_connect_refuses_while_the_recorded_device_session_is_live(self):
        cases = (({"root": "/tmp/other", "started": "2026-10-18 00:10:21"}, "started 2026-10-18 00:10:21 from the bridge checkout at /tmp/other"),
                 (None, "recorded by an older bridge instance"))
        for owner, expected in cases:
            with self.subTest(owner=owner):
                record = {"ssh": {"pid": 41156, "identity": "bridge ssh"}}
                if owner:
                    record["owner"] = owner
                self.save(**record)
                with patch.object(lifecycle, "owned_process", return_value=True), \
                     patch.object(lifecycle, "status", return_value={"connected": False}), \
                     patch.object(lifecycle, "stop_unlocked") as stop, patch.object(lifecycle.os, "kill") as kill:
                    with self.assertRaises(RuntimeError) as caught:
                        lifecycle.connect()
                    self.assertIn(expected, str(caught.exception))
                    self.assertIn("Reconnect", str(caught.exception))
                    stop.assert_not_called()
                    kill.assert_not_called()
                self.assertTrue(lifecycle.STATE.exists())

    def test_recovery_failure_keeps_the_record_and_names_the_session(self):
        self.save(owner={"root": "/tmp/other", "started": "yesterday"})
        with patch.object(lifecycle, "owned_process", return_value=False), \
             patch.object(lifecycle, "status", return_value={"connected": False}), \
             patch.object(lifecycle, "stop_unlocked", side_effect=RuntimeError("remote cleanup failed")):
            with self.assertRaisesRegex(RuntimeError, "Recovering .*/tmp/other.*remote cleanup failed.*run stop"):
                lifecycle.connect()
        self.assertTrue(lifecycle.STATE.exists())

    def test_legacy_checkout_record_is_adopted_into_the_shared_directory(self):
        path = self.legacy()
        with patch.object(lifecycle, "PATHS", Paths(self.root, None, self.work)):  # A sandbox never adopts.
            with lifecycle.locked():
                pass
        self.assertTrue(path.exists())
        self.assertFalse(lifecycle.STATE.exists())
        with lifecycle.locked():
            pass
        self.assertFalse(path.exists())
        info = lifecycle.state()
        self.assertEqual(info["token"], "b" * 32)
        self.assertEqual(info["owner"]["root"], str(self.root))
        self.assertTrue(info["owner"]["adopted"])
        self.assertEqual(lifecycle.STATE.stat().st_mode & 0o777, 0o600)

    def test_legacy_adoption_waits_for_older_code_and_rejects_unknown_records(self):
        path = self.legacy()
        with path.with_name("lifecycle.lock").open("a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, "older code is still running"):
                with lifecycle.locked():
                    pass
            fcntl.flock(held, fcntl.LOCK_UN)
        self.assertTrue(path.exists())
        self.assertFalse(lifecycle.STATE.exists())
        path.write_text(json.dumps({"schema_version": 1, "token": "x"}))
        with self.assertRaisesRegex(RuntimeError, "Unrecognized bridge record"):
            with lifecycle.locked():
                pass
        self.assertTrue(path.exists())
        self.assertFalse(lifecycle.STATE.exists())

    def test_both_records_refuse_connect_until_stop_adopts_the_legacy_one(self):
        self.save(owner={"root": "/tmp/other", "started": "yesterday"})
        path = self.legacy()
        with patch.object(lifecycle, "owned_process", return_value=False), \
             patch.object(lifecycle, "status", return_value={"connected": False}):
            with self.assertRaisesRegex(RuntimeError, "run stop twice"):
                lifecycle.connect()
        self.assertTrue(path.exists())
        self.assertTrue(lifecycle.STATE.exists())
        with patch.object(lifecycle, "remote"), patch.object(lifecycle.os, "kill") as kill:
            first = lifecycle.stop()
            self.assertEqual(first["remaining_record"], str(path))
            self.assertFalse(lifecycle.STATE.exists())
            self.assertTrue(path.exists())
            second = lifecycle.stop()
            kill.assert_not_called()
        self.assertTrue(second["stopped"])
        self.assertNotIn("remaining_record", second)
        self.assertFalse(path.exists())
        self.assertFalse(lifecycle.STATE.exists())

    def test_own_only_stop_keeps_sessions_started_elsewhere(self):
        ssh = {"pid": 1234, "identity": "bridge ssh"}
        for owner in ({"root": "/tmp/other", "started": "yesterday"}, None):
            with self.subTest(owner=owner):
                self.save(ssh=ssh, **({"owner": owner} if owner else {}))
                with patch.object(lifecycle, "owned_process", return_value=True), \
                     patch.object(lifecycle.os, "kill") as kill:
                    result = lifecycle.stop(own_only=True)
                    kill.assert_not_called()
                self.assertFalse(result["stopped"])
                self.assertIn("was not started by", result["kept"])
                self.assertTrue(lifecycle.STATE.exists())
        self.save(ssh=ssh, owner={"root": str(self.root), "started": "now"})
        with patch.object(lifecycle, "owned_process", side_effect=[True, False]), \
             patch.object(lifecycle.os, "kill") as kill:
            self.assertTrue(lifecycle.stop(own_only=True)["stopped"])
            kill.assert_called_once_with(1234, 15)
        self.assertFalse(lifecycle.STATE.exists())

    def test_stop_ends_every_recorded_process_shape_in_reverse_start_order(self):
        self.save(usb={"pid": 6, "identity": "bridge usb"}, relay={"pid": 5, "identity": "bridge relay"},
                  fixture_server={"pid": 8, "identity": "bridge fixture"},
                  fixture_ssh={"pid": 9, "identity": "bridge fixture usb"},
                  owner={"root": "/tmp/other", "started": "yesterday"})
        killed = []
        with patch.object(lifecycle, "owned_process", side_effect=lambda entry: entry["pid"] not in killed), \
             patch.object(lifecycle.os, "kill", side_effect=lambda pid, signal: killed.append(pid)):
            self.assertTrue(lifecycle.stop()["stopped"])
        self.assertEqual(killed, [9, 8, 5, 6])
        self.assertFalse(lifecycle.STATE.exists())

    def test_recovery_reports_the_instance_holding_the_usb_port(self):
        self.save(remote_started=True, owner={"root": "/tmp/other", "started": "yesterday"})
        held = ("USB port 15422 is held by an iPhoneBridge USB forward (pid 7, started today) run from "
                "the bridge checkout at /tmp/older; run ./bridge stop in that checkout; nothing was stopped")
        with patch.object(lifecycle, "owned_process", return_value=False), \
             patch.object(lifecycle, "status", return_value={"connected": False}), \
             patch.object(lifecycle, "ensure_usb", side_effect=RuntimeError(held)), \
             patch.object(lifecycle, "remote") as remote, patch.object(lifecycle.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "Recovering .*/tmp/other.*checkout at /tmp/older.*reconnect USB"):
                lifecycle.connect()
            remote.assert_not_called()
            kill.assert_not_called()
        self.assertTrue(lifecycle.STATE.exists())

    def test_process_identity_uses_one_locale_for_every_instance(self):
        from iphonebridge import processes
        with patch.object(processes.transport, "run", return_value="Sun Oct 18 00:10:21 2026     /bin/x") as run:
            self.assertEqual(processes.process_identity(41125), "Sun Oct 18 00:10:21 2026     /bin/x")
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(processes.process_identity(0), "")

    def test_status_reports_the_owner_and_a_remaining_legacy_record(self):
        self.save(owner={"root": "/tmp/other", "started": "yesterday"})
        path = self.legacy()
        with patch.object(lifecycle, "owned_process", return_value=False):
            result = lifecycle.status()
        self.assertEqual(result["owner"]["root"], "/tmp/other")
        self.assertEqual(result["legacy_record"], str(path))
        self.assertEqual(result["data_directory"], str(self.work))

    def test_nothing_in_the_real_data_directory_is_touched(self):
        home = Path.home() / "Library/Application Support/iPhoneBridge"
        names = ("state.json", "lifecycle.lock", "control.lock")

        def snapshot():
            return {name: (home / name).stat().st_mtime_ns if (home / name).exists() else None for name in names}

        before = snapshot()
        with lifecycle.locked():
            pass
        with control._locked():
            pass
        with patch.object(lifecycle, "remote"), patch.object(lifecycle.os, "kill"):
            lifecycle.stop()
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
