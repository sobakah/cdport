"""Step 2: rip the disc with cyanrip."""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import proc, ui
from .config import STAGING_PREFIX
from .tags import AUDIO_EXTENSIONS


# What cyanrip measures over all tracks of a run (crip_replaygain_meta_album).
ALBUM_GAIN_TAGS = ("REPLAYGAIN_ALBUM_GAIN", "REPLAYGAIN_ALBUM_RANGE", "REPLAYGAIN_ALBUM_PEAK", "R128_ALBUM_GAIN")


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
                 tags: ManualTags | None = None, paranoia: str = "",
                 tracks: list[int] | None = None, eject: bool | None = None) -> list[str]:
    """cyanrip options from the config and this run's choices (without the program).

    paranoia is cyanrip's -P ("none", "max"; empty: cyanrip's default, max),
    tracks limits the rip to those track numbers (-l), and eject overrides
    the config's eject (a fast rip must not eject before it is checked).
    """
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
        if opts["eject"] if eject is None else eject:
            args.append("-Q")
        if paranoia:
            args += ["-P", paranoia]
        if tracks:
            args += ["-l", ",".join(str(n) for n in tracks)]
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
    # cyanrip's "AccurateRip:" line: found, not found, mismatch, error,
    # disabled; "" when the output was not seen. Only "found" lets a fast rip
    # be checked track by track.
    accuraterip: str = ""


# cyanrip's messages (src/musicbrainz.c, master b40c1f0). Matching its text
# is fragile, so missing them only hides the hints; the rip still works.
_SUBMISSION_URL = re.compile(r"https://musicbrainz\.org/cdtoc/attach\?\S+")
_TOC = re.compile(r"[?&]toc=(\d+)\+(\d+)\+")
_DISC_UNKNOWN = ("Unable to find", "matching stub", "isn't on MusicBrainz yet")
_NEEDS_TAGS = "add metadata via -a or -t"
_RELEASE_LINE = re.compile(r"^\d+ \(ID: ([0-9a-fA-F-]{36})\): (.+)$")
_MULTIPLE = "Multiple releases found"
_ACCURATERIP = re.compile(r"^AccurateRip:\s+(\S.*?)\s*$", re.MULTILINE)


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
                   releases=_releases(lines) if _MULTIPLE in text else [],
                   accuraterip=ar.group(1) if (ar := _ACCURATERIP.search(text)) else "")


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


def lock_path(staging: Path) -> Path:
    """The lock file next to (not inside) a staging folder, so it never
    counts as something cyanrip left behind."""
    return staging.with_name(staging.name + ".lock")


@contextlib.contextmanager
def staging_dir(work_dir: Path) -> Iterator[Path]:
    """A fresh folder for one rip, marked as in use until the rip is done.

    cyanrip names the album folder from metadata only it knows, so it rips
    into an empty folder of its own; whatever appears there is the album.
    Another cdimport started meanwhile must not take it for an interrupted
    rip and offer to delete it, so the lock is taken before the folder
    exists: no moment shows the folder unlocked. The operating system drops
    the lock when this process ends in any way, so a crashed run's folder
    shows as free.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: in_use() then says "cannot tell"
        fcntl = None
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for n in itertools.count(1):
        path = work_dir / (f"{STAGING_PREFIX}{stamp}" + (f"-{n}" if n > 1 else ""))
        if path.exists():
            continue
        with open(lock_path(path), "a", encoding="utf-8") as handle:
            if fcntl:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue  # another cdimport took this name in the same second
            try:
                path.mkdir()
            except FileExistsError:
                continue
            handle.truncate(0)
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            try:
                yield path
            finally:
                lock_path(path).unlink(missing_ok=True)
            return


def in_use(staging: Path) -> bool | None:
    """Whether a running cdimport holds the staging folder; None when this
    platform cannot tell."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        return None
    # Read-only: opening to write would create the file again when the rip
    # just ended and removed it, and leave it behind.
    try:
        handle = open(lock_path(staging), encoding="utf-8")
    except FileNotFoundError:
        return False
    with handle:
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
        tags: ManualTags | None = None, paranoia: str = "",
        eject: bool | None = None) -> tuple[Path, Path | None]:
    """Rip the disc into the working folder.

    Returns the album folder and, when cyanrip left other files next to it,
    the staging folder that still holds them.
    """
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release, no_musicbrainz=no_musicbrainz,
                                   tags=tags, paranoia=paranoia, eject=eject)
    with staging_dir(work_dir) as staging:
        _run_into(cmd, staging)
        album = find_album_dir(staging)
        target = unique_path(work_dir / album.name)
        shutil.move(str(album), str(target))
        leftover = None if remove_empty_dirs(staging) else staging
    return target, leftover


