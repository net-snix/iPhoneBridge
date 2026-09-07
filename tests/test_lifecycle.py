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
        self.work = Path(self.temp.name)
        p = patch.object(lifecycle, "PATHS", Paths(lifecycle.ROOT, None, self.work))
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
             patch.object(lifecycle, "owned_process", side_effect=[False, False, False, True, False, False]), \
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


if __name__ == "__main__":
    unittest.main()
