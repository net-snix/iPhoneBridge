"""Exercise the patched LibVNCServer C implementation without a vendor checkout.

Normal unittest discovery uses patches/libvncserver-iphonebridge.patch. Direct
execution also accepts --patch PATH. Only unchanged helper excerpts are fixtures;
all modified snapshot and resize function bodies come from the patch's new side.
"""
from pathlib import Path
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
DEFAULT_PATCH = HERE.parent / "patches/libvncserver-iphonebridge.patch"
PATCH_OVERRIDE = None
HELPERS_SHA256 = "610e2a712f86a5735c4a36d51ed68af3a98075c05317a9805606b04c700f3212"
FUNCTION_FILES = {
    "rfbGetClientSnapshot": "src/libvncserver/rfbserver.c",
    "rfbReleaseClientSnapshot": "src/libvncserver/rfbserver.c",
    "rfbNewFramebufferWithFormat": "src/libvncserver/main.c",
    "rfbNewFramebuffer": "src/libvncserver/main.c",
}


def new_side_hunks(patch):
    """Keep hunks separate so missing context cannot create a synthetic body."""
    files = {}
    current_file = None
    current_hunk = None

    def finish_hunk():
        if current_hunk is not None:
            files.setdefault(current_file, []).append("".join(current_hunk))

    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            finish_hunk()
            current_file = current_hunk = None
        elif line.startswith("+++ b/") and current_hunk is None:
            current_file = line[len("+++ b/"):].rstrip("\r\n")
        elif line.startswith("@@ "):
            finish_hunk()
            if current_file is None:
                raise ValueError("Patch hunk has no new-side source filename")
            current_hunk = []
        elif current_hunk is not None and line.startswith((" ", "+")):
            current_hunk.append(line[1:])
    finish_hunk()
    return files


def extract_function(hunks, name):
    signature = re.compile(r"(?m)^(?:static\s+)?(?:void|rfbBool)\s+" + re.escape(name) + r"\s*\(")
    # Braces inside strings and comments are not C scope boundaries.
    tokens = re.compile(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]')
    found = []
    for hunk in hunks:
        for match in signature.finditer(hunk):
            opening = hunk.find("{", match.end())
            if opening < 0 or ";" in hunk[match.end():opening]:
                continue
            depth = 0
            for token in tokens.finditer(hunk, opening):
                value = token.group()
                if value == "{":
                    depth += 1
                elif value == "}":
                    depth -= 1
                    if depth == 0:
                        found.append(hunk[match.start():token.end()])
                        break
    if len(found) != 1:
        raise ValueError(f"Expected one complete {name} definition in patch context; found {len(found)}")
    return found[0]


def helper_section(text, name):
    begin, end = f"/* BEGIN {name} */\n", f"/* END {name} */"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"Invalid pinned helper section: {name}")
    return text.split(begin, 1)[1].split(end, 1)[0]


def generate_source(patch_path):
    hunks = new_side_hunks(patch_path.read_text())
    functions = {name: extract_function(hunks.get(path, []), name)
                 for name, path in FUNCTION_FILES.items()}
    fixture = (HERE / "libvncserver_pinned_helpers.c.inc").read_bytes()
    if hashlib.sha256(fixture).hexdigest() != HELPERS_SHA256:
        raise ValueError("Pinned unchanged LibVNCServer helper fixture differs from reviewed source")
    helpers = fixture.decode()
    replacements = {
        "PRODUCTION_REFS": helper_section(helpers, "REFS"),
        "PRODUCTION_FORMAT": helper_section(helpers, "FORMAT"),
        "PRODUCTION_SNAPSHOT": functions["rfbGetClientSnapshot"] + "\n\n" + functions["rfbReleaseClientSnapshot"],
        "PRODUCTION_RESIZE": functions["rfbNewFramebufferWithFormat"] + "\n\n" + functions["rfbNewFramebuffer"],
        "PRODUCTION_UNLINK_AND_WAIT": helper_section(helpers, "UNLINK_AND_WAIT") + "    dispose_client(cl);\n}",
    }
    source = (HERE / "libvncserver_framebuffer_harness.c.in").read_text()
    for name, body in replacements.items():
        marker = f"/* {name} */"
        if source.count(marker) != 1:
            raise ValueError(f"Invalid C harness marker: {name}")
        source = source.replace(marker, body)
    return source


@unittest.skipUnless(os.name == "posix", "The LibVNCServer pthread regression requires a POSIX host")
class LibVNCServerFramebufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("clang") or shutil.which("cc")
        if compiler is None:
            raise unittest.SkipTest("No host C compiler is available")
        version = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout.lower()
        if not any(name in version for name in ("clang", "gcc", "free software foundation")):
            raise unittest.SkipTest("The pthread/UBSan regression requires Clang or GCC")
        cls.temporary = tempfile.TemporaryDirectory(prefix="iphonebridge-rfb-resize-")
        cls.addClassCleanup(cls.temporary.cleanup)
        build = Path(cls.temporary.name)
        source = build / "framebuffer.c"
        source.write_text(generate_source(PATCH_OVERRIDE or DEFAULT_PATCH))
        cls.binary = build / "framebuffer"
        command = [compiler, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-parameter", "-Wno-unused-function", "-fsanitize=undefined",
                   "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", "-pthread", str(source), "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Host pthread/UBSan regression compilation failed:\n" + result.stdout + result.stderr)

    def check_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), f"PASS {case}")
        self.assertEqual(result.stderr, "")

    def test_empty_framebuffer_resize(self):
        self.check_case("empty")

    def test_closed_client_remains_in_lock_set(self):
        self.check_case("closed_lock_set")

    def test_final_bgra_format_reaches_translation_callback(self):
        self.check_case("format")

    def test_disconnect_while_resize_waits_and_reader_admission(self):
        self.check_case("blocked_resize_disconnect_admission")

    def test_aliased_format_and_legacy_wrapper(self):
        self.check_case("format_alias_legacy")

    def test_snapshot_allocation_failure_leaves_framebuffer_unchanged(self):
        self.check_case("allocation_failure")

    def test_incompatible_pixel_format_leaves_framebuffer_unchanged(self):
        self.check_case("invalid_format")

    def test_empty_snapshot_succeeds_without_allocation(self):
        self.check_case("empty_snapshot")

    def test_snapshot_survives_a_then_b_removal(self):
        self.check_case("snapshot_a_b_removal")

    def test_snapshot_retains_before_concurrent_removal(self):
        self.check_case("atomic_snapshot_acquisition")

    def test_unchanged_explicit_format_never_exposes_derived_layout(self):
        self.check_case("unchanged_explicit_format")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--patch", type=Path)
    options, remaining = parser.parse_known_args()
    PATCH_OVERRIDE = options.patch
    unittest.main(argv=[sys.argv[0], *remaining])