def _run_into(cmd: list[str], staging: Path) -> None:
    """Run cyanrip in the staging folder; RipError with its reason if it fails."""
    code, tail = proc.run_tail(cmd, cwd=staging)
    if code != 0:
        reason = explain(tail) or "\n  (see cyanrip's output above)"
        if remove_empty_dirs(staging):
            raise RipError(f"cyanrip stopped (exit code {code}) before writing anything:{reason}")
        raise RipError(f"cyanrip failed (exit code {code}):{reason}\nPartial files are in {staging}")


# A track's section in cyanrip's log starts with this line (src/cyanrip_main.c)
# and holds its AccurateRip result, e.g. "Accurip v2:  2BDE5557 (accurately
# ripped, confidence 71)" (src/cyanrip_log.c, master b40c1f0).
_TRACK_DONE = re.compile(r"^Track (\d+) ripped and encoded (successfully!|with errors\.)", re.MULTILINE)
_ACCURATE = "(accurately ripped"


def accuraterip_results(log_text: str) -> dict[int, bool]:
    """Track number -> whether AccurateRip confirms it, from a rip log.

    A track ripped with errors, or only partially matching, counts as not
    confirmed: those are the ones a fast rip has to read again securely.
    """
    marks = list(_TRACK_DONE.finditer(log_text))
    results = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(log_text)
        results[int(mark.group(1))] = mark.group(2).startswith("successfully") and \
            _ACCURATE in log_text[mark.end():end]
    return results


def rip_log(album: Path) -> Path | None:
    """The rip log cyanrip wrote into the album folder (the newest, if several)."""
    logs = sorted(album.glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


SECURE_LOG_SUFFIX = " (secure re-rip)"


def rerip(cyanrip: str, cfg: dict[str, Any], album: Path, work_dir: Path, tracks: list[int], *,
          device: str = "", release: str = "", no_musicbrainz: bool = False,
          tags: ManualTags | None = None, eject: bool | None = None
          ) -> tuple[list[Path], Path | None, Path | None]:
    """Rip tracks again with full paranoia and put them in place of the fast ones.

    Same release, tags and naming as the first pass, so cyanrip gives each
    track the same file name; a file whose name is not in the album stays in
    the staging folder instead of joining the album as an extra track.
    Returns the replaced files, the re-rip's log (kept next to the first
    one as "<name> (secure re-rip).log") and the staging folder if cyanrip
    left anything else there (as rip() does), else None.
    """
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release, no_musicbrainz=no_musicbrainz,
                                   tags=tags, paranoia="max", tracks=tracks, eject=eject)
    replaced: list[Path] = []
    log = None
    with staging_dir(work_dir) as staging:
        _run_into(cmd, staging)
        new = find_album_dir(staging)
        for f in sorted(new.iterdir()):
            target = album / f.name
            if f.suffix.lower() in AUDIO_EXTENSIONS and target.exists():
                _keep_album_gain(target, f)
                os.replace(f, target)
                replaced.append(target)
            elif f.suffix.lower() == ".log":
                log = _numbered(album / f"{f.stem}{SECURE_LOG_SUFFIX}{f.suffix}")
                shutil.move(str(f), str(log))
            elif f.suffix.lower() not in AUDIO_EXTENSIONS and target.exists():
                # The cue sheet and cover art of this partial rip; the album
                # already has the complete ones.
                f.unlink()
        leftover = None if remove_empty_dirs(staging) else staging
    return replaced, log, leftover


def _keep_album_gain(fast: Path, secure: Path) -> None:
    """Give a re-ripped track the album values of the fast rip's: a partial
    rip measures only its own tracks as "the album", and the tracks of one
    album would disagree. Its track values are the secure rip's own."""
    from mutagen import MutagenError
    from mutagen.flac import FLAC

    if secure.suffix.lower() != ".flac":
        return
    try:
        old, new = FLAC(fast), FLAC(secure)
        for key in ALBUM_GAIN_TAGS:
            if key in old:
                new[key] = old[key]
            elif key in new:
                del new[key]
        new.save()
    except (MutagenError, OSError) as exc:
        ui.warn(f"{secure.name} keeps the album loudness of its own rip: {exc}")


def _numbered(path: Path) -> Path:
    """path, or 'name (2).ext', ... when a re-rip of the same album ran before."""
    n = 1
    candidate = path
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
    return candidate
