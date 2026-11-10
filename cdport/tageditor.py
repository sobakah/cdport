"""The page's tag editor: read and write an album's tags, and rename what follows them.

FLAC only, as cdport rips. Several artists follow Picard: ARTIST holds
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

import hashlib
import os
import re
from pathlib import Path

from . import cover as cover_module, tags

# cyanrip's default sanitation (src/naming.c, crip_char_replacement, with
# CRIP_SANITIZE_UNICODE from cyanrip_main.c): look-alikes, and straight
# double quotes as alternating curly ones.
_CYANRIP_REPLACEMENTS = {"<": "‹", ">": "›", ":": "∶", "|": "│", "?": "？", "*": "∗", "/": "∕", "\\": "⧹"}
_TRACK_PREFIX = re.compile(r"^(?:\d+\.)?\d+ - ")
_CUE_FILE = re.compile(r'^(\s*FILE ")(.*)(".*)$')
ALBUM_ARTIST_KEYS = ("albumartist", "album artist", "album_artist")
_ALBUM_FIELDS = ("album", "albumArtist", "albumArtists", "date", "genre")
# Tags with a field of their own in the editor; every other tag is listed as
# it is in the file ("other"), lyrics excepted: lyrembed's business, and far
# too long for a field.
_OWN_FIELDS = {"title", "artist", "artists", "album", "albumartists", "date", "genre",
               "tracknumber", "discnumber", *ALBUM_ARTIST_KEYS}
_LYRICS_KEYS = {"lyrics", "unsyncedlyrics", "syncedlyrics"}
FRONT_COVER = 3  # FLAC picture type
NAME_MAX = 255  # bytes in a file name, on Linux file systems


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


def _other(audio) -> dict[str, list[str]]:
    """The tags without a field of their own, by their name in capitals."""
    keys = sorted({k.lower() for k in (audio.tags.keys() if audio.tags else [])})
    return {k.upper(): [str(v) for v in audio.get(k) or []]
            for k in keys if k not in _OWN_FIELDS and k not in _LYRICS_KEYS}


def _picture(audio) -> bytes:
    """The embedded front cover, else the first picture; b"" without one."""
    pictures = list(getattr(audio, "pictures", []) or [])
    front = [p for p in pictures if p.type == FRONT_COVER]
    chosen = (front or pictures)[:1]
    return chosen[0].data if chosen else b""


def _track(path: Path, audio) -> dict:
    artist = _values(audio, "artist")
    picture = _picture(audio)
    # One ARTIST is a credit; several (some taggers write it so) are names.
    return {
        "file": path.name,
        "title": _first(audio, "title"),
        "artist": artist[0] if len(artist) == 1 else credit_from(artist),
        "artists": _values(audio, "artists") or (artist if len(artist) > 1 else []),
        "track": _first(audio, "tracknumber"),
        "disc": _first(audio, "discnumber"),
        "cover": hashlib.sha1(picture).hexdigest()[:12] if picture else "",
        "hasLyrics": any(_values(audio, k) for k in _LYRICS_KEYS),
        "other": _other(audio),
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
    tracks = [_track(f, a) for f, a in zip(flac, audios)]
    # A tag every track has with the same value is the album's (shown once);
    # the rest stay with their tracks.
    shared = {}
    if len(tracks) > 1:  # with one track, nothing says a tag is the album's
        for key, values in tracks[0]["other"].items():
            if all(t["other"].get(key) == values for t in tracks):
                shared[key] = values
    for t in tracks:
        t["other"] = {k: v for k, v in t["other"].items() if k not in shared}
    covers = {t["cover"] for t in tracks}
    return {
        "folder": str(folder),
        "folderName": folder.name,
        **first,
        "differing": [k for k in _ALBUM_FIELDS if any(a[k] != first[k] for a in albums)],
        "other": shared,
        # One cover in every track is the album's.
        "cover": covers.pop() if len(covers) == 1 else "",
        "tracks": tracks,
        "others": len(files) - len(flac),
    }


def cover(folder: Path, name: str) -> tuple[bytes, str]:
    """The embedded cover of track *name* and its type, for the editor."""
    path = next((f for f in _flac(folder) if f.name == name), None)
    if path is None:
        raise TagEditError("No such track in the album.")
    data = _picture(_open(path))
    try:
        return data, cover_module.image_type(data)[0]
    except cover_module.CoverError as exc:
        raise TagEditError("The track has no cover the page can show.") from exc


COVER_FILES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png")


def album_cover(folder: Path) -> tuple[bytes, str]:
    """A picture of the album for the page (the start's albums, the album
    bar): the first track's embedded cover, else a cover file next to it.
    Only JPEG and PNG. Raises TagEditError when there is none."""
    for path in _flac(folder)[:1]:
        data = _picture(_open(path))
        try:
            return data, cover_module.image_type(data)[0]
        except cover_module.CoverError:
            pass  # none embedded, or not one the page shows: try the files
    for name in COVER_FILES:
        try:
            data = (folder / name).read_bytes()
        except OSError:
            continue
        try:
            return data, cover_module.image_type(data)[0]
        except cover_module.CoverError:
            continue
    raise TagEditError("The album has no cover the page can show.")


# --- checking and planning an edit -----------------------------------------------
def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TagEditError(f"{what} must be text.")
    return value.strip()


def _names(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TagEditError(f"{what} must be a list of names.")
    return [v.strip() for v in value if v.strip()]


def _others(value: object, known: dict[str, list[str]], where: str) -> dict[str, list[str]]:
    """Edited values of tags the file has; no new names, so a typo cannot
    add a stray tag. An empty list removes the tag."""
    if value is None:
        return dict(known)
    if not isinstance(value, dict):
        raise TagEditError(f"The tags of {where} must be name: values.")
    clean = {}
    for key, values in value.items():
        if key not in known:
            raise TagEditError(f"{where} has no tag {key}; only the file's tags can be changed here.")
        clean[key] = _names(values, f"{key} of {where}")
    return {**known, **clean}


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
        "other": _others(edit.get("other"), current["other"], "the album"),
        "tracks": [],
    }
    known = {t["file"] for t in current["tracks"]}
    given = {}
    for track in edit["tracks"]:
        if not isinstance(track, dict) or track.get("file") not in known:
            raise TagEditError("The edit names a file that is not in the album (anymore); reload the editor.")
        old = next(t for t in current["tracks"] if t["file"] == track["file"])
        given[track["file"]] = {
            "file": track["file"],
            "title": _text(track.get("title", ""), "A title"),
            "artist": _text(track.get("artist", ""), "An artist"),
            "artists": _names(track.get("artists", []), "The artists"),
            "track": _text(track.get("track", old["track"]), "A track number"),
            "disc": _text(track.get("disc", old["disc"]), "A disc number"),
            "other": _others(track.get("other"), old["other"], track["file"]),
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


def _too_long(names: list[str]) -> None:
    """Raise for a new name the file system cannot hold (255 bytes on
    Linux), before anything is written."""
    for name in names:
        if len(os.fsencode(name)) > NAME_MAX:
            raise TagEditError(f"'{name[:40]}…' is too long for a file name; shorten the title or album.")


def _new_names(files: list[dict], folder_name: str | None) -> list[str]:
    return [f["newFile"] for f in files if f["newFile"]] + ([folder_name] if folder_name else [])


def _sidecars(folder: Path, old_album: str, new_album: str) -> list[tuple[Path, Path]]:
    """The .cue and .log files that follow a renamed album: cyanrip names
    them "{album}" and "{album} CD{disc}" (-L, -M)."""
    old_prefix, new_prefix = cyanrip_name(old_album), cyanrip_name(new_album)
    return [(side, folder / (new_prefix + side.name[len(old_prefix):]))
            for side in sorted(folder.iterdir())
            if side.suffix.lower() in (".cue", ".log") and side.stem.startswith(old_prefix)]


def _same(a: Path, b: Path) -> bool:
    """Whether two names are one file: on a case-insensitive file system
    (exFAT, NTFS, SMB, Windows) "opening" and "Opening" are."""
    try:
        return a.samefile(b)
    except OSError:
        return False


def plan(folder: Path, edit: dict) -> dict:
    """The renames a save would make, for the editor to show while typing.
    An edit that cannot be saved yet still gets a plan, with its problem."""
    try:
        current, clean = _checked(folder, edit)
    except TagEditError as exc:
        return {"problem": str(exc), "files": [], "folderName": None}
    files, folder_name = _renames(folder, current, clean)
    try:
        _too_long(_new_names(files, folder_name))
    except TagEditError as exc:
        return {"problem": str(exc), "files": files, "folderName": folder_name}
    return {"problem": "", "files": files, "folderName": folder_name}


# --- saving ------------------------------------------------------------------------
def _set(audio, key: str, values: list[str]) -> None:
    if values:
        audio[key] = values
    elif key in audio:
        del audio[key]


def save(folder: Path, edit: dict) -> dict:
    """Rename what follows the tags, then write the tags.

    Every rename and file is checked before anything changes, so an edit is
    saved whole or not at all. A rename or write that fails anyway puts the
    names back; only a write that fails partway leaves earlier tracks written
    (the error names the file)."""
    current, clean = _checked(folder, edit)
    files, folder_name = _renames(folder, current, clean)
    targets = [f["newFile"] for f in files if f["newFile"] and f["newFile"] != f["file"]]
    if len(set(targets)) != len(targets):
        raise TagEditError("Two tracks would get the same file name; give them different titles.")
    staying = {f["file"] for f in files if not (f["newFile"] and f["newFile"] != f["file"])}
    moving = {f["file"] for f in files} - staying
    for name in targets:
        # A target may be a track that moves away itself (also by a change of
        # case only), never one that stays or another file.
        target = folder / name
        if name in staying or (target.exists() and not any(_same(target, folder / m) for m in moving)):
            raise TagEditError(f"A file named '{name}' is already there.")
    new_folder = folder.parent / folder_name if folder_name and folder_name != folder.name else folder
    if new_folder != folder and new_folder.exists() and not _same(new_folder, folder):
        raise TagEditError(f"A folder named '{new_folder.name}' is already there; choose another album name.")
    sidecars = _sidecars(folder, current["album"], clean["album"]) if new_folder != folder else []
    _too_long(_new_names(files, folder_name) + [target.name for _, target in sidecars])

    for path in _flac(folder):
        if not os.access(path, os.W_OK):
            raise TagEditError(f"{path.name} cannot be written.")

    # The names first: a rename that follows the tags cannot be tried again,
    # as the next save finds the title unchanged. Through temporary names:
    # two tracks that swap titles would otherwise overwrite each other's file.
    moving = [e for e in files if e["newFile"] and e["newFile"] != e["file"]]
    done: list[tuple[Path, Path]] = []
    renamed: list[list[str]] = []
    folder_renamed: list[list[str]] = []
    try:
        for i, entry in enumerate(moving):
            _move(folder / entry["file"], folder / f".cdport-rename-{i}{Path(entry['file']).suffix}", done)
        for i, entry in enumerate(moving):
            _move(folder / f".cdport-rename-{i}{Path(entry['file']).suffix}", folder / entry["newFile"], done)
    except OSError as exc:
        raise TagEditError(f"Renaming the files failed ({exc}); nothing was saved.{_undo(done)}") from exc
    renamed = [[entry["file"], entry["newFile"]] for entry in moving]
    try:
        if renamed:
            _follow_in_cue_sheets(folder, dict(renamed))
        if new_folder != folder:
            try:
                for side, target in sidecars:
                    if target.exists() and not _same(target, side):
                        continue  # never over another file
                    _rename(side, target, done)
                    folder_renamed.append([side.name, target.name])
                _rename(folder, new_folder, done)
            except OSError as exc:
                raise TagEditError(f"Renaming the folder failed ({exc}); nothing was saved.") from exc
        album_changed = {k: clean[k] != current[k] or k in current["differing"] for k in _ALBUM_FIELDS}
        changed = 0
        for entry, old, new in zip(files, current["tracks"], clean["tracks"]):
            audio = _open(new_folder / (entry["newFile"] or entry["file"]))
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
            for field, key in (("track", "tracknumber"), ("disc", "discnumber")):
                if new[field] != old[field]:
                    _set(audio, key, [new[field]] if new[field] else [])
                    touched = True
            # The album's shared tags go to every track, a track's own to it alone.
            for key, values in clean["other"].items():
                if values != current["other"].get(key):
                    _set(audio, key.lower(), values)
                    touched = True
            for key, values in new["other"].items():
                if values != old["other"].get(key):
                    _set(audio, key.lower(), values)
                    touched = True
            if touched:
                try:
                    audio.save()
                except Exception as exc:
                    raise TagEditError(f"Could not write {entry['file']}: {exc}") from exc
                changed += 1

    except TagEditError as exc:
        # A write can fail partway; tracks already written keep their tags.
        raise TagEditError(f"{exc}{_put_back(folder, done, renamed)}") from exc
    return {
        "folder": str(new_folder),
        "changed": changed,
        "renamed": renamed + folder_renamed,
        "folderRenamed": new_folder != folder,
        "kept": [f["file"] for f in files if f["kept"]],
    }


def _move(source: Path, target: Path, done: list[tuple[Path, Path]]) -> None:
    source.rename(target)
    done.append((source, target))


def _rename(source: Path, target: Path, done: list[tuple[Path, Path]]) -> None:
    """A rename that also works when only the case changes: some
    case-insensitive file systems ignore that, so it goes through a
    temporary name, short so that a name near the limit still fits."""
    if _same(source, target):
        temporary = source.with_name(f".cdport-rename-{len(done)}")
        _move(source, temporary, done)
        source = temporary
    _move(source, target, done)


def _undo(done: list[tuple[Path, Path]]) -> str:
    """Put renamed files back, newest first; what stayed under the new name,
    for the error message, or ""."""
    stuck = []
    for source, target in reversed(done):
        try:
            target.rename(source)
        except OSError:
            stuck.append(target.name)
    return f" These could not be renamed back: {', '.join(stuck)}." if stuck else ""


def _put_back(folder: Path, done: list[tuple[Path, Path]], renamed: list[list[str]]) -> str:
    """Undo a save's renames and what the cue sheets say about them; what
    could not be undone, for the error message, or "". """
    stuck = _undo(done)
    if not renamed:
        return stuck
    try:
        _follow_in_cue_sheets(folder, {new: old for old, new in renamed})
    except TagEditError as exc:
        return f"{stuck} {exc}"
    return stuck


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
        try:
            sheet.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            raise TagEditError(f"{sheet.name} could not be updated ({exc}); nothing was saved.") from exc
