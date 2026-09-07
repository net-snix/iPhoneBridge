"""Command-line interface for the local iPhone bridge."""
import argparse
import json
import subprocess
import sys

from . import lifecycle


def main():
    parser = argparse.ArgumentParser(prog="iphonebridge", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "deploy", "start", "stop", "health", "screenshot", "mcp", "mirror", "viewer", "test-surface"):
        commands.add_parser(name)
    tap = commands.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    drag = commands.add_parser("drag", aliases=["swipe"])
    for name in ("x1", "y1", "x2", "y2"):
        drag.add_argument(name, type=int)
    drag.add_argument("--duration", type=float, default=0.5)
    typing = commands.add_parser("type", help="Read text from stdin; avoids shell history")
    key = commands.add_parser("key")
    key.add_argument("name")
    navigate = commands.add_parser("navigate", help="Open Home or App Switcher using the native Home button")
    navigate.add_argument("name", choices=("home", "app-switcher"))
    for subparser in (tap, drag, typing, key, navigate):
        subparser.add_argument("--size", nargs=2, type=int, required=True,
                               metavar=("WIDTH", "HEIGHT"), help="Exact latest screenshot size")
    args = parser.parse_args()
    if args.command == "mirror":
        app = lifecycle.ROOT / "iPhoneBridge.app"
        if not app.is_dir():
            raise RuntimeError("Build the Mac app first with ./scripts/build-app")
        subprocess.run(["/usr/bin/open", str(app)], check=True)
        return
    if args.command == "setup":
        subprocess.run([str(lifecycle.ROOT / "scripts/setup")], check=True)
        return
    if args.command == "mcp":
        from .mcp_server import main as serve
        serve()
        return
    if args.command == "viewer":
        print(lifecycle.viewer_url())
        return
    if args.command == "test-surface":
        from . import fixture
        print(json.dumps(fixture.start(), indent=2))
        return
    if args.command in ("deploy", "start", "stop", "health"):
        result = getattr(lifecycle, args.command)()
    else:
        from . import control
        if args.command == "screenshot":
            result = control.screenshot()
        elif args.command == "tap":
            result = control.tap(args.x, args.y, *args.size)
        elif args.command in ("drag", "swipe"):
            result = control.drag(args.x1, args.y1, args.x2, args.y2, *args.size, args.duration)
        elif args.command == "type":
            result = control.type_text(sys.stdin.read(257), *args.size)
        elif args.command == "navigate":
            result = control.navigate(args.name, *args.size)
        else:
            result = control.key(args.name, *args.size)
    print(json.dumps(result, indent=2))
    if args.command == "health" and not result["healthy"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"iphonebridge: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        sys.exit(1)
