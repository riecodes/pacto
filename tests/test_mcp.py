"""MCP stdio check: a zip dry run on a git repo must answer, not hang.

git used to inherit the server's stdin (the JSON-RPC pipe) and hang on Windows.

Run with: python tests/test_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = Path(__file__).resolve().parents[1] / "src"


async def dry_run(root: Path) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pacto.mcp_server"],
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("zip", {"name": "demo", "root": str(root)})
            return json.loads(result.content[0].text)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "demo"
        folder.mkdir()
        (folder / "a.txt").write_text("hi\n")
        subprocess.run(["git", "init", "-q", str(folder)], check=True, stdin=subprocess.DEVNULL)

        out = asyncio.run(asyncio.wait_for(dry_run(Path(tmp)), timeout=60))

        assert out["dry_run"] is True, out
        assert out["files"] >= 1, out
        assert out["git"] is not None, out
        assert (folder / "a.txt").exists(), "dry run must not delete anything"
    print("ok: mcp zip dry run answered", out["git"])


if __name__ == "__main__":
    main()
