"""freeze CLI: scan, zip, unzip, plus an interactive picker when run bare."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import core
from .core import FreezeError, human


def _rows(entries: list[core.Entry]) -> str:
    if not entries:
        return "nothing to show"
    width = max(len(e.name) for e in entries)
    lines = []
    for e in entries:
        state = "frozen" if e.frozen else f"{human(e.size):>9}  {e.idle_days:5.0f}d"
        git = f"  [{e.git}]" if e.dirty else ""
        lines.append(f"  {e.name:<{width}}  {state}{git}")
    return "\n".join(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    entries = core.scan(
        core.resolve_root(args.root),
        min_size=core.parse_size(args.min_size) if args.min_size else 0,
        min_idle_days=core.parse_days(args.idle) if args.idle else 0.0,
        git=not args.no_git,
    )
    if args.json:
        print(json.dumps([
            {
                "name": e.name,
                "path": str(e.path),
                "frozen": e.frozen,
                "size": e.size,
                "files": e.files,
                "idle_days": round(e.idle_days, 1),
                "git": e.git,
                "score": e.score,
            }
            for e in entries
        ], indent=2))
        return 0
    print(_rows(entries))
    live = [e for e in entries if not e.frozen]
    if live:
        print(f"\n{len(live)} active, {human(sum(e.size for e in live))} reclaimable")
    return 0


def _report(result: core.ZipResult, unzipped: bool = False) -> None:
    if unzipped:
        print(f"unzipped {result.folder.name}: {result.files} files restored")
        return
    note = " (resumed)" if result.resumed else ""
    print(
        f"froze {result.folder.name}{note}: {result.files} files -> "
        f"{human(result.zip_size)}, freed {human(result.freed)}"
    )
    for link in result.links_skipped:
        print(f"  skipped link: {link}")
    for left in result.leftovers:
        print(f"  NOT deleted (locked?): {left}", file=sys.stderr)


def _targets(args: argparse.Namespace) -> list[Path]:
    root = core.resolve_root(args.root)
    out = []
    for name in args.names:
        path = Path(name)
        out.append(path.resolve() if path.is_absolute() else (root / name).resolve())
    return out


def cmd_zip(args: argparse.Namespace) -> int:
    failed = 0
    for folder in _targets(args):
        state = core.git_state(folder)
        if state and state != "clean":
            print(f"warning: {folder.name} git: {state}")
        try:
            _report(core.zip_folder(folder))
        except (FreezeError, OSError) as exc:
            print(f"error: {folder.name}: {exc}", file=sys.stderr)
            failed = 1
    return failed


def cmd_unzip(args: argparse.Namespace) -> int:
    failed = 0
    for folder in _targets(args):
        try:
            _report(core.unzip_folder(folder, keep_zip=args.keep_zip), unzipped=True)
        except (FreezeError, OSError) as exc:
            print(f"error: {folder.name}: {exc}", file=sys.stderr)
            failed = 1
    return failed


def cmd_pick(args: argparse.Namespace) -> int:
    try:
        import questionary
    except ImportError:
        print("interactive picker needs: pip install questionary", file=sys.stderr)
        return 2

    root = core.resolve_root(args.root)
    print(f"scanning {root} ...")
    entries = core.scan(root)
    active = [e for e in entries if not e.frozen]
    frozen = [e for e in entries if e.frozen]
    if not active and not frozen:
        print("nothing here")
        return 0

    action = questionary.select(
        "What do you want to do?",
        choices=[f"zip ({len(active)} active)", f"unzip ({len(frozen)} frozen)", "quit"],
    ).ask()
    if not action or action == "quit":
        return 0

    pool = active if action.startswith("zip") else frozen
    if not pool:
        print("nothing to do")
        return 0
    labels = {
        f"{e.name}  {human(e.size)}  idle {e.idle_days:.0f}d"
        + (f"  [{e.git}]" if e.dirty else ""): e
        for e in pool
    }
    picked = questionary.checkbox("Pick folders", choices=list(labels)).ask()
    if not picked:
        return 0
    chosen = [labels[p] for p in picked]

    if action.startswith("zip"):
        total = sum(e.size for e in chosen)
        print(f"this deletes the contents of {len(chosen)} folder(s), freeing about {human(total)}")
        if not questionary.confirm("Continue?", default=False).ask():
            return 0
    ns = argparse.Namespace(root=str(root), names=[e.name for e in chosen], keep_zip=False)
    return cmd_zip(ns) if action.startswith("zip") else cmd_unzip(ns)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="freeze",
        description="Archive a project folder into a single zip inside it. "
        "Run bare for an interactive picker.",
    )
    p.add_argument("--root", help=f"folder to work in (default: cwd if under {core.DEFAULT_ROOT}, else {core.DEFAULT_ROOT})")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="list folders ranked by size * idle days")
    s.add_argument("--min-size", help="only folders at least this big, e.g. 500MB")
    s.add_argument("--idle", help="only folders untouched this long, e.g. 60d")
    s.add_argument("--no-git", action="store_true", help="skip git status checks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    z = sub.add_parser("zip", help="archive folders (deletes contents after verifying)")
    z.add_argument("names", nargs="+")
    z.set_defaults(func=cmd_zip)

    u = sub.add_parser("unzip", help="restore folders in place")
    u.add_argument("names", nargs="+")
    u.add_argument("--keep-zip", action="store_true", help="keep the archive after restoring")
    u.set_defaults(func=cmd_unzip)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        return cmd_pick(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
