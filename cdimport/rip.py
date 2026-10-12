"""Step 2: rip the disc with cyanrip."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import proc
from .config import STAGING_PREFIX
from .tags import AUDIO_EXTENSIONS


class RipError(Exception):
    """The rip failed or its result cannot be found."""


@dataclass
class ManualTags:
    """Tags typed in by the user, for a disc MusicBrainz does not know.

    cyanrip refuses to rip such a disc without them (or -N); it names the
    album folder and the files from them.
    """
    album: str = ""
    artist: str = ""
    date: str = ""
    titles: dict[int, str] = field(default_factory=dict)  # track number -> title


def _escape(value: str) -> str:
    # cyanrip parses -a/-t with FFmpeg's av_dict_parse_string ("key=value:..."),
    # and its own check for keyless entries understands backslash escapes but
    # not quotes, so a backslash is the only safe way to keep ":" in a title.
    return re.sub(r"([\\':=])", r"\\\1", value)


def tag_args(tags: ManualTags) -> list[str]:
    """cyanrip's -a (album) and -t (per track) options for typed-in tags."""
    album = [f"{key}={_escape(value)}" for key, value in
             (("album", tags.album), ("album_artist", tags.artist), ("date", tags.date)) if value]
    args = ["-a", ":".join(album)] if album else []
    for number, title in sorted(tags.titles.items()):
        if title:
            args += ["-t", f"{number}=title={_escape(title)}"]
    return args


def cyanrip_args(cfg: dict[str, Any], *, device: str = "", release: str = "",
                 no_musicbrainz: bool = False, info_only: bool = False,
                 tags: ManualTags | None = None) -> list[str]:
    """cyanrip options from the config and this run's choices (without the program)."""
    opts = cfg["cyanrip"]
    args: list[str] = []
    device = device or opts["device"]
    if device:
        args += ["-d", device]
    if opts["offset"] is not None:
        args += ["-s", str(opts["offset"])]
    if no_musicbrainz:
        args.append("-N")
    elif release:
        args += ["-R", release]
    if tags:
        args += tag_args(tags)
    if info_only:
        args.append("-I")
    else:
        if not opts["deemphasis"]:
            args.append("-W")
        if not opts["replaygain"]:
            args.append("-K")
        if opts["eject"]:
            args.append("-Q")
        if opts["dir_scheme"]:
            args += ["-D", opts["dir_scheme"]]
    args += opts["extra_args"]
    return args


@dataclass
class Preview:
    """What cyanrip -I said about the disc."""
    code: int
    tail: list[str]                  # the last lines, for an error message
    submission_url: str = ""         # MusicBrainz page to add this disc's ID
    disc_unknown: bool = False       # MusicBrainz has no release with this disc ID
    needs_tags: bool = False         # cyanrip will not rip without -a/-t, -R or -N
    tracks: tuple[int, int] | None = None  # first and last track number
    # Several releases share the disc ID: (release ID, cyanrip's description).
    # cyanrip does not rip until one is picked with -R.
    releases: list[tuple[str, str]] = field(default_factory=list)


# cyanrip's messages (src/musicbrainz.c, master b40c1f0). Matching its text
# is fragile, so missing them only hides the hints; the rip still works.
_SUBMISSION_URL = re.compile(r"https://musicbrainz\.org/cdtoc/attach\?\S+")
_TOC = re.compile(r"[?&]toc=(\d+)\+(\d+)\+")
_DISC_UNKNOWN = ("Unable to find", "matching stub", "isn't on MusicBrainz yet")
_NEEDS_TAGS = "add metadata via -a or -t"
_RELEASE_LINE = re.compile(r"^\d+ \(ID: ([0-9a-fA-F-]{36})\): (.+)$")
_MULTIPLE = "Multiple releases found"


def preview(cyanrip: str, cfg: dict[str, Any], *, device: str = "", release: str = "",
            no_musicbrainz: bool = False, tags: ManualTags | None = None) -> Preview:
    """Show the disc and its MusicBrainz match (cyanrip -I) on the terminal,
    and pick out what cdimport needs from it."""
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release,
                                   no_musicbrainz=no_musicbrainz, info_only=True, tags=tags)
    # The submission link comes early, followed by the track list and a QR
    # code, so keep all of cyanrip's output, not only the end.
    code, lines = proc.run_tail(cmd, lines=100_000, keep_bytes=1 << 20)
    return parse_preview(code, lines)


