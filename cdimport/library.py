"""Step 5: move the finished album into the music library."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# Path separators, NUL and the characters Windows forbids in file names.
_UNSAFE = re.compile(r'[\x00/\\:*?"<>|]')


def safe_component(text: str) -> str:
    """Make a tag value usable as one folder name."""
    cleaned = _UNSAFE.sub("_", text).strip().rstrip(".")
    return cleaned or "_"


def destination(library: Path, layout: str, folder: Path, albumartist: str) -> Path:
    """Where the album would go, from the layout template.

    Each '/'-separated part of the layout is one folder level; the values put
    into it are sanitised, so an artist like 'AC/DC' cannot add a level.
    """
    values = {"albumartist": safe_component(albumartist or "Unknown Artist"),
              "folder": safe_component(folder.name)}
    parts = [part.format(**values) for part in layout.split("/") if part]
    return library.joinpath(*parts)


class MoveError(Exception):
    pass


def move_album(source: Path, target: Path) -> None:
    """Move the album folder; never replaces or merges into an existing folder."""
    if target.exists():
        raise MoveError(f"{target} already exists - not overwriting it")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def keep_logs_out(album: Path, log_dir: Path) -> list[Path]:
    """Move the rip logs (*.log) out of an album that went into the library.

    The library holds music; the log is the rip's AccurateRip proof, so it
    is kept in log_dir under a name that never overwrites an older log.
    Returns where the logs went.
    """
    moved = []
    for log in sorted(album.glob("*.log")):
        log_dir.mkdir(parents=True, exist_ok=True)
        target = unique_file(log_dir / log.name)
        shutil.move(str(log), str(target))
        moved.append(target)
    return moved


def unique_file(path: Path) -> Path:
    """path, or 'name (2).ext', 'name (3).ext', ... - never an existing file.

    Unlike rip.unique_path (for folders, whose names may contain dots) the
    number goes before the extension.
    """
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate
