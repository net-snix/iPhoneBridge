"""Release provenance must bind committed sources to the assembled runtime."""
import copy
import io
import json
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = runpy.run_path(str(ROOT / 'scripts/package-release'))
BUNDLE = runpy.run_path(str(ROOT / 'scripts/bundle-runtime'))


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native release identity ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        self.root.mkdir()
        self.source = Path(self.temporary.name) / 'device source'
        self.macos = Path(self.temporary.name) / 'macos source'
        for name in PACKAGE['PROJECT_FILES']:
            self.write(self.root, name, (name + '\n').encode())
        for name in ('Sources/iPhoneBridge/App.swift', 'iphonebridge/entry.py',
                     'iphonebridge/device-session.sh', 'fixtures/colour.html',
                     'licenses/python/pillow/LICENSE', 'assets/AppIcon.icns',
                     'device/Makefile', 'device/src/MirrorServer.mm', 'device/vendor/COPYING'):
            self.write(self.root, name, (name + '\n').encode())
        for name in ('package-release', 'bundle-runtime', 'build-app', 'build-device-deps'):
            self.write(self.root, 'scripts/' + name, (ROOT / 'scripts' / name).read_bytes())
        self.write(self.root, 'VERSION', b'0.3.0\n')
        self.write(self.root, '.gitignore', b'work/\niPhoneBridge.app/\n')
        self.project = {'schema_version': 1, 'theos': {'commit': 'a' * 40, 'sdk': 'iPhoneOS16.5.sdk'}}
        self.source_lock = {'schema_version': 2, 'retained_files_sha256': {}}
        self.json(self.root, 'dependency-lock.json', self.project)
        self.json(self.root, 'device-sources.lock.json', self.source_lock)
        locked = ['version = 1', '[[package]]\nname = "iphonebridge"\nversion = "0.3.0"']
        for name, version in (('mcp', '2.2.0'), ('pillow', '12.3.0'), ('platform-only', '1.0')):
            filename = f'{name}-{version}.tar.gz'
            archive = self.write(self.macos, 'python-sdists/' + filename, filename.encode())
            locked.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                          f'sdist = {{ url = "https://source.test/{filename}", '
                          f'hash = "sha256:{PACKAGE["sha"](archive)}" }}')
        locked.append('[[package]]\nname = "platform-wheel-only"\nversion = "1.0"')
        self.write(self.root, 'uv.lock', ('\n\n'.join(locked) + '\n').encode())
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'native release fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.files = PACKAGE['committed_files'](self.root, self.commit)
        self.device_fixture()
        self.runtime_fixture()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True)

    def write(self, root, name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def json(self, root, name, value):
        return self.write(root, name, (json.dumps(value, indent=2) + '\n').encode())

    def refresh_inventory(self, rebind=False):
        inventory = {path.relative_to(self.macos).as_posix(): PACKAGE['sha'](path)
                     for path in sorted(self.macos.rglob('*'))
                     if path.is_file() and path.name != 'SHA256SUMS.json'}
        path = self.json(self.macos, 'SHA256SUMS.json', inventory)
        if rebind:
            self.runtime['macos_source_inventory_sha256'] = PACKAGE['sha'](path)

    def device_fixture(self):
        shutil.copytree(self.root / 'device', self.source / 'device')
        for source, target in (('dependency-lock.json', 'dependency-lock.json'),
                               ('device-sources.lock.json', 'device-sources.lock.json'),
                               ('scripts/build-device-deps', 'scripts/build-device-deps'),
                               ('iphonebridge/device-session.sh', 'device-session.sh'),
                               ('LICENSE', 'LICENSE'), ('THIRD_PARTY.md', 'THIRD_PARTY.md')):
            self.write(self.source, target, self.files[source])
        inventory = {name.removeprefix('device/'): PACKAGE['digest'](data)
                     for name, data in self.files.items() if name.startswith('device/')}
        self.device = {
            'source': {'kind': 'native', 'commit': self.commit, 'files_sha256': inventory,
                       'tree_sha256': PACKAGE['digest'](json.dumps(
                           inventory, sort_keys=True, separators=(',', ':')).encode())},
            'device_sources': self.source_lock, 'static_libraries': {},
            'build_recipe_sha256': PACKAGE['digest'](self.files['scripts/build-device-deps']),
            'script_sha256': PACKAGE['digest'](self.files['iphonebridge/device-session.sh']),
            'toolchain': {'theos_commit': 'a' * 40, 'sdk': 'iPhoneOS16.5.sdk'},
        }

    def runtime_fixture(self):
        for name in ('uv.lock', 'scripts/build-app', 'scripts/bundle-runtime',
                     'scripts/package-release', 'THIRD_PARTY.md'):
            self.write(self.macos, name, self.files[name])
        for name, data in self.files.items():
            if name.startswith('licenses/'):
                self.write(self.macos, name, data)
        interpreter = self.write(self.root / 'work', 'python3.13', b'original Python executable')
        origin = {'python_version': '3.13.11', 'build': '20260114',
                  'target': 'aarch64-apple-darwin', 'python_executable_sha256': PACKAGE['sha'](interpreter)}
        self.json(self.macos, 'python-runtime-verification.json', origin)
        metadata = {'python_version': '3.13.11', 'target_triple': 'aarch64-apple-darwin',
                    'build_info': {'core': {'links': [{'name': 'System', 'system': True}]},
                                   'extensions': {'_zlib': [{'links': [{'name': 'z'}]}]}}}
        self.json(self.macos, 'PYTHON.json', metadata)
        self.native = {}
        for name, version, archive, libraries in (
                ('cpython-3.13', '3.13.11', 'Python-3.13.11.tar.xz', []),
                ('zlib', '1.3.1', 'zlib-1.3.1.tar.gz', ['z'])):
            path = self.write(self.macos, 'archives/' + archive, archive.encode())
            self.native[name] = {'version': version, 'url': 'https://source.test/' + archive,
                                 'sha256': PACKAGE['sha'](path), 'library_names': libraries}
        self.json(self.macos, 'python-native-sources.lock.json', self.native)
        recipe = self.macos / 'archives/python-build-standalone-20260114.tar.gz'
        with tarfile.open(recipe, 'w:gz') as archive:
            data = ('DOWNLOADS = ' + repr(self.native) + '\n').encode()
            entry = tarfile.TarInfo('python-build-standalone-20260114/pythonbuild/downloads.py')
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        usb_archive = self.write(self.macos, 'archives/libusbmuxd-2.1.1.tar.bz2', b'USB source')
        usb_hash = PACKAGE['sha'](usb_archive)
        url = 'https://source.test/libusbmuxd-2.1.1.tar.bz2'
        formula = self.write(self.macos, 'formulas/libusbmuxd.rb',
                             f'class Libusbmuxd < Formula\n  url "{url}"\n  sha256 "{usb_hash}"\nend\n'.encode())
        self.usb = [{'name': 'libusbmuxd', 'version': '2.1.1', 'url': url,
                     'sha256': usb_hash, 'archive': usb_archive.name}]
        self.json(self.macos, 'usb-sources.lock.json', self.usb)
        self.refresh_inventory()
        self.runtime = {'schema_version': 2, 'mirror_protocol': 'IPBM/1',
                        **BUNDLE['release_identity'](self.root, self.macos, interpreter),
                        'python_version': '3.13.11', 'python_build': '20260114',
                        'python_packages': {'distributions': {'mcp': {'version': '2.2.0'},
                                                              'pillow': {'version': '12.3.0'}}},
                        'usb': {'libusbmuxd': {'version': '2.1.1', 'source': BUNDLE['formula_source'](formula)}}}

    def verify_device(self):
        PACKAGE['verify_committed_device'](self.commit, self.files, self.device, self.source)

    def verify_runtime(self):
        PACKAGE['verify_committed_runtime'](self.files, self.runtime)
        PACKAGE['verify_runtime_sources'](self.runtime, self.macos, self.files)

    def test_exact_committed_device_and_runtime_sources_pass(self):
        PACKAGE['verify_clean_commit'](self.root, self.commit)
        self.verify_device()
        self.verify_runtime()

    def test_release_exports_and_revalidates_sources_without_running_platform_tools(self):
        app = self.root / 'iPhoneBridge.app'
        binary = self.write(app, 'Contents/Resources/device/trollvncserver', b'non-executable test fixture')
        self.device.update({'schema_version': 1, 'binary': {'sha256': PACKAGE['sha'](binary)}})
        self.runtime['device_sha256'] = self.device['binary']['sha256']
        self.json(app, 'Contents/Resources/device/manifest.json', self.device)
        self.write(app, 'Contents/Resources/device/device-session.sh', self.files['iphonebridge/device-session.sh'])
        self.json(app, 'Contents/Resources/runtime-manifest.json', self.runtime)
        self.write(app, 'Contents/Info.plist', plistlib.dumps({'CFBundleShortVersionString': '0.3.0'}))
        self.json(self.source, 'build-manifest.json', self.device)
        self.json(self.source, 'SHA256SUMS.json', {
            path.relative_to(self.source).as_posix(): PACKAGE['sha'](path)
            for path in self.source.rglob('*') if path.is_file()})
        staging = self.root / 'work/release-sources'
        shutil.copytree(self.source, staging / 'device')
        shutil.copytree(self.macos, staging / 'macos')
        original_run, platform_commands = subprocess.run, []

        def run(arguments, *args, **kwargs):
            if arguments[0] not in ('codesign', 'ditto'):
                return original_run(arguments, *args, **kwargs)
            platform_commands.append(arguments)
            if arguments[0] == 'ditto':
                Path(arguments[-1]).write_bytes(b'fixture app archive')
            return subprocess.CompletedProcess(arguments, 0)

        with mock.patch.dict(PACKAGE['main'].__globals__, {'ROOT': self.root}), \
                mock.patch.object(sys, 'argv', ['package-release', '--source-ref', self.commit]), \
                mock.patch.object(subprocess, 'run', side_effect=run), \
                mock.patch('builtins.print'):
            PACKAGE['main']()
        self.assertEqual(platform_commands[0][:4], ['codesign', '--verify', '--deep', '--strict'])
        self.assertEqual([command[0] for command in platform_commands], ['codesign', 'ditto'])
        exported = self.root / 'work/releases/v0.3.0/iPhoneBridge-0.3.0-source'
        self.assertEqual((exported / 'project/device/src/MirrorServer.mm').read_bytes(),
                         self.files['device/src/MirrorServer.mm'])
        PACKAGE['verify_runtime_sources'](self.runtime, exported / 'macos', self.files)
        self.assertTrue((exported.parent / 'iPhoneBridge-0.3.0-corresponding-source.tar.gz').is_file())

    def test_release_requires_clean_reviewed_head(self):
        for name in ('untracked-source.py', 'Sources/iPhoneBridge/App.swift'):
            with self.subTest(name=name):
                path = self.root / name
                old = path.read_bytes() if path.exists() else None
                path.write_bytes(b'not reviewed')
                with self.assertRaisesRegex(RuntimeError, 'clean HEAD'):
                    PACKAGE['verify_clean_commit'](self.root, self.commit)
                path.unlink() if old is None else path.write_bytes(old)
        with self.assertRaisesRegex(RuntimeError, 'clean HEAD'):
            PACKAGE['verify_clean_commit'](self.root, '0' * 40)

    def test_native_files_must_exist_in_the_recorded_commit(self):
        files = {name: value for name, value in self.files.items() if not name.startswith('device/')}
        with self.assertRaisesRegex(RuntimeError, 'Device source inventory differs'):
            PACKAGE['verify_committed_device'](self.commit, files, self.device, self.source)

    def test_committed_source_changes_cannot_be_hidden_by_a_self_consistent_inventory(self):
        self.device['source']['files_sha256']['src/MirrorServer.mm'] = PACKAGE['digest'](b'new source')
        self.device['source']['tree_sha256'] = PACKAGE['digest'](json.dumps(
            self.device['source']['files_sha256'], sort_keys=True, separators=(',', ':')).encode())
        self.write(self.source, 'device/src/MirrorServer.mm', b'new source')
        with self.assertRaisesRegex(RuntimeError, 'Device source inventory differs'):
            self.verify_device()

    def test_device_build_inputs_match_exact_committed_bytes(self):
        for name in ('dependency-lock.json', 'device-sources.lock.json',
                     'scripts/build-device-deps', 'device-session.sh'):
            path = self.source / name
            old = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(old + b' ')
                with self.assertRaisesRegex(RuntimeError, 'Staged build input differs'):
                    self.verify_device()
                path.write_bytes(old)

    def test_staged_device_inventory_cannot_add_uncommitted_sources(self):
        self.write(self.source, 'device/src/Extra.mm', b'uncommitted source')
        with self.assertRaisesRegex(RuntimeError, 'Staged device source differs'):
            self.verify_device()

    def test_runtime_snapshot_rejects_stale_omitted_or_legacy_source_identity(self):
        for field, value in (('schema_version', 1), ('project_files_sha256', {}),
                             ('project_files_sha256', {**self.runtime['project_files_sha256'],
                                                        'Sources/iPhoneBridge/App.swift': '0' * 64})):
            old = self.runtime[field]
            with self.subTest(field=field):
                self.runtime[field] = value
                with self.assertRaisesRegex(RuntimeError, 'Runtime project source differs'):
                    self.verify_runtime()
                self.runtime[field] = old

    def test_rewriting_mac_source_inventory_does_not_change_sealed_identity(self):
        self.write(self.macos, 'extra-source', b'new source')
        self.refresh_inventory()
        with self.assertRaisesRegex(RuntimeError, 'inventory sealed with the runtime'):
            self.verify_runtime()

    def test_stale_mac_lock_is_rejected_even_with_a_rebound_inventory(self):
        path = self.macos / 'uv.lock'
        path.write_bytes(path.read_bytes().replace(b'12.3.0', b'12.2.0'))
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'Mac source uv.lock differs'):
            self.verify_runtime()

    def test_sdist_hashes_come_from_committed_lock_not_editable_source_inventory(self):
        self.write(self.macos, 'python-sdists/pillow-12.3.0.tar.gz', b'tampered Pillow source')
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'Source archive differs from pinned provenance'):
            self.verify_runtime()

    def test_sources_for_locked_platform_dependencies_are_required(self):
        (self.macos / 'python-sdists/platform-only-1.0.tar.gz').unlink()
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'Missing or unsafe source file.*platform-only'):
            self.verify_runtime()

    def test_bundled_python_distribution_versions_must_match_locked_sources(self):
        self.runtime['python_packages']['distributions']['pillow']['version'] = '12.2.0'
        with self.assertRaisesRegex(RuntimeError, 'Runtime Python distribution differs.*pillow'):
            self.verify_runtime()

    def test_bundled_wheel_only_distribution_cannot_omit_source_provenance(self):
        self.runtime['python_packages']['distributions']['platform-wheel-only'] = {'version': '1.0'}
        with self.assertRaisesRegex(RuntimeError, 'lacks source provenance.*platform-wheel-only'):
            self.verify_runtime()

    def test_cpython_version_build_and_executable_identity_must_match(self):
        path = self.macos / 'python-runtime-verification.json'
        original = json.loads(path.read_text())
        for field in ('python_version', 'build', 'python_executable_sha256'):
            with self.subTest(field=field):
                self.json(self.macos, path.name, {**original, field: 'stale'})
                self.refresh_inventory(rebind=True)
                with self.assertRaisesRegex(RuntimeError, 'CPython source identity differs'):
                    self.verify_runtime()

    def test_native_source_hashes_must_match_upstream_recipe(self):
        native = copy.deepcopy(self.native)
        altered = self.write(self.macos, 'archives/zlib-1.3.1.tar.gz', b'tampered native source')
        native['zlib']['sha256'] = PACKAGE['sha'](altered)
        self.json(self.macos, 'python-native-sources.lock.json', native)
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'native source differs from upstream build recipe.*zlib'):
            self.verify_runtime()

    def test_linked_cpython_native_library_sources_cannot_be_omitted(self):
        native = {key: value for key, value in self.native.items() if key != 'zlib'}
        self.json(self.macos, 'python-native-sources.lock.json', native)
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'omits linked native libraries'):
            self.verify_runtime()

    def test_redistributed_tcl_tk_are_required_despite_system_link_metadata(self):
        path = self.macos / 'PYTHON.json'
        metadata = json.loads(path.read_text())
        metadata['build_info']['extensions']['_tkinter'] = [{'links': [{'name': 'tcl9.0', 'system': True}]}]
        self.json(self.macos, path.name, metadata)
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'omits bundled Tcl/Tk sources'):
            self.verify_runtime()

    def test_usb_source_version_url_and_hash_must_match_bundled_formula(self):
        for field in ('version', 'url', 'sha256'):
            with self.subTest(field=field):
                self.json(self.macos, 'usb-sources.lock.json', [{**self.usb[0], field: 'stale'}])
                self.refresh_inventory(rebind=True)
                with self.assertRaisesRegex(RuntimeError, 'USB source identity differs'):
                    self.verify_runtime()

    def test_usb_formula_and_assembly_recipes_cannot_drift(self):
        for name, message in (('formulas/libusbmuxd.rb', 'USB formula differs'),
                              ('scripts/build-app', 'Mac source assembly input differs'),
                              ('scripts/bundle-runtime', 'Mac source assembly input differs'),
                              ('scripts/package-release', 'Mac source assembly input differs')):
            path = self.macos / name
            old = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(old + b'\n# changed\n')
                self.refresh_inventory(rebind=True)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify_runtime()
                path.write_bytes(old)
                self.refresh_inventory(rebind=True)

    def test_notice_bytes_must_match_committed_notices(self):
        self.write(self.macos, 'licenses/python/pillow/LICENSE', b'incomplete notice')
        self.refresh_inventory(rebind=True)
        with self.assertRaisesRegex(RuntimeError, 'Mac source notice differs'):
            self.verify_runtime()


if __name__ == '__main__':
    unittest.main()
