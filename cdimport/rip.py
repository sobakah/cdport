"""Step 2: rip the disc with cyanrip."""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from . import proc
from .config import STAGING_PREFIX
from .tags import AUDIO_EXTENSIONS


class RipError(Exception):
    """The rip failed or its result cannot be found."""


def cyanrip_args(cfg: dict[str, Any], *, device: str = "", release: str = "",
                 no_musicbrainz: bool = False, info_only: bool = False) -> list[str]:
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


def preview(cyanrip: str, cfg: dict[str, Any], *, device: str = "", release: str = "",
            no_musicbrainz: bool = False) -> tuple[int, list[str]]:
    """Show the disc and its MusicBrainz match (cyanrip -I) on the terminal;
    returns the exit code and cyanrip's last lines."""
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release,
                                   no_musicbrainz=no_musicbrainz, info_only=True)
    return proc.run_tail(cmd)


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
        release: str = "", no_musicbrainz: bool = False) -> tuple[Path, Path | None]:
    """Rip the disc into the working folder.

    Returns the album folder and, when cyanrip left other files next to it,
    the staging folder that still holds them.
    """
    staging = new_staging_dir(work_dir)
    cmd = [cyanrip] + cyanrip_args(cfg, device=device, release=release, no_musicbrainz=no_musicbrainz)
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
