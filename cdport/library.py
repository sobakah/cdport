"""Step 5: move the finished album into the music library."""

from __future__ import annotations

import filecmp
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import tags
from .config import STATE_FILE_NAME

# Path separators, NUL and the characters Windows forbids in file names.
_UNSAFE = re.compile(r'[\x00/\\:*?"<>|]')


def safe_component(text: str) -> str:
    """Make a tag value usable as one folder name."""
    # Windows and SMB shares refuse names that end in a dot or a space.
    cleaned = _UNSAFE.sub("_", text).strip().rstrip(". ")
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
    renamed: dict[Path, str] = field(default_factory=dict)  # a cue sheet or log that needs a name of its own


def plan_disc_merge(source: Path, target: Path) -> DiscMerge | None:
    """The merge, when target holds other discs of the album in source; else None.

    cyanrip names the folder of every disc after the album alone, but the
    files apart ("1.01 - ...", "2.01 - ...", "<album> CD2.log"), so the
    second disc of an album finds the first one's folder in the library.
    Merging is only offered for the same release (MusicBrainz release ID,
    or album and album artist when a folder has no ID; see _same_release)
    with disc numbers that do not overlap. Raises MoveError when a file of the same name but
    other content is there: nothing in the library is ever replaced.
    """
    here, there = tags.audio_files(source), tags.audio_files(target)
    if not here or not there:
        return None
    tags_here = [tags.read_tags(f) for f in here]
    tags_there = [tags.read_tags(f) for f in there]
    if not _same_release(tags_here, tags_there):
        return None
    discs_here = {_disc(t) for t in tags_here}
    discs_there = {_disc(t) for t in tags_there}
    if "" in discs_here or "" in discs_there or discs_here & discs_there:
        return None
    files, duplicates, renamed = [], [], {}
    for f in sorted(source.iterdir()):
        if f.name == STATE_FILE_NAME:
            continue
        existing = target / f.name
        if not existing.exists():
            files.append(f)
        elif f.is_file() and existing.is_file() and filecmp.cmp(f, existing, shallow=False):
            duplicates.append(f)
        elif (name := _disc_sidecar(f, discs_here)) and not (target / name).exists():
            files.append(f)
            renamed[f] = name
        else:
            raise MoveError(f"{existing} already exists with other content - not merging")
    return DiscMerge(sorted(discs_there, key=_disc_order), sorted(discs_here, key=_disc_order),
                     files, duplicates, renamed)


def _disc_sidecar(path: Path, discs: set[str]) -> str | None:
    """The name cyanrip gives a disc's cue sheet or log, "<album> CD<disc>".
    Discs tagged by hand have no number at the rip, so both are "<album>"."""
    if path.suffix.lower() not in (".cue", ".log") or len(discs) != 1:
        return None
    return f"{path.stem} CD{next(iter(discs))}{path.suffix}"


def merge_disc(plan: DiscMerge, target: Path) -> None:
    """Move the disc's files into the album's library folder (see plan_disc_merge).

    All or nothing: when a move fails, the files moved so far go back, so
    the disc is never left split between the working folder and the library.
    """
    moved: list[tuple[Path, Path]] = []
    try:
        for f in plan.files:
            destination = target / plan.renamed.get(f, f.name)
            if destination.exists():  # appeared since the plan was made
                raise MoveError(f"{destination} already exists - not overwriting it")
            shutil.move(str(f), str(destination))
            moved.append((f, destination))
    except (MoveError, OSError) as exc:
        stuck = []
        for source, destination in reversed(moved):
            try:
                shutil.move(str(destination), str(source))
            except OSError:
                stuck.append(destination.name)
        if stuck:
            raise MoveError(f"{exc}; these files could not be moved back and are in {target}: "
                            + ", ".join(stuck)) from exc
        raise MoveError(f"{exc}; the disc stays where it was") from exc
    for f in plan.duplicates:
        f.unlink()


def _same_release(here: list[dict[str, str]], there: list[dict[str, str]]) -> bool:
    """Whether the tracks of two folders belong to one release.

    The MusicBrainz release ID decides when both have one. Otherwise every
    track must have the same album title, and the album artists must agree:
    the same where both folders have them, else at least one artist in
    common (track artists differ within an album: guests, compilations).
    """
    ids_here, ids_there = _values(here, "musicbrainz_albumid"), _values(there, "musicbrainz_albumid")
    if ids_here and ids_there:
        return ids_here == ids_there
    albums = {t.get("album", "") for t in here + there}
    if len(albums) != 1 or albums == {""}:
        return False
    artists_here, artists_there = _values(here, "albumartist"), _values(there, "albumartist")
    if artists_here and artists_there:
        return artists_here == artists_there
    return bool(_artists(here) & _artists(there))


def _values(tracks: list[dict[str, str]], key: str) -> set[str]:
    return {t[key] for t in tracks if t.get(key)}


def _artists(tracks: list[dict[str, str]]) -> set[str]:
    return {t.get("albumartist") or t.get("artist", "") for t in tracks} - {""}


def _disc(t: dict[str, str]) -> str:
    """The disc number without leading zeros ("01/2" is "1"); disc 0 (a
    bonus disc) stays "0", "" means none."""
    raw = t.get("discnumber", "").split("/")[0].strip()
    return raw.lstrip("0") or ("0" if raw else "")


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
