"""Extract exact production bodies from complete new-side patch hunks.

No vendor checkout, downloaded SDK, work directory, or phone is required. Missing
function context is an error: separate hunks may never form a synthetic body.
"""
from pathlib import Path
import hashlib
import re

HERE = Path(__file__).resolve().parent
HELPERS_SHA256 = "a6bbf0030461b06c5122100a6920d6fdac8f872735b95fda908e74824638adc8"
TOKENS = re.compile(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]')


def new_side_hunks(patch):
    files, current_file, current_hunk = {}, None, None

    def finish():
        if current_hunk is not None:
            files.setdefault(current_file, []).append("".join(current_hunk))

    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            finish()
            current_file = current_hunk = None
        elif line.startswith("+++ b/") and current_hunk is None:
            current_file = line[len("+++ b/"):].rstrip("\r\n")
        elif line.startswith("@@ "):
            finish()
            if current_file is None:
                raise ValueError("Patch hunk has no resulting filename")
            current_hunk = []
        elif current_hunk is not None and line.startswith(("+", " ")):
            current_hunk.append(line[1:])
    finish()
    return files


def function(hunks, signature):
    found = []
    for hunk in hunks:
        pattern = re.compile(r"(?m)^" + re.escape(signature))
        for match in pattern.finditer(hunk):
            opening = hunk.find("{", match.start())
            if opening < 0 or ";" in hunk[match.start():opening]:
                continue
            depth = 0
            for token in TOKENS.finditer(hunk, opening):
                if token.group() == "{":
                    depth += 1
                elif token.group() == "}":
                    depth -= 1
                    if depth == 0:
                        found.append(hunk[match.start():token.end()] + "\n")
                        break
    if len(found) != 1:
        raise ValueError(f"Expected one complete definition for {signature!r}; found {len(found)}")
    return found[0]


def range_in_hunk(hunks, begin, end):
    found = []
    for hunk in hunks:
        if begin in hunk and end in hunk.split(begin, 1)[1]:
            found.append(begin + hunk.split(begin, 1)[1].split(end, 1)[0])
    if len(found) != 1:
        raise ValueError(f"Expected one complete source region beginning {begin!r}")
    return found[0]


def replace_markers(template, replacements):
    for marker, value in replacements.items():
        key = f"/* {marker} */"
        if template.count(key) != 1:
            raise ValueError(f"Invalid harness marker {marker}")
        template = template.replace(key, value)
    return template


def write_headers(patch, directory):
    hunks = new_side_hunks(patch)
    for name in ("CaptureSchedule.h", "CaptureGovernor.h", "FrameUpdateRetry.h", "ClientSendLocks.h"):
        path = "src/" + name
        section = patch.split(f"diff --git a/{path} b/{path}\n", 1)[1].split("diff --git ", 1)[0]
        if "--- /dev/null\n" not in section or len(hunks.get(path, [])) != 1:
            raise ValueError(f"{name} must be a complete newly added production header")
        (directory / name).write_text(hunks[path][0])
    (directory / "rfb").mkdir()
    (directory / "rfb/rfb.h").write_bytes((HERE / "daemon_rfb_test_types.h").read_bytes())


def overlap_source(patch):
    hunks = new_side_hunks(patch)
    capture, server = hunks["src/ScreenCapturer.mm"], hunks["src/trollvncserver.mm"]

    def functions(source, signatures):
        return "\n".join(function(source, name) for name in signatures)

    methods = functions(capture, (
        "- (void)startCaptureWithFrameHandler:", "- (void)endCapture {",
        "- (void)setFramePublicationHandler:", "- (void)setCaptureLifecycleHandler:",
        "- (void)forceNextFrameUpdate {", "- (void)cancelCaptureWakeTimer {",
        "- (void)onDisplayLink:", "- (void)dealloc {"))
    fixture = (HERE / "daemon_pinned_helpers.mm.inc").read_bytes()
    if hashlib.sha256(fixture).hexdigest() != HELPERS_SHA256:
        raise ValueError("Unchanged pinned TrollVNC helper fixture differs from reviewed source")
    pure = fixture.decode() + functions(server, (
        "static int buildDirtyRects(", "NS_INLINE void markRectsModified(",
        "static void hashFullBackBuffer(",
        "NS_INLINE void swapBuffers(", "static void resetPreparedFrameSession("))
    publish = functions(server, (
        "NS_INLINE BOOL maybeResizeFramebufferForRotation(",
        "static void tryPublishPreparedFrame(void) {", "static void handleFramebuffer("))
    asyncs = functions(server, (
        "static void requestPreparedFramePublication(", "static void displayHook(",
        "static void displayFinishedHook(", "static void requestCaptureLifecycleReconcile("))
    scratch = functions(server, ("NS_INLINE int ensureRotateScratch(", "NS_INLINE int ensureScaleTemp("))
    # The rest of newClientHook manages unrelated protocol/clipboard setup. Run
    # its exact acceptance tail, checking that no code follows the gate.
    client = function(server, "static enum rfbNewClientAction newClientHook(")
    barrier = "    std::lock_guard<std::mutex> admission(gPublicationAdmissionMutex);\n"
    if client.count(barrier) != 1 or client.split(barrier, 1)[1] != "    return RFB_CLIENT_ACCEPT;\n}\n":
        raise ValueError("Client acceptance must end at the publication admission barrier")
    return replace_markers((HERE / "daemon_overlap_harness.mm.in").read_text(), {
        "SCREEN_METHODS": methods, "PURE_FUNCTIONS": pure, "SCRATCH_FUNCTIONS": scratch,
        "PUBLISH_AND_PREPARE": publish, "ASYNC_FUNCTIONS": asyncs, "ACCEPTANCE_BARRIER": barrier})


def timer_source(patch):
    capture = new_side_hunks(patch)["src/ScreenCapturer.mm"]
    methods = "\n".join(function(capture, signature) for signature in (
        "- (void)cancelCaptureWakeTimer {", "- (void)queueCaptureWake:", "- (void)dealloc {"))
    end_capture = function(capture, "- (void)endCapture {")
    stop = "mCaptureSchedule.stop();" + end_capture.split("mCaptureSchedule.stop();", 1)[1].split("if (mDisplayLink)", 1)[0]
    return replace_markers((HERE / "daemon_capture_timer_harness.mm.in").read_text(), {
        "PRODUCTION_TIMER_METHODS": methods, "STOP_BODY": stop})
