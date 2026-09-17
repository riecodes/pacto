"""Round-trip check: zip a tree, confirm it is gone, unzip, compare byte for byte.

Run with: python tests/test_core.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from freeze import core  # noqa: E402


def make_tree(root: Path) -> dict[str, bytes]:
    files = {
        "README.md": b"# demo\n",
        "src/app.py": b"print('hi')\n" * 100,
        "src/deep/nested/data.bin": os.urandom(4096),
        ".hidden": b"dotfiles must survive\n",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "empty_dir").mkdir()
    return files


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_round_trip(tmp: Path) -> None:
    folder = tmp / "demo"
    folder.mkdir()
    expected = make_tree(folder)
    before = snapshot(folder)

    result = core.zip_folder(folder)
    assert result.files == len(expected), result.files
    assert core.is_frozen(folder), list(folder.iterdir())
    assert result.archive == folder / "demo.zip"
    assert result.freed > 0

    core.unzip_folder(folder)
    assert not (folder / "demo.zip").exists()
    assert snapshot(folder) == before
    assert (folder / "empty_dir").is_dir(), "empty dirs must survive the round trip"


def test_refuses_double_freeze(tmp: Path) -> None:
    folder = tmp / "twice"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    core.zip_folder(folder)
    try:
        core.zip_folder(folder)
    except core.FreezeError as exc:
        assert "already frozen" in str(exc)
    else:
        raise AssertionError("second zip should refuse")


def test_resumes_after_interrupted_delete(tmp: Path) -> None:
    """A verified archive plus leftover files means finish the delete, not re-zip."""
    folder = tmp / "halfway"
    folder.mkdir()
    (folder / "keep.txt").write_text("data")
    core.zip_folder(folder)
    (folder / "leftover.txt").write_text("survivor of a crashed run")

    result = core.zip_folder(folder)
    assert result.resumed is True
    assert core.is_frozen(folder)
    with zipfile.ZipFile(folder / "halfway.zip") as zf:
        assert zf.namelist() == ["keep.txt"], zf.namelist()


def test_rejects_corrupt_archive(tmp: Path) -> None:
    folder = tmp / "corrupt"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    (folder / "corrupt.zip").write_bytes(b"not a zip at all")
    try:
        core.zip_folder(folder)
    except core.FreezeError as exc:
        assert "did not verify" in str(exc)
    else:
        raise AssertionError("must not delete anything next to an unreadable archive")
    assert (folder / "a.txt").exists()


def test_unzip_refuses_zip_slip(tmp: Path) -> None:
    folder = tmp / "evil"
    folder.mkdir()
    with zipfile.ZipFile(folder / "evil.zip", "w") as zf:
        zf.writestr("../escaped.txt", "pwned")
    try:
        core.unzip_folder(folder)
    except core.FreezeError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("path traversal must be refused")
    assert not (tmp / "escaped.txt").exists()


def test_size_and_day_parsing() -> None:
    assert core.parse_size("500MB") == 500 * 1024**2
    assert core.parse_size("1.5g") == int(1.5 * 1024**3)
    assert core.parse_size("1024") == 1024
    assert core.parse_days("60d") == 60
    assert core.parse_days("2w") == 14
    assert core.human(1536) == "1.5 KB"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        with tempfile.TemporaryDirectory() as td:
            args = (Path(td),) if test.__code__.co_argcount else ()
            test(*args)
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
