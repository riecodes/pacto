"""Archive a project folder into a single zip that lives inside it.

C:\\dev\\foo  ->  C:\\dev\\foo\\foo.zip  (folder kept as a marker, contents deleted)

The zip is always verified before anything is deleted, so an interrupted run
leaves the data safe: re-running zip finds the verified archive and finishes
the delete.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("PACTO_ROOT", r"C:\dev"))
PARTIAL_SUFFIX = ".zip.partial"
DAY = 86400.0


# --------------------------------------------------------------------------- paths


def resolve_root(explicit: str | os.PathLike | None = None) -> Path:
    """Explicit root, else the cwd when it sits inside DEFAULT_ROOT, else DEFAULT_ROOT."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd().resolve()
    root = DEFAULT_ROOT.resolve()
    if cwd == root or root in cwd.parents:
        return cwd
    return root


def archive_path(folder: Path) -> Path:
    return folder / f"{folder.name}.zip"


def partial_path(folder: Path) -> Path:
    return folder / f"{folder.name}{PARTIAL_SUFFIX}"


def is_packed(folder: Path) -> bool:
    """packed means the folder holds nothing but its own <name>.zip."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    return len(entries) == 1 and entries[0] == archive_path(folder)


def self_folder() -> Path:
    """The checkout/install this module runs from, so pacto never packs itself."""
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- scan


@dataclass
class Stats:
    size: int = 0
    files: int = 0
    empty_dirs: int = 0
    newest: float = 0.0
    links: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def folder_stats(folder: Path, skip: set[Path] | None = None) -> Stats:
    """One walk collecting size, file count, newest mtime and empty dirs.

    Symlinks and Windows junctions are recorded and skipped: os.walk does not
    descend into them, and copying their target into the zip would be wrong.
    """
    skip = skip or set()
    st = Stats(newest=folder.stat().st_mtime)
    for dirpath, dirnames, filenames in os.walk(folder, onerror=st.unreadable.append):
        here = Path(dirpath)
        for d in list(dirnames):
            if (here / d).is_symlink():
                st.links.append(str((here / d).relative_to(folder)))
                dirnames.remove(d)
        if not dirnames and not filenames:
            st.empty_dirs += 1
        for name in filenames:
            p = here / name
            if p in skip:
                continue
            if p.is_symlink():
                st.links.append(str(p.relative_to(folder)))
                continue
            try:
                info = p.stat()
            except OSError:
                st.unreadable.append(str(p))
                continue
            st.size += info.st_size
            st.files += 1
            st.newest = max(st.newest, info.st_mtime)
    return st


def git_state(folder: Path) -> str | None:
    """'clean', or a warning string. None when the folder is not a git repo."""
    if not (folder / ".git").exists():
        return None

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(folder), *args],
                capture_output=True,
                # the MCP server's stdin is the JSON-RPC pipe; an inherited handle hangs git on Windows
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    notes = []
    dirty = git("status", "--porcelain")
    if dirty:
        notes.append(f"{len(dirty.splitlines())} uncommitted")
    if git("remote") == "":
        notes.append("no remote")
    else:
        ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ahead and ahead != "0":
            notes.append(f"{ahead} unpushed")
    return ", ".join(notes) if notes else "clean"


@dataclass
class Entry:
    path: Path
    packed: bool
    size: int
    newest: float
    files: int
    git: str | None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def idle_days(self) -> float:
        return max((time.time() - self.newest) / DAY, 0.0)

    @property
    def score(self) -> float:
        """Big and long untouched ranks first. packed folders score nothing."""
        return 0.0 if self.packed else self.size * max(self.idle_days, 0.01)

    @property
    def dirty(self) -> bool:
        return bool(self.git) and self.git != "clean"


def scan(
    root: Path | None = None,
    min_size: int = 0,
    min_idle_days: float = 0.0,
    git: bool = True,
) -> list[Entry]:
    """Direct children of root, ranked by size * idle days."""
    root = Path(root) if root else resolve_root()
    me = self_folder()
    children = [
        c
        for c in sorted(root.iterdir())
        if c.is_dir() and not c.is_symlink() and not c.name.startswith(".") and c != me
    ]

    def measure(child: Path) -> Entry:
        packed = is_packed(child)
        st = folder_stats(child)
        return Entry(
            path=child,
            packed=packed,
            size=st.size,
            newest=st.newest,
            files=st.files,
            git=git_state(child) if git and not packed else None,
        )

    # Walking many trees is IO-bound, so threads cut a multi-minute scan down.
    with ThreadPoolExecutor(max_workers=min(16, (len(children) or 1))) as pool:
        entries = list(pool.map(measure, children))

    out: list[Entry] = []
    for entry in entries:
        if not entry.packed and (entry.size < min_size or entry.idle_days < min_idle_days):
            continue
        out.append(entry)
    out.sort(key=lambda e: e.score, reverse=True)
    return out


# --------------------------------------------------------------------------- zip


class PactoError(RuntimeError):
    pass


def _force_remove(path: Path) -> None:
    """Delete a tree, clearing the read-only bit git sets on .git/objects."""

    def on_error(func, target, exc_info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=on_error)
    else:
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def _write_zip(folder: Path, dest: Path, skip: set[Path]) -> Stats:
    st = folder_stats(folder, skip=skip)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(folder):
            here = Path(dirpath)
            for d in list(dirnames):
                if (here / d).is_symlink():
                    dirnames.remove(d)
            if not dirnames and not filenames and here != folder:
                # zipfile stores files only; keep empty dirs so restore is faithful
                zf.writestr(f"{here.relative_to(folder).as_posix()}/", "")
            for name in filenames:
                p = here / name
                if p in skip or p.is_symlink():
                    continue
                try:
                    zf.write(p, p.relative_to(folder).as_posix())
                except OSError as exc:
                    raise PactoError(f"cannot read {p}: {exc}") from exc
    return st


def verify_zip(archive: Path, expect: Stats | None = None) -> None:
    """CRC-check every member, and match file count and total size when known."""
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise PactoError(f"corrupt entry in {archive.name}: {bad}")
        members = [i for i in zf.infolist() if not i.is_dir()]
        if expect is not None:
            if len(members) != expect.files:
                raise PactoError(
                    f"{archive.name} holds {len(members)} files, folder has {expect.files}"
                )
            total = sum(i.file_size for i in members)
            if total != expect.size:
                raise PactoError(
                    f"{archive.name} unpacks to {total} bytes, folder is {expect.size}"
                )


@dataclass
class ZipResult:
    folder: Path
    archive: Path
    zip_size: int
    freed: int
    files: int
    resumed: bool = False
    links_skipped: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)


def zip_folder(folder: Path) -> ZipResult:
    """Zip the folder into <name>.zip inside it, verify, then delete the rest."""
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise PactoError(f"{folder} is not a folder")
    if folder == self_folder():
        raise PactoError("refusing to pacto pacto's own checkout")
    archive = archive_path(folder)
    partial = partial_path(folder)

    if is_packed(folder):
        raise PactoError(f"{folder.name} is already packed")

    resumed = False
    if archive.exists():
        # A previous run verified this archive but did not finish deleting.
        try:
            verify_zip(archive)
        except (PactoError, zipfile.BadZipFile) as exc:
            raise PactoError(
                f"{archive.name} already exists and did not verify ({exc}). "
                "Move or delete it, then run again."
            ) from exc
        resumed = True
        st = folder_stats(folder, skip={archive, partial})
    else:
        if partial.exists():
            partial.unlink()
        st = _write_zip(folder, partial, skip={archive, partial})
        verify_zip(partial, st)
        partial.replace(archive)

    zip_size = archive.stat().st_size
    leftovers: list[str] = []
    for child in folder.iterdir():
        if child == archive:
            continue
        try:
            _force_remove(child)
        except OSError as exc:
            leftovers.append(f"{child.name}: {exc}")

    return ZipResult(
        folder=folder,
        archive=archive,
        zip_size=zip_size,
        freed=max(st.size - zip_size, 0),
        files=st.files,
        resumed=resumed,
        links_skipped=st.links,
        leftovers=leftovers,
    )


# --------------------------------------------------------------------------- unzip


def _safe_target(folder: Path, name: str) -> Path:
    target = (folder / name).resolve()
    if target != folder and folder not in target.parents:
        raise PactoError(f"archive entry escapes the folder: {name}")
    return target


def unzip_folder(folder: Path, keep_zip: bool = False) -> ZipResult:
    """Restore the folder in place, then delete the archive."""
    folder = Path(folder).resolve()
    archive = archive_path(folder)
    if not archive.exists():
        raise PactoError(f"no {archive.name} in {folder}")
    others = [c for c in folder.iterdir() if c != archive]
    if others:
        raise PactoError(
            f"{folder.name} holds {len(others)} other item(s); refusing to unzip over them"
        )

    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise PactoError(f"corrupt entry in {archive.name}: {bad}")
        for info in zf.infolist():
            _safe_target(folder, info.filename)
        members = [i for i in zf.infolist() if not i.is_dir()]
        size = sum(i.file_size for i in members)
        zf.extractall(folder)

    zip_size = archive.stat().st_size
    if not keep_zip:
        archive.unlink()
    return ZipResult(
        folder=folder,
        archive=archive,
        zip_size=zip_size,
        freed=-size,
        files=len(members),
    )


# --------------------------------------------------------------------------- format


def human(n: float) -> str:
    sign = "-" if n < 0 else ""
    value = abs(float(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            digits = 0 if unit == "B" else 1
            return f"{sign}{value:.{digits}f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def parse_size(text: str) -> int:
    units = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2,
             "G": 1024**3, "GB": 1024**3, "T": 1024**4, "TB": 1024**4}
    raw = text.strip().upper().replace(" ", "")
    for unit in sorted(units, key=len, reverse=True):
        if raw.endswith(unit):
            return int(float(raw[: -len(unit)] or 1) * units[unit])
    return int(float(raw))


def parse_days(text: str) -> float:
    raw = text.strip().lower().replace(" ", "")
    mult = {"d": 1.0, "w": 7.0, "m": 30.0, "y": 365.0}
    if raw and raw[-1] in mult:
        return float(raw[:-1]) * mult[raw[-1]]
    return float(raw)
