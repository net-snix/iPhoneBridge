"""Synthetic endurance qualification checks; no app, socket, or phone access."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MEASURE = runpy.run_path(str(ROOT / "scripts/measure-native-endurance"))


def report(frames=1800, pid=123):
    return {"id": "test", "label": "test", "mode": "animation", "renderer": "native-hevc",
            "processID": pid, "requestedDurationMs": 30000, "receivedFps": 60,
            "displaySubmittedFps": 60, "visibleQualification": True,
            "visibility": {"available": True, "hidden": False, "hadHidden": False},
            "events": [{"sequence": 0x80000000 + i, "decodedAt": 1000 + i * 1000 / 60}
                       for i in range(frames)],
            "displaySubmittedEvents": [{"sequence": 0x80000000 + i, "submittedAt": 1005 + i * 1000 / 60}
                                       for i in range(frames)]}


class NativeEnduranceTests(unittest.TestCase):
    def qualify(self, value):
        return MEASURE["qualify_window"](value, 123, 30000, 55)

    def test_full_visible_motion_window_passes_both_counters(self):
        result = self.qualify(report())
        self.assertTrue(result["passed"], result["failures"])
        self.assertAlmostEqual(result["decodedWholeWindowFps"], 1799 / 30)

    def test_fast_burst_then_frozen_window_fails(self):
        result = self.qualify(report(frames=60))
        self.assertFalse(result["passed"])
        self.assertLess(result["displaySubmittedWholeWindowFps"], 2)

    def test_rejects_wrong_pid_hidden_window_or_nonmotion_fixture(self):
        variants = [report(pid=456), report(), report()]
        variants[1]["visibility"]["hadHidden"] = True
        variants[2]["events"][0]["sequence"] = 1
        for value in variants:
            self.assertFalse(self.qualify(value)["passed"])

    def test_rejects_missing_display_events_and_nonfinite_rates(self):
        value = report()
        value["displaySubmittedEvents"] = []
        self.assertFalse(self.qualify(value)["passed"])
        value = report()
        value["receivedFps"] = float("nan")
        self.assertFalse(self.qualify(value)["passed"])

    def test_memory_detects_late_growth_and_peak_limit(self):
        samples = [{"elapsedSeconds": i, "rssMiB": 200 + i % 10} for i in range(0, 1321, 5)]
        self.assertTrue(MEASURE["memory_summary"](samples, 512, 64)["passed"])
        growing = [{"elapsedSeconds": i, "rssMiB": 200 + i / 10} for i in range(0, 1321, 5)]
        self.assertFalse(MEASURE["memory_summary"](growing, 512, 64)["passed"])
        samples[100]["rssMiB"] = 600
        self.assertFalse(MEASURE["memory_summary"](samples, 512, 64)["passed"])

    def test_health_rejects_disconnect_reset_and_missing_coverage(self):
        healthy = [{"mode": "renderer-health", "processID": 123, "connected": True,
                    "decodedFrames": i * 300, "displaySubmittedFrames": i * 300,
                    "received_at": i * 5, "visibility": {"hidden": False}} for i in range(1, 7)]
        qualify = MEASURE["qualify_health"]
        self.assertTrue(qualify(healthy, 123, 0, 32)["passed"])
        reset = copy.deepcopy(healthy)
        reset[-1]["decodedFrames"] = 1
        self.assertFalse(qualify(reset, 123, 0, 32)["passed"])
        self.assertFalse(qualify(healthy[:1], 123, 0, 32)["passed"])
        healthy[-1]["connected"] = False
        self.assertFalse(qualify(healthy, 123, 0, 32)["passed"])

    def test_log_tail_ignores_history_and_waits_for_complete_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.ndjson"
            path.write_text('{"old":true}\n')
            tail = MEASURE["ReportTail"](path)
            with path.open("a") as stream:
                stream.write('{"new":')
            self.assertEqual(tail.read(), [])
            with path.open("a") as stream:
                stream.write('true}\n')
            self.assertEqual(tail.read(), [{"new": True}])
            path.write_text("")
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                tail.read()

    def test_pid_identity_change_fails_without_signaling_app(self):
        output = mock.Mock(returncode=0, stdout="Sun Sep 13 01:00:00 2026 204800 3.2 /tmp/iPhoneBridge --attach --benchmark --benchmark-visible\n")
        with mock.patch.object(MEASURE["subprocess"], "run", return_value=output):
            first = MEASURE["process_sample"](123)
            self.assertEqual(first["rssMiB"], 200)
            with self.assertRaisesRegex(RuntimeError, "no longer belongs"):
                MEASURE["process_sample"](123, "different process")


if __name__ == "__main__":
    unittest.main()
