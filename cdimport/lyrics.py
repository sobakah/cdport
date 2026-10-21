"""Step 4: embed lyrics with lyrembed, run as a separate program."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import proc
from .tags import TagReadError

# lyrembed's exit codes. In batch mode (--auto) a track without a confident
# match counts as skipped, not failed: 0 also means "nothing embedded", and 1
# only says that files could not be read or written.
EXIT_OK = 0
EXIT_BATCH_FAILURES = 1
EXIT_UNUSABLE_INPUT = 2
EXIT_INTERRUPTED = 130

# The formats lyrembed tags (its tagging.SUPPORTED_EXTENSIONS); it leaves
# other files alone and gives up on a folder with none of these.
LYREMBED_EXTENSIONS = (".flac", ".mp3", ".ogg", ".opus", ".m4a")
# Where lyrembed writes lyrics, and what it reads as existing ones.
_VORBIS_LYRIC_KEYS = ("lyrics", "unsyncedlyrics", "syncedlyrics")
_MP4_LYRIC_KEY = "\xa9lyr"


def lyrembed_can_tag(path: Path) -> bool:
    return path.suffix.lower() in LYREMBED_EXTENSIONS


def without_lyrics(files: list[Path]) -> list[Path]:
    """The tracks that have no embedded lyrics.

    lyrembed's batch mode exits 0 also when it found nothing, so this is
    how cdimport learns which tracks are left. Raises TagReadError.
    """
    return [f for f in files if not has_lyrics(f)]


def has_lyrics(path: Path) -> bool:
    try:
        import mutagen
    except ImportError as exc:
        raise TagReadError("the Python package 'mutagen' is not installed") from exc
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # mutagen raises many unrelated types for broken files
        raise TagReadError(f"{path.name}: {exc}") from exc
    if audio is None or audio.tags is None:
        return False
    found = audio.tags
    if hasattr(found, "getall"):  # ID3
        return any(str(frame).strip() for frame in found.getall("USLT")) or bool(found.getall("SYLT"))
    values: list = []
    for key in _VORBIS_LYRIC_KEYS + (_MP4_LYRIC_KEY,):
        try:
            values += found.get(key) or []
        except (KeyError, ValueError):  # MP4 tags reject Vorbis keys and the other way round
            continue
    return any(str(v).strip() for v in values)


def lyrembed_command(lyrembed: str, cfg: dict[str, Any], folder: Path, mode: str,
                     romanize: bool | None = None) -> list[str]:
    """mode is "auto" (batch, never prompts) or "interactive".

    romanize applies to "auto" only; None leaves it to lyrembed's own
    settings.auto_romanize.
    """
    cmd = [lyrembed, str(folder)]
    if mode == "auto":
        cmd.append("--auto")
        if romanize is not None:
            cmd.append("--romanize" if romanize else "--no-romanize")
    return cmd + cfg["lyrembed"]["extra_args"]


def run_lyrembed(lyrembed: str, cfg: dict[str, Any], folder: Path, mode: str,
                 romanize: bool | None = None) -> int:
    return proc.run(lyrembed_command(lyrembed, cfg, folder, mode, romanize))


def opens_page(lyrembed: str) -> bool:
    """Whether this lyrembed works on a page in the browser (2.0.0 and newer)
    rather than in terminal menus. Either way cdimport starts `lyrembed DIR`
    and waits for it to end; only what to tell the user differs."""
    result = proc.capture([lyrembed, "--version"])
    match = re.search(r"lyrembed (\d+)\.", result.stdout if result is not None else "")
    return match is not None and int(match.group(1)) >= 2


def can_romanize(lyrembed: str) -> bool:
    """Whether this lyrembed has --romanize for batch mode (added after 1.0.0).

    Older versions reject the flag, so it is only offered when --help lists it.
    """
    result = proc.capture([lyrembed, "--help"])
    return result is not None and "--romanize" in result.stdout