def parse_preview(code: int, lines: list[str]) -> Preview:
    text = "\n".join(lines)
    url = _SUBMISSION_URL.search(text)
    toc = _TOC.search(url.group(0)) if url else None
    return Preview(code, lines[-3:],
                   submission_url=url.group(0) if url else "",
                   disc_unknown=any(phrase in text for phrase in _DISC_UNKNOWN),
                   needs_tags=_NEEDS_TAGS in text,
                   tracks=(int(toc.group(1)), int(toc.group(2))) if toc else None,
                   releases=_releases(lines) if _MULTIPLE in text else [])


def _releases(lines: list[str]) -> list[tuple[str, str]]:
    """cyanrip's list of releases for the disc ID, e.g.
    "1 (ID: 5c580fb1-...): 때깔 (Killin’ It) (색깔 (Superb) ver.) (KR) (2024-01-08)"."""
    found = []
    for line in lines:
        match = _RELEASE_LINE.match(line)
        if match:
            found.append((match.group(1).lower(), match.group(2)))
    return found


def explain(lines: list[str]) -> str:
    """cyanrip's last lines, indented, for an error message."""
    return "".join(f"\n  cyanrip: {line}" for line in lines)


def new_staging_dir(work_dir: Path) -> Path:
    """A fresh folder for one rip.

    cyanrip names the album folder from metadata only it knows, so it rips
    into an empty folder of its own; whatever appears there is the album.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = work_dir / f"{STAGING_PREFIX}{stamp}"
    n = 1
    while path.exists():
        n += 1
        path = work_dir / f"{STAGING_PREFIX}{stamp}-{n}"
    path.mkdir(parents=True)
    return path


def lock_path(staging: Path) -> Path:
    """The lock file next to (not inside) a staging folder, so it never
    counts as something cyanrip left behind."""
    return staging.with_name(staging.name + ".lock")


@contextlib.contextmanager
def hold_lock(staging: Path) -> Iterator[None]:
    """Mark a staging folder as in use while cyanrip rips into it.

    Another cdimport started meanwhile must not take it for an interrupted
    rip and offer to delete it. The operating system drops the lock when
    this process ends in any way, so a crashed run's folder shows as free.
    """
    path = lock_path(staging)
    with open(path, "w", encoding="utf-8") as handle:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows: in_use() then says "cannot tell"
            fcntl = None
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


def in_use(staging: Path) -> bool | None:
    """Whether a running cdimport holds the staging folder; None when this
    platform cannot tell."""
    path = lock_path(staging)
    if not path.exists():
        return False
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        return None
    with open(path, "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def find_album_dir(staging: Path) -> Path:
    """The one folder in staging that holds the ripped tracks."""
    candidates = sorted({f.parent for f in staging.rglob("*")
                         if f.suffix.lower() in AUDIO_EXTENSIONS and f.is_file()})
    if not candidates:
        raise RipError(f"cyanrip finished, but no audio files were found in {staging}")
    if len(candidates) > 1:
        listed = "\n  ".join(str(c) for c in candidates)
        raise RipError(f"cyanrip created several folders with audio files:\n  {listed}")
    return candidates[0]


def unique_path(path: Path) -> Path:
    """path, or 'path (2)', 'path (3)', ... - never an existing one."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.name} ({n})")
        if not candidate.exists():
            return candidate
        n += 1


def remove_empty_dirs(root: Path) -> bool:
    """Remove root if it holds nothing but empty folders; True when it is gone.

    Never deletes a file: anything cyanrip left behind stays for the user.
    """
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if filenames:
            return False
        for d in dirnames:
            try:
                (Path(dirpath) / d).rmdir()
            except OSError:
                return False
    root.rmdir()
    return True


def rip(cyanrip: str, cfg: dict[str, Any], work_dir: Path, *, device: str = "",
        release: str = "", no_musicbrainz: bool = False,
        tags: ManualTags | None = None) -> tuple[Path, Path | None]:
    """Rip the disc into the working folder.

    Returns the album folder and, when cyanrip left other files next to it,
    the staging folder that still holds them.
    """
    staging = new_staging_dir(work_dir)
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release, no_musicbrainz=no_musicbrainz,
                                   tags=tags)
    with hold_lock(staging):
        code, tail = proc.run_tail(cmd, cwd=staging)
        if code != 0:
            reason = explain(tail) or "\n  (see cyanrip's output above)"
            if remove_empty_dirs(staging):
                raise RipError(f"cyanrip stopped (exit code {code}) before writing anything:{reason}")
            raise RipError(f"cyanrip failed (exit code {code}):{reason}\nPartial files are in {staging}")
        album = find_album_dir(staging)
        target = unique_path(work_dir / album.name)
        shutil.move(str(album), str(target))
        leftover = None if remove_empty_dirs(staging) else staging
    return target, leftover
