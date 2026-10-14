"""Step 5: move the finished album into the music library."""

from __future__ import annotations

import filecmp
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import tags
from .config import STATE_FILE_NAME

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


@dataclass
class DiscMerge:
    """How a disc joins the library folder of another disc of its album."""
    discs_there: list[str]
    discs_here: list[str]
    files: list[Path]       # moved into the library folder
    duplicates: list[Path]  # byte-identical to a file there (cover.jpg); dropped


def plan_disc_merge(source: Path, target: Path) -> DiscMerge | None:
    """The merge, when target holds other discs of the album in source; else None.

    cyanrip names the folder of every disc after the album alone, but the
    files apart ("1.01 - ...", "2.01 - ...", "<album> CD2.log"), so the
    second disc of an album finds the first one's folder in the library.
    Merging is only offered for the same release (MusicBrainz release ID,
    or album and album artist when a folder has no ID) with disc numbers
    that do not overlap. Raises MoveError when a file of the same name but
    other content is there: nothing in the library is ever replaced.
    """
    here, there = tags.audio_files(source), tags.audio_files(target)
    if not here or not there:
        return None
    tags_here = [tags.read_tags(f) for f in here]
    tags_there = [tags.read_tags(f) for f in there]
    if not _same_release(tags_here[0], tags_there[0]):
        return None
    discs_here = {_disc(t) for t in tags_here}
    discs_there = {_disc(t) for t in tags_there}
    if "" in discs_here or "" in discs_there or discs_here & discs_there:
        return None
    files, duplicates = [], []
    for f in sorted(source.iterdir()):
        if f.name == STATE_FILE_NAME:
            continue
        existing = target / f.name
        if not existing.exists():
            files.append(f)
        elif f.is_file() and existing.is_file() and filecmp.cmp(f, existing, shallow=False):
            duplicates.append(f)
        else:
            raise MoveError(f"{existing} already exists with other content - not merging")
    return DiscMerge(sorted(discs_there, key=_disc_order), sorted(discs_here, key=_disc_order),
                     files, duplicates)


def merge_disc(plan: DiscMerge, target: Path) -> None:
    """Move the disc's files into the album's library folder (see plan_disc_merge)."""
    for f in plan.files:
        destination = target / f.name
        if destination.exists():  # appeared since the plan was made
            raise MoveError(f"{destination} already exists - not overwriting it")
        shutil.move(str(f), str(destination))
    for f in plan.duplicates:
        f.unlink()


def _same_release(a: dict[str, str], b: dict[str, str]) -> bool:
    id_a, id_b = a.get("musicbrainz_albumid", ""), b.get("musicbrainz_albumid", "")
    if id_a and id_b:
        return id_a == id_b
    album = a.get("album", "")
    return bool(album) and album == b.get("album", "") and \
        (a.get("albumartist") or a.get("artist", "")) == (b.get("albumartist") or b.get("artist", ""))


def _disc(t: dict[str, str]) -> str:
    return t.get("discnumber", "").split("/")[0].strip().lstrip("0")


def _disc_order(disc: str) -> tuple[int, str]:
    return (int(disc), disc) if disc.isdigit() else (1 << 30, disc)


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
