"""Compile and exercise the exact retry implementation shipped in the patch.

The header is added in full by the patch, so these tests also run in a clean CI
checkout without downloading iOS sources or requiring a phone or private SDK.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DaemonRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        patch = (ROOT / "patches/trollvnc-loopback.patch").read_text()
        for filename in ("FrameUpdateRetry.h", "ClientSendLocks.h"):
            name = f"src/{filename}"
            section = patch.split(f"diff --git a/{name} b/{name}\n", 1)[1].split("diff --git ", 1)[0]
            if "--- /dev/null\n" not in section:
                raise AssertionError("The test must compile the complete production header")
            header = "".join(line[1:] for line in section.splitlines(keepends=True)
                             if line.startswith("+") and not line.startswith("+++"))
            (directory / filename).write_text(header)
        (directory / "rfb").mkdir()
        (directory / "rfb/rfb.h").write_text("""#pragma once
struct rfbClient;
struct rfbScreenInfo;
struct rfbClientIterator;
using rfbClientPtr = rfbClient *;
using rfbScreenInfoPtr = rfbScreenInfo *;
using rfbClientIteratorPtr = rfbClientIterator *;
rfbClientIteratorPtr rfbGetClientIterator(rfbScreenInfoPtr screen);
rfbClientPtr rfbClientIteratorNext(rfbClientIteratorPtr iterator);
void rfbReleaseClientIterator(rfbClientIteratorPtr iterator);
void rfbIncrClientRef(rfbClientPtr client);
void rfbDecrClientRef(rfbClientPtr client);
""")
        cls.binary = directory / "daemon_retry_harness"
        cls.lock_binary = directory / "daemon_client_locks_harness"
        compiler = shutil.which("c++")
        if not compiler:
            raise RuntimeError("A C++ compiler is required for daemon concurrency regressions")
        for binary in (cls.binary, cls.lock_binary):
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pthread",
                            "-I", str(directory), str(ROOT / "tests" / f"{binary.name}.cpp"),
                            "-o", str(binary)], check=True, capture_output=True, text=True, timeout=30)

    def check_case(self, name):
        subprocess.run([str(self.binary), name], check=True, timeout=20)

    def test_final_update_recovers_after_dirty_counter_stops_changing(self):
        self.check_case("final")

    def test_busy_updates_coalesce_to_latest_screen(self):
        self.check_case("latest")

    def test_multiple_clients_and_unlimited_profile(self):
        self.check_case("clients")

    def test_capacity_lost_before_retry_keeps_final_frame_pending(self):
        self.check_case("busy-again")

    def test_capture_and_client_completions_cannot_lose_retry(self):
        self.check_case("race")

    def test_disconnect_releases_retained_client_and_allows_teardown(self):
        subprocess.run([str(self.lock_binary), "disconnect"], check=True, timeout=20)

    def test_membership_changes_do_not_change_unlock_targets(self):
        subprocess.run([str(self.lock_binary), "membership"], check=True, timeout=20)

    def test_scope_exit_releases_closed_client(self):
        subprocess.run([str(self.lock_binary), "scope"], check=True, timeout=20)

    def test_multiple_disconnected_clients_can_finish_cleanup(self):
        subprocess.run([str(self.lock_binary), "concurrent"], check=True, timeout=20)
