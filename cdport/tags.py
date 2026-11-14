"""Step 3: hand the tracks to a tag editor, then check what came back."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import container, proc
from .tools import Editor

AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".opus", ".m4a", ".wav", ".aiff", ".wv", ".tta"}

# lyrembed searches with both; a track missing either finds nothing.
REQUIRED_TAGS = ("artist", "title")


def audio_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)


def expand_command(template: list[str], folder: Path, files: list[Path]) -> list[str]:
    """Fill a command template: an argument that is exactly "{files}" becomes
    one argument per file; "{dir}" anywhere is replaced by the folder."""
    cmd: list[str] = []
    for arg in template:
        if arg == "{files}":
            cmd += [str(f) for f in files]
        else:
            cmd.append(arg.replace("{dir}", str(folder)))
    return cmd


def open_editor(editor: Editor, folder: Path) -> proc.Launched:
    """Start the editor on the album; raises OSError when it cannot start.
    A Flatpak runs on the host from a toolbox or distrobox."""
    return proc.launch(container.flatpak_on_host(expand_command(editor.command, folder, audio_files(folder))))


class TagReadError(Exception):
    """Tags cannot be read (mutagen missing, or a broken file)."""


# WAV and AIFF have no "easy" mode in mutagen: their tags arrive as ID3 frames.
_ID3_NAMES = {"TIT2": "title", "TPE1": "artist", "TPE2": "albumartist", "TALB": "album", "TRCK": "tracknumber",
              "TPOS": "discnumber", "TDRC": "date", "TCON": "genre",
              "TXXX:MusicBrainz Album Id": "musicbrainz_albumid"}


def read_tags(path: Path) -> dict[str, str]:
    """The file's tags with lower-case keys, first value only."""
    try:
        import mutagen
    except ImportError as exc:
        raise TagReadError("the Python package 'mutagen' is not installed") from exc
    try:
        audio = mutagen.File(path, easy=True)
    except Exception as exc:  # mutagen raises many unrelated types for broken files
        raise TagReadError(f"{path.name}: {exc}") from exc
    if audio is None or audio.tags is None:
        return {}
    tags = {}
    for key, value in audio.tags.items():
        if isinstance(value, list):
            value = value[0] if value else ""
        tags[_ID3_NAMES.get(key, key.lower())] = str(value).strip()
    return tags


def missing_tags(files: list[Path],
                 reader: Callable[[Path], dict[str, str]] = read_tags) -> list[tuple[Path, list[str]]]:
    """Tracks lacking a tag lyrembed needs, with the missing names."""
    problems = []
    for f in files:
        tags = reader(f)
        missing = [t for t in REQUIRED_TAGS if not tags.get(t)]
        if missing:
            problems.append((f, missing))
    return problems


def album_artist(files: list[Path], reader: Callable[[Path], dict[str, str]] = read_tags) -> str:
    """The album artist from the first track that has one, else its artist."""
    for f in files:
        tags = reader(f)
        for key in ("albumartist", "album_artist", "album artist", "artist"):
            if tags.get(key):
                return tags[key]
    return ""
