"""Execute actual production framing, geometry, lease and surface ownership code."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-device-deps'))


class NativeDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('clang++'):
            raise unittest.SkipTest('clang++ unavailable')
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.work.name) / 'native-device-harness'
        subprocess.run(['clang++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-pthread',
                        '-I', str(ROOT / 'device/include'),
                        str(ROOT / 'tests/native_device_harness.cpp'), '-o', str(cls.binary)], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_fragmented_bounds_geometry_leases_and_callback_ownership(self):
        result = subprocess.run([str(self.binary)], check=True, capture_output=True, text=True, timeout=15)
        self.assertIn('native device invariants passed', result.stdout)

    def test_shared_golden_wire_vectors(self):
        for vector in json.loads((ROOT / 'docs/mirror-protocol-vectors.json').read_text()):
            with self.subTest(name=vector['name']):
                result = subprocess.run([str(self.binary), str(vector['type']),
                    str(vector['request_id']), vector['payload_hex']], check=True, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.stdout.strip(), vector['frame_hex'])

    def test_retained_sources_match_upstream_inventory(self):
        lock = json.loads((ROOT / 'device-sources.lock.json').read_text())
        BUILD['verify_retained_sources'](ROOT / 'device', lock)

    def test_source_inventory_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'escape').symlink_to(ROOT / 'device/Makefile')
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                BUILD['source_files'](root)

    def test_source_inventory_detects_added_and_removed_files(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'a.mm').write_text('a')
            before = BUILD['source_files'](root)
            (root / 'b.mm').write_text('b')
            after = BUILD['source_files'](root)
            self.assertNotEqual(BUILD['source_tree_sha'](before), BUILD['source_tree_sha'](after))
            (root / 'b.mm').unlink()
            self.assertEqual(BUILD['source_files'](root), before)

    @unittest.skipUnless(sys.platform == 'darwin', 'Foundation transport requires macOS')
    def test_production_socket_fragmentation_and_slow_peer_cleanup(self):
        binary = Path(self.work.name) / 'native-connection-harness'
        subprocess.run(['clang++', '-std=c++20', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-framework', 'Foundation',
                        '-I', str(ROOT / 'device/include'),
                        str(ROOT / 'device/src/MirrorConnection.mm'),
                        str(ROOT / 'tests/native_connection_harness.mm'), '-o', str(binary)],
                       check=True, timeout=30)
        for mode in ('fragmented', 'bounded', 'paced', 'stopped'):
            with self.subTest(mode=mode):
                subprocess.run([str(binary), mode], check=True, timeout=10)

    @unittest.skipUnless(sys.platform == 'darwin', 'Color metadata validation requires Apple frameworks')
    def test_capture_color_and_missing_or_conflicting_format_tags(self):
        binary = Path(self.work.name) / 'native-color-harness'
        subprocess.run(['clang++', '-std=c++20', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-framework', 'Foundation', '-framework', 'CoreVideo',
                        '-framework', 'CoreMedia', '-framework', 'CoreGraphics',
                        '-I', str(ROOT / 'device/include'),
                        str(ROOT / 'tests/native_color_harness.mm'), '-o', str(binary)],
                       check=True, timeout=30)
        subprocess.run([str(binary)], check=True, timeout=10)

    @unittest.skipUnless(sys.platform == 'darwin', 'Capture policy harness requires Foundation')
    def test_idle_gap_preserves_quietness_and_dependency_recovery(self):
        source = (ROOT / 'device/src/MirrorServer.mm').read_text()
        method = '- (void)captureTick {' + source.split('- (void)captureTick {', 1)[1].split(
            '\n- (void)deliverStill:', 1)[0]
        template = (ROOT / 'tests/native_capture_policy_harness.mm.in').read_text()
        harness = Path(self.work.name) / 'native-capture-policy.mm'
        harness.write_text(template.replace('@@CAPTURE_TICK@@', method))
        binary = harness.with_suffix('')
        subprocess.run(['clang++', '-std=c++20', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-framework', 'Foundation', '-framework', 'CoreVideo',
                        '-I', str(ROOT / 'device/include'), str(harness), '-o', str(binary)],
                       check=True, timeout=30)
        subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
