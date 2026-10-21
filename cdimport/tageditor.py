"""The page's tag editor: read and write an album's tags, and rename what follows them.

FLAC only, as cdimport rips. Several artists follow Picard: ARTIST holds
the credit as one line ("A feat. B"), ARTISTS the single names, and the same
for ALBUMARTIST and ALBUMARTISTS. cyanrip writes the credit only.

A changed title renames its file, and a changed album the folder, but only
where cyanrip built the name: "{track} - {title}" (with "{disc}." on
multi-disc albums) and "{album}..." with cyanrip's default ("unicode")
replacements for characters a file name cannot hold. Anything else keeps
its name; the editor says so. The .cue sheet names the track files, so its
FILE lines follow; the rip log is never changed, its checksum would fail.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import tags

# cyanrip's default sanitation (src/naming.c, crip_char_replacement, with
# CRIP_SANITIZE_UNICODE from cyanrip_main.c): look-alikes, and straight
# double quotes as alternating curly ones.
_CYANRIP_REPLACEMENTS = {"<": "‹", ">": "›", ":": "∶", "|": "│", "?": "？", "*": "∗", "/": "∕", "\\": "⧹"}
_TRACK_PREFIX = re.compile(r"^(?:\d+\.)?\d+ - ")
_CUE_FILE = re.compile(r'^(\s*FILE ")(.*)(".*)$')
ALBUM_ARTIST_KEYS = ("albumartist", "album artist", "album_artist")
_ALBUM_FIELDS = ("album", "albumArtist", "albumArtists", "date", "genre")


class TagEditError(Exception):
    """An edit that cannot be saved: nothing was written."""


def cyanrip_name(text: str) -> str:
    """*text* as cyanrip puts it into a file or folder name."""
    out, quote = [], 0
    for char in text:
        if char == '"':
            out.append("“" if quote == 0 else "”")
            quote ^= 1
        else:
            out.append(_CYANRIP_REPLACEMENTS.get(char, char))
    return "".join(out)


def credit_from(names: list[str]) -> str:
    """A credit line for names without join phrases: "A", "A & B", "A, B & C"."""
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " & " + names[-1]


def _flac(folder: Path) -> list[Path]:
    return [f for f in tags.audio_files(folder) if f.suffix.lower() == ".flac"]


def _open(path: Path):
    from mutagen.flac import FLAC

    try:
        return FLAC(path)
    except Exception as exc:  # mutagen raises many unrelated types for broken files
        raise TagEditError(f"{path.name} cannot be read: {exc}") from exc


def _values(audio, key: str) -> list[str]:
    return [str(v).strip() for v in (audio.get(key) or []) if str(v).strip()]


def _first(audio, key: str) -> str:
    values = _values(audio, key)
    return values[0] if values else ""


def _album_artist(audio) -> list[str]:
    for key in ALBUM_ARTIST_KEYS:
        values = _values(audio, key)
        if values:
            return values
    return []


def _track(path: Path, audio) -> dict:
    artist = _values(audio, "artist")
    # One ARTIST is a credit; several (some taggers write it so) are names.
    return {
        "file": path.name,
        "title": _first(audio, "title"),
        "artist": artist[0] if len(artist) == 1 else credit_from(artist),
        "artists": _values(audio, "artists") or (artist if len(artist) > 1 else []),
        "track": _first(audio, "tracknumber"),
        "disc": _first(audio, "discnumber"),
    }


def _album(audio) -> dict:
    album_artist = _album_artist(audio)
    return {
        "album": _first(audio, "album"),
        "albumArtist": album_artist[0] if len(album_artist) == 1 else credit_from(album_artist),
        "albumArtists": _values(audio, "albumartists") or (album_artist if len(album_artist) > 1 else []),
        "date": _first(audio, "date"),
        "genre": _first(audio, "genre"),
    }


def read_album(folder: Path) -> dict:
    """The album's tags for the editor: album fields from the first track
    (*differing* names those the tracks do not agree on), and each track."""
    files = tags.audio_files(folder)
    flac = _flac(folder)
    audios = [_open(f) for f in flac]
    albums = [_album(a) for a in audios]
    first = albums[0] if albums else {k: ([] if k == "albumArtists" else "") for k in _ALBUM_FIELDS}
    return {
        "folder": str(folder),
        "folderName": folder.name,
        **first,
        "differing": [k for k in _ALBUM_FIELDS if any(a[k] != first[k] for a in albums)],
        "tracks": [_track(f, a) for f, a in zip(flac, audios)],
        "others": len(files) - len(flac),
    }


# --- checking and planning an edit -----------------------------------------------
def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TagEditError(f"{what} must be text.")
    return value.strip()


def _names(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TagEditError(f"{what} must be a list of names.")
    return [v.strip() for v in value if v.strip()]


def _checked(folder: Path, edit: dict) -> tuple[dict, dict]:
    """The current tags and the edit, cleaned; raises for an edit that does
    not fit the folder or lacks what lyrembed searches with."""
    current = read_album(folder)
    if not isinstance(edit, dict) or not isinstance(edit.get("tracks"), list):
        raise TagEditError("The edit has no tracks.")
    clean = {
        "album": _text(edit.get("album", ""), "The album"),
        "albumArtist": _text(edit.get("albumArtist", ""), "The album artist"),
        "albumArtists": _names(edit.get("albumArtists", []), "The album artists"),
        "date": _text(edit.get("date", ""), "The date"),
        "genre": _text(edit.get("genre", ""), "The genre"),
        "tracks": [],
    }
    known = {t["file"] for t in current["tracks"]}
    given = {}
    for track in edit["tracks"]:
        if not isinstance(track, dict) or track.get("file") not in known:
            raise TagEditError("The edit names a file that is not in the album (anymore); reload the editor.")
        given[track["file"]] = {
            "file": track["file"],
            "title": _text(track.get("title", ""), "A title"),
            "artist": _text(track.get("artist", ""), "An artist"),
            "artists": _names(track.get("artists", []), "The artists"),
        }
    if set(given) != known:
        raise TagEditError("The edit does not cover every track; reload the editor.")
    for t in current["tracks"]:
        edited = given[t["file"]]
        if not edited["title"] or not edited["artist"]:
            number = t["track"] or t["file"]
            raise TagEditError(f"Track {number} needs a title and an artist: lyrembed searches with both.")
        clean["tracks"].append(edited)
    if not clean["album"]:
        raise TagEditError("The album needs a name.")
    return current, clean


def _renames(folder: Path, current: dict, edit: dict) -> tuple[list[dict], str | None]:
    """What a save would rename: each track file whose title changed, and the
    folder for a changed album. A name cyanrip did not build stays."""
    files = []
    for old, new in zip(current["tracks"], edit["tracks"]):
        entry = {"file": old["file"], "newFile": None, "kept": ""}
        if new["title"] != old["title"]:
            match = _TRACK_PREFIX.match(old["file"])
            suffix = Path(old["file"]).suffix
            if match and old["file"] == match.group(0) + cyanrip_name(old["title"]) + suffix:
                entry["newFile"] = match.group(0) + cyanrip_name(new["title"]) + suffix
            else:
                entry["kept"] = "its name was not made from the title by cyanrip"
        files.append(entry)
    folder_name = None
    if edit["album"] != current["album"] and current["album"]:
        prefix = cyanrip_name(current["album"])
        if folder.name.startswith(prefix):
            folder_name = cyanrip_name(edit["album"]) + folder.name[len(prefix):]
    return files, folder_name


def plan(folder: Path, edit: dict) -> dict:
    """The renames a save would make, for the editor to show while typing.
    An edit that cannot be saved yet still gets a plan, with its problem."""
    try:
        current, clean = _checked(folder, edit)
    except TagEditError as exc:
        return {"problem": str(exc), "files": [], "folderName": None}
    files, folder_name = _renames(folder, current, clean)
    return {"problem": "", "files": files, "folderName": folder_name}


# --- saving ------------------------------------------------------------------------
def _set(audio, key: str, values: list[str]) -> None:
    if values:
        audio[key] = values
    elif key in audio:
        del audio[key]


def save(folder: Path, edit: dict) -> dict:
    """Write the changed tags, then rename what follows them.

    Every rename is checked before anything is written, so an edit is saved
    whole or not at all (a file that cannot be written is the exception;
    the error names it)."""
    current, clean = _checked(folder, edit)
    files, folder_name = _renames(folder, current, clean)
    targets = [f["newFile"] for f in files if f["newFile"] and f["newFile"] != f["file"]]
    if len(set(targets)) != len(targets):
        raise TagEditError("Two tracks would get the same file name; give them different titles.")
    staying = {f["file"] for f in files if not (f["newFile"] and f["newFile"] != f["file"])}
    tracks = {f["file"] for f in files}
    for name in targets:
        # A target may be a track that moves away itself, never one that stays or another file.
        if name in staying or ((folder / name).exists() and name not in tracks):
            raise TagEditError(f"A file named '{name}' is already there.")
    new_folder = folder.parent / folder_name if folder_name and folder_name != folder.name else folder
    if new_folder != folder and new_folder.exists():
        raise TagEditError(f"A folder named '{new_folder.name}' is already there; choose another album name.")

    album_changed = {k: clean[k] != current[k] or k in current["differing"] for k in _ALBUM_FIELDS}
    changed = 0
    for path, old, new in zip(_flac(folder), current["tracks"], clean["tracks"]):
        audio = _open(path)
        touched = False
        if new["title"] != old["title"]:
            _set(audio, "title", [new["title"]])
            touched = True
        if new["artist"] != old["artist"] or new["artists"] != old["artists"]:
            _set(audio, "artist", [new["artist"]])
            _set(audio, "artists", new["artists"])
            touched = True
        if album_changed["album"]:
            _set(audio, "album", [clean["album"]])
            touched = True
        if album_changed["albumArtist"] or album_changed["albumArtists"]:
            for key in ALBUM_ARTIST_KEYS[1:]:  # one spelling, so no stale second one stays
                if key in audio:
                    del audio[key]
            _set(audio, "albumartist", [clean["albumArtist"]] if clean["albumArtist"] else [])
            _set(audio, "albumartists", clean["albumArtists"])
            touched = True
        for field, key in (("date", "date"), ("genre", "genre")):
            if album_changed[field]:
                _set(audio, key, [clean[field]] if clean[field] else [])
                touched = True
        if touched:
            try:
                audio.save()
            except Exception as exc:
                raise TagEditError(f"Could not write {path.name}: {exc}") from exc
            changed += 1

    # Through temporary names first: two tracks that swap titles would
    # otherwise overwrite each other's file.
    moving = [e for e in files if e["newFile"] and e["newFile"] != e["file"]]
    for i, entry in enumerate(moving):
        (folder / entry["file"]).rename(folder / f".cdimport-rename-{i}{Path(entry['file']).suffix}")
    renamed = []
    for i, entry in enumerate(moving):
        (folder / f".cdimport-rename-{i}{Path(entry['file']).suffix}").rename(folder / entry["newFile"])
        renamed.append([entry["file"], entry["newFile"]])
    if renamed:
        _follow_in_cue_sheets(folder, dict(renamed))
    folder_renamed = []
    if new_folder != folder:
        old_prefix, new_prefix = cyanrip_name(current["album"]), cyanrip_name(clean["album"])
        for side in sorted(folder.iterdir()):
            # cyanrip names them "{album}" and "{album} CD{disc}" (-L, -M).
            if side.suffix.lower() in (".cue", ".log") and side.stem.startswith(old_prefix):
                target = folder / (new_prefix + side.name[len(old_prefix):])
                if not target.exists():
                    side.rename(target)
                    folder_renamed.append([side.name, target.name])
        folder.rename(new_folder)
    return {
        "folder": str(new_folder),
        "changed": changed,
        "renamed": renamed + folder_renamed,
        "folderRenamed": new_folder != folder,
        "kept": [f["file"] for f in files if f["kept"]],
    }


def _follow_in_cue_sheets(folder: Path, renamed: dict[str, str]) -> None:
    """Point the cue sheets' FILE lines at the renamed track files."""
    for sheet in folder.glob("*.cue"):
        try:
            text = sheet.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # not one cyanrip wrote; better unchanged than garbled
        lines = []
        for line in text.splitlines(keepends=True):
            match = _CUE_FILE.match(line.rstrip("\r\n"))
            if match and match.group(2) in renamed:
                ending = line[len(line.rstrip("\r\n")):]
                line = match.group(1) + renamed[match.group(2)] + match.group(3) + ending
            lines.append(line)
        sheet.write_text("".join(lines), encoding="utf-8")
