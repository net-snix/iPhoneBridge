"""Bundled interpreter entrypoint; isolated mode never imports checkout/user code."""
from pathlib import Path
import runpy
import sys

def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    if sys.argv[1:2] == ["--module"]:
        if len(sys.argv) < 3 or sys.argv[2] not in {"websockify", "iphonebridge.fixture_server"}:
            raise SystemExit("Unsupported bridge helper module")
        module = sys.argv[2]
        sys.argv = [module, *sys.argv[3:]]
    else:
        module = "iphonebridge"
    runpy.run_module(module, run_name="__main__")


# macOS multiprocessing imports this file as __mp_main__ with the helper's argv.
# Importing it must not dispatch the CLI or start another helper.
if __name__ == "__main__":
    main()
