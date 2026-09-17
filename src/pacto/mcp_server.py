"""MCP server exposing pacto over stdio. Destructive calls need confirm=true."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP("pacto")


def _entry(e: core.Entry) -> dict[str, Any]:
    return {
        "name": e.name,
        "path": str(e.path),
        "packed": e.packed,
        "size_bytes": e.size,
        "size": core.human(e.size),
        "files": e.files,
        "idle_days": round(e.idle_days, 1),
        "git": e.git,
    }


def _folder(name: str, root: str | None) -> Path:
    path = Path(name)
    return path.resolve() if path.is_absolute() else (core.resolve_root(root) / name).resolve()


@mcp.tool()
def scan(root: str | None = None, min_size: str | None = None, idle: str | None = None) -> dict:
    """List folders in root with size, idle days, git state and packed status.

    min_size like "500MB", idle like "60d".
    """
    entries = core.scan(
        core.resolve_root(root),
        min_size=core.parse_size(min_size) if min_size else 0,
        min_idle_days=core.parse_days(idle) if idle else 0.0,
    )
    return {
        "root": str(core.resolve_root(root)),
        "folders": [_entry(e) for e in entries],
    }


@mcp.tool()
def suggest(root: str | None = None, limit: int = 10) -> dict:
    """Best archive candidates, ranked by size * idle days."""
    entries = [e for e in core.scan(core.resolve_root(root)) if not e.packed][:limit]
    return {
        "candidates": [_entry(e) for e in entries],
        "reclaimable": core.human(sum(e.size for e in entries)),
    }


@mcp.tool()
def zip(name: str, root: str | None = None, confirm: bool = False) -> dict:
    """Archive a folder into <name>.zip inside it and DELETE its other contents.

    Without confirm=true this only reports what would happen.
    """
    folder = _folder(name, root)
    if not confirm:
        st = core.folder_stats(folder)
        return {
            "dry_run": True,
            "folder": str(folder),
            "files": st.files,
            "size": core.human(st.size),
            "git": core.git_state(folder),
            "note": "call again with confirm=true to archive and delete the contents",
        }
    result = core.zip_folder(folder)
    return {
        "folder": str(result.folder),
        "archive": str(result.archive),
        "files": result.files,
        "zip_size": core.human(result.zip_size),
        "freed": core.human(result.freed),
        "resumed": result.resumed,
        "links_skipped": result.links_skipped,
        "leftovers": result.leftovers,
    }


@mcp.tool()
def unzip(name: str, root: str | None = None, keep_zip: bool = False) -> dict:
    """Restore a packed folder in place and delete its archive."""
    result = core.unzip_folder(_folder(name, root), keep_zip=keep_zip)
    return {"folder": str(result.folder), "files_restored": result.files}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
