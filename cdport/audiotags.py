"""One way to read and write the tags of the formats cdport edits.

The tag editor and the cover step work on FLAC, MP3 (ID3), Ogg Vorbis, Opus
and M4A: the formats lyrembed tags, so an album edited here can get its
lyrics too. Each keeps its tags differently (Vorbis comments, ID3 frames,
MP4 atoms); a `TagFile` hides that behind the Vorbis names cdport uses
everywhere else ("title", "albumartist", "tracknumber", ...), as Picard maps
them. Tags without a field of their own in the editor ("other") are listed
by a readable name, the same in every format where Picard has one
("COMPOSER" is TCOM in an MP3 and ©wrt in an M4A), and can be changed but
not added.

No UI. Errors are `TagError`; nothing here swallows one.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

# The formats lyrembed tags (its SUPPORTED_EXTENSIONS); .ogg may also hold
# Speex or FLAC, which carry Vorbis comments too.
EXTENSIONS = (".flac", ".mp3", ".ogg", ".opus", ".m4a")
FRONT_COVER = 3  # picture type, in FLAC and ID3 alike
# The names the editor has fields for; never listed with the other tags.
OWN = ("title", "artist", "artists", "album", "albumartist", "albumartists", "date", "genre",
       "tracknumber", "discnumber")
# Vorbis spellings of the album artist other programs write.
ALBUM_ARTIST_SPELLINGS = ("albumartist", "album artist", "album_artist")
_VORBIS_LYRICS = ("lyrics", "unsyncedlyrics", "syncedlyrics")
_VORBIS_PICTURES = ("metadata_block_picture", "coverart", "coverartmime")
# ID3 stores dates as timestamps and drops what does not parse as one.
_ID3_TIMESTAMP = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?)?)?$")
_NUMBER = re.compile(r"^\d{1,5}(/\d{1,5})?$")


class TagError(Exception):
    """A file that cannot be read or written, or a value its format cannot hold."""


@dataclass
class Picture:
    type: int  # FRONT_COVER, or another of FLAC's/ID3's picture types
    mime: str
    data: bytes


def editable(files: list[Path]) -> list[Path]:
    """The files of a format cdport can tag, in their order."""
    return [f for f in files if f.suffix.lower() in EXTENSIONS]


def open_file(path: Path) -> "TagFile":
    """The file's tags; raises TagError for a broken file or another format."""
    try:
        import mutagen
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.mp4 import MP4
        from mutagen.ogg import OggFileType
    except ImportError as exc:
        raise TagError("the Python package 'mutagen' is not installed") from exc
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # mutagen raises many unrelated types for broken files
        raise TagError(f"{path.name} cannot be read: {exc}") from exc
    if isinstance(audio, FLAC):
        return FlacTags(path, audio)
    if isinstance(audio, OggFileType):
        return VorbisTags(path, audio)
    if isinstance(audio, MP3):
        return Id3Tags(path, audio)
    if isinstance(audio, MP4):
        return Mp4Tags(path, audio)
    raise TagError(f"{path.name} is not a FLAC, MP3, Ogg, Opus or M4A file cdport can tag.")


def front_cover(pictures: list[Picture]) -> Picture | None:
    """The front cover, else the first picture (what a player shows)."""
    front = [p for p in pictures if p.type == FRONT_COVER]
    return (front or pictures or [None])[0]


def value_problem(kind: str, key: str, value: str) -> str:
    """Why *value* cannot be saved under *key* in a *kind* file ("" if it
    can): checked before anything is written, as a format that cannot hold a
    value drops it or fails halfway through an album."""
    if not value:
        return ""
    if kind == "M4A" and key in ("tracknumber", "discnumber"):
        if not _NUMBER.match(value) or any(int(n) > 65535 for n in value.split("/")):
            return f"M4A files take only numbers as {_SAY[key]}, like 3 or 3/12."
    if kind == "MP3" and (key == "date" or key in _ID3_TIMESTAMP_NAMES):
        if not _ID3_TIMESTAMP.match(value):
            what = "a date" if key == "date" else key
            return f"MP3 files take {what} only as 2024, 2024-05 or 2024-05-17."
    return ""


_SAY = {"tracknumber": "track numbers", "discnumber": "disc numbers"}


class TagFile:
    """The tags of one file, under cdport's names. `set` and `set_other`
    change them in memory; `save` writes them."""

    kind = ""

    def __init__(self, path: Path, audio) -> None:
        self.path = path
        self.audio = audio

    def get(self, key: str) -> list[str]:
        raise NotImplementedError

    def set(self, key: str, values: list[str]) -> None:
        """Replace a tag of *OWN*; an empty list removes it."""
        raise NotImplementedError

    def other(self) -> dict[str, list[str]]:
        """Text tags without a field of their own, by their readable name;
        lyrics and pictures excluded (lyrembed's, and the cover step's)."""
        raise NotImplementedError

    def set_other(self, name: str, values: list[str]) -> None:
        """Replace a tag `other` listed; an empty list removes it."""
        raise NotImplementedError

    def has_lyrics(self) -> bool:
        raise NotImplementedError

    def pictures(self) -> list[Picture]:
        raise NotImplementedError

    def set_front_cover(self, data: bytes, mime: str) -> None:
        """Make *data* the only front cover; other pictures (back cover,
        booklet) stay."""
        raise NotImplementedError

    def save(self) -> None:
        try:
            self._save()
        except Exception as exc:  # mutagen raises many unrelated types
            raise TagError(f"Could not write {self.path.name}: {exc}") from exc

    def _save(self) -> None:
        self.audio.save()

    def _tags(self):
        if self.audio.tags is None:
            self.audio.add_tags()
        return self.audio.tags


def _clean(values) -> list[str]:
    return [str(v) for v in values or []]


# --- Vorbis comments: FLAC, Ogg Vorbis, Opus -------------------------------------------
class VorbisTags(TagFile):
    """Vorbis comments, as cdport's names are theirs; pictures as
    METADATA_BLOCK_PICTURE (the old COVERART counts as a front cover)."""

    kind = "Ogg"

    def __init__(self, path: Path, audio) -> None:
        super().__init__(path, audio)
        name = type(audio).__name__
        self.kind = "Opus" if "Opus" in name else "Ogg"

    def _get(self, key: str) -> list[str]:
        tags = self.audio.tags
        return _clean(tags.get(key)) if tags is not None and key in tags else []

    def get(self, key: str) -> list[str]:
        if key == "albumartist":
            for spelling in ALBUM_ARTIST_SPELLINGS:
                values = [v for v in self._get(spelling) if v.strip()]
                if values:
                    return values
            return []
        return self._get(key)

    def _set(self, key: str, values: list[str]) -> None:
        tags = self._tags()
        if values:
            tags[key] = list(values)
        elif key in tags:
            del tags[key]

    def set(self, key: str, values: list[str]) -> None:
        if key == "albumartist":  # one spelling, so no stale second one stays
            for spelling in ALBUM_ARTIST_SPELLINGS[1:]:
                self._set(spelling, [])
        self._set(key, values)

    def other(self) -> dict[str, list[str]]:
        tags = self.audio.tags
        keys = sorted({k.lower() for k in (tags.keys() if tags is not None else [])})
        skip = {*OWN, *ALBUM_ARTIST_SPELLINGS, *_VORBIS_LYRICS, *_VORBIS_PICTURES}
        return {k.upper(): self._get(k) for k in keys if k not in skip}

    def set_other(self, name: str, values: list[str]) -> None:
        self._set(name.lower(), values)

    def has_lyrics(self) -> bool:
        return any(v.strip() for k in _VORBIS_LYRICS for v in self._get(k))

    def pictures(self) -> list[Picture]:
        from mutagen.flac import Picture as FlacPicture

        found = []
        for raw in self._get("metadata_block_picture"):
            try:
                p = FlacPicture(base64.b64decode(raw))
            except Exception:  # a broken block is no picture; the others still count
                continue
            found.append(Picture(p.type, p.mime, p.data))
        for raw in self._get("coverart"):
            try:
                found.append(Picture(FRONT_COVER, (self._get("coverartmime") or [""])[0], base64.b64decode(raw)))
            except ValueError:
                continue
        return found

    def set_front_cover(self, data: bytes, mime: str) -> None:
        from mutagen.flac import Picture as FlacPicture

        keep = []
        for raw in self._get("metadata_block_picture"):
            try:
                if FlacPicture(base64.b64decode(raw)).type == FRONT_COVER:
                    continue
            except Exception:  # unreadable: not ours to drop
                pass
            keep.append(raw)
        self._set("coverart", [])
        self._set("coverartmime", [])
        self._set("metadata_block_picture", keep + [base64.b64encode(_flac_picture(data, mime).write()).decode("ascii")])


class FlacTags(VorbisTags):
    """FLAC: Vorbis comments, and pictures in blocks of their own."""

    kind = "FLAC"

    def __init__(self, path: Path, audio) -> None:
        TagFile.__init__(self, path, audio)

    def pictures(self) -> list[Picture]:
        return [Picture(p.type, p.mime, p.data) for p in self.audio.pictures]

    def set_front_cover(self, data: bytes, mime: str) -> None:
        keep = [p for p in self.audio.pictures if p.type != FRONT_COVER]
        self.audio.clear_pictures()
        for p in keep:
            self.audio.add_picture(p)
        self.audio.add_picture(_flac_picture(data, mime))


def _flac_picture(data: bytes, mime: str):
    from mutagen.flac import Picture as FlacPicture

    picture = FlacPicture()
    picture.type = FRONT_COVER
    picture.mime = mime
    picture.desc = ""
    picture.data = data
    return picture


# --- ID3: MP3 ------------------------------------------------------------------------
_ID3_OWN = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "date": "TDRC",
            "genre": "TCON", "tracknumber": "TRCK", "discnumber": "TPOS"}
_ID3_TXXX_OWN = {"artists": "ARTISTS", "albumartists": "ALBUMARTISTS"}  # as Picard writes them
# Picard's names for the frames it maps (picard/formats/id3.py); a frame
# not listed keeps its ID, a TXXX frame its description.
_ID3_NAMES = {
    "TCOM": "COMPOSER", "TEXT": "LYRICIST", "TPE3": "CONDUCTOR", "TPE4": "REMIXER", "TIT1": "GROUPING",
    "TIT3": "SUBTITLE", "TSST": "DISCSUBTITLE", "TBPM": "BPM", "TKEY": "KEY", "TPUB": "LABEL",
    "TSRC": "ISRC", "TCOP": "COPYRIGHT", "TENC": "ENCODEDBY", "TSSE": "ENCODERSETTINGS", "TMED": "MEDIA",
    "TLAN": "LANGUAGE", "TMOO": "MOOD", "TDOR": "ORIGINALDATE", "TDRL": "RELEASEDATE",
    "TOAL": "ORIGINALALBUM", "TOPE": "ORIGINALARTIST", "TSOP": "ARTISTSORT", "TSO2": "ALBUMARTISTSORT",
    "TSOA": "ALBUMSORT", "TSOT": "TITLESORT", "TSOC": "COMPOSERSORT", "TCMP": "COMPILATION",
}
_ID3_TIMESTAMP_NAMES = {"ORIGINALDATE", "RELEASEDATE", "TDEN", "TDTG"}


class Id3Tags(TagFile):
    """ID3 frames. Saved in the version the file had: 2.3 for players that
    read no 2.4, where several values become one, joined by "/"."""

    kind = "MP3"

    def __init__(self, path: Path, audio) -> None:
        super().__init__(path, audio)
        self._names: dict[str, str] | None = None

    def _txxx(self, desc: str) -> list:
        tags = self.audio.tags
        return [f for f in (tags.getall("TXXX") if tags is not None else []) if f.desc.upper() == desc]

    def get(self, key: str) -> list[str]:
        tags = self.audio.tags
        if key in _ID3_TXXX_OWN:
            return [v for f in self._txxx(_ID3_TXXX_OWN[key]) for v in _clean(f.text)]
        frame_id = _ID3_OWN.get(key)
        if frame_id is None or tags is None or frame_id not in tags:
            return []
        frame = tags[frame_id]
        return list(frame.genres) if frame_id == "TCON" else _clean(frame.text)

    def set(self, key: str, values: list[str]) -> None:
        from mutagen.id3 import TXXX, Frames

        tags = self._tags()
        if key in _ID3_TXXX_OWN:
            for frame in self._txxx(_ID3_TXXX_OWN[key]):
                del tags[frame.HashKey]
            if values:
                tags.add(TXXX(encoding=3, desc=_ID3_TXXX_OWN[key], text=list(values)))
            return
        frame_id = _ID3_OWN[key]
        tags.delall(frame_id)
        if values:
            tags.add(Frames[frame_id](encoding=3, text=list(values)))

    def _frames(self) -> dict[str, str]:
        """Readable name → the frame's key, for the text frames `other` lists."""
        if self._names is None:
            from mutagen.id3 import COMM, TXXX, TextFrame

            names: dict[str, str] = {}
            tags = self.audio.tags
            for key, frame in sorted((tags.items() if tags is not None else []), key=lambda kv: kv[0]):
                if isinstance(frame, TXXX):
                    if frame.desc.upper() in _ID3_TXXX_OWN.values():
                        continue
                    name = frame.desc
                elif isinstance(frame, COMM):
                    name = "COMMENT" + (f":{frame.desc}" if frame.desc else "")
                elif isinstance(frame, TextFrame):
                    if frame.FrameID in _ID3_OWN.values():
                        continue
                    name = _ID3_NAMES.get(frame.FrameID, frame.FrameID)
                else:
                    continue  # pictures, lyrics, IDs, binary data
                names[key if name in names else name] = key
            self._names = names
        return self._names

    def other(self) -> dict[str, list[str]]:
        tags = self.audio.tags
        return {name: _clean(tags[key].text) for name, key in self._frames().items()}

    def set_other(self, name: str, values: list[str]) -> None:
        from mutagen.id3 import COMM, TXXX, Frames

        key = self._frames().get(name)
        if key is None:
            raise TagError(f"{self.path.name} has no tag {name}.")
        tags = self._tags()
        frame = tags[key]
        del tags[key]
        if not values:
            return
        if isinstance(frame, TXXX):
            tags.add(TXXX(encoding=3, desc=frame.desc, text=list(values)))
        elif isinstance(frame, COMM):
            tags.add(COMM(encoding=3, lang=frame.lang, desc=frame.desc, text=list(values)))
        else:
            tags.add(Frames[frame.FrameID](encoding=3, text=list(values)))

    def has_lyrics(self) -> bool:
        tags = self.audio.tags
        if tags is None:
            return False
        return any(str(f).strip() for f in tags.getall("USLT")) or bool(tags.getall("SYLT"))

    def pictures(self) -> list[Picture]:
        tags = self.audio.tags
        return [Picture(int(f.type), f.mime, f.data) for f in (tags.getall("APIC") if tags is not None else [])]

    def set_front_cover(self, data: bytes, mime: str) -> None:
        from mutagen.id3 import APIC

        tags = self._tags()
        for frame in tags.getall("APIC"):
            if frame.type == FRONT_COVER:
                del tags[frame.HashKey]
        # Frames are told apart by their description: a back cover without
        # one would be replaced by a front cover without one.
        taken = {f.desc for f in tags.getall("APIC")}
        desc = next(d for d in ("", "Front cover", "Front cover (cdport)") if d not in taken)
        tags.add(APIC(encoding=3, mime=mime, type=FRONT_COVER, desc=desc, data=data))

    def _save(self) -> None:
        version = self.audio.tags.version[1] if self.audio.tags is not None else 4
        self.audio.save(v2_version=3 if version == 3 else 4)


# --- MP4 atoms: M4A ----------------------------------------------------------------------
_MP4_OWN = {"title": "©nam", "artist": "©ART", "album": "©alb", "albumartist": "aART", "date": "©day",
            "genre": "©gen"}
_MP4_NUMBERS = {"tracknumber": "trkn", "discnumber": "disk"}
_FREEFORM = "----:com.apple.iTunes:"
_MP4_FREEFORM_OWN = {"artists": _FREEFORM + "ARTISTS", "albumartists": _FREEFORM + "ALBUMARTISTS"}
_MP4_LYRICS = "©lyr"
# Picard's names for the atoms it maps (picard/formats/mp4.py).
_MP4_NAMES = {
    "©wrt": "COMPOSER", "©cmt": "COMMENT", "©grp": "GROUPING", "©too": "ENCODER", "cprt": "COPYRIGHT",
    "soar": "ARTISTSORT", "soaa": "ALBUMARTISTSORT", "soal": "ALBUMSORT", "sonm": "TITLESORT",
    "soco": "COMPOSERSORT", "©wrk": "WORK", "©mvn": "MOVEMENT", "desc": "DESCRIPTION",
}


class Mp4Tags(TagFile):
    """MP4 atoms. Track and disc numbers are pairs of numbers ("3/12");
    a cover has no type, so the first one is the front cover."""

    kind = "M4A"

    def __init__(self, path: Path, audio) -> None:
        super().__init__(path, audio)
        self._names: dict[str, str] | None = None

    def _raw(self, key: str) -> list:
        tags = self.audio.tags
        value = tags.get(key) if tags is not None else None
        if value is None:
            return []
        return list(value) if isinstance(value, list) else [value]  # flags (cpil) are a bare bool

    def get(self, key: str) -> list[str]:
        if key in _MP4_NUMBERS:
            return [f"{n}/{total}" if total else str(n) for n, total in self._raw(_MP4_NUMBERS[key])]
        if key in _MP4_FREEFORM_OWN:
            return _freeform_text(self._raw(_MP4_FREEFORM_OWN[key])) or []
        values = _clean(self._raw(_MP4_OWN[key])) if key in _MP4_OWN else []
        if key == "genre" and not values:  # the old numbered genre
            from mutagen.id3 import TCON

            values = [TCON(text=[f"({n - 1})"]).genres[0] for n in self._raw("gnre") if isinstance(n, int)]
        return values

    def set(self, key: str, values: list[str]) -> None:
        from mutagen.mp4 import AtomDataType, MP4FreeForm

        tags = self._tags()
        if key in _MP4_NUMBERS:
            atom = _MP4_NUMBERS[key]
            pairs = []
            for value in values:
                problem = value_problem("M4A", key, value)
                if problem:
                    raise TagError(problem)
                n, _, total = value.partition("/")
                pairs.append((int(n), int(total or 0)))
            self._put(tags, atom, pairs)
        elif key in _MP4_FREEFORM_OWN:
            self._put(tags, _MP4_FREEFORM_OWN[key],
                      [MP4FreeForm(v.encode("utf-8"), dataformat=AtomDataType.UTF8) for v in values])
        else:
            if key == "genre":
                self._put(tags, "gnre", [])
            self._put(tags, _MP4_OWN[key], list(values))

    @staticmethod
    def _put(tags, atom: str, values: list) -> None:
        if values:
            tags[atom] = values
        elif atom in tags:
            del tags[atom]

    def _atoms(self) -> dict[str, str]:
        """Readable name → atom, for the text atoms `other` lists."""
        if self._names is None:
            names: dict[str, str] = {}
            tags = self.audio.tags
            skip = {*_MP4_OWN.values(), *_MP4_NUMBERS.values(), *_MP4_FREEFORM_OWN.values(), _MP4_LYRICS, "gnre"}
            for key in sorted(tags.keys() if tags is not None else []):
                if key in skip:
                    continue
                values = self._raw(key)
                if key.startswith("----:"):
                    if _freeform_text(values) is None:
                        continue  # binary data, not text
                    name = key[len(_FREEFORM):] if key.startswith(_FREEFORM) else key
                elif values and all(isinstance(v, str) for v in values):
                    name = _MP4_NAMES.get(key, key)
                else:
                    continue  # covers, flags, numbers
                names[key if name in names else name] = key
            self._names = names
        return self._names

    def other(self) -> dict[str, list[str]]:
        result = {}
        for name, key in self._atoms().items():
            values = self._raw(key)
            result[name] = _freeform_text(values) if key.startswith("----:") else _clean(values)
        return result

    def set_other(self, name: str, values: list[str]) -> None:
        from mutagen.mp4 import AtomDataType, MP4FreeForm

        key = self._atoms().get(name)
        if key is None:
            raise TagError(f"{self.path.name} has no tag {name}.")
        if key.startswith("----:"):
            values = [MP4FreeForm(v.encode("utf-8"), dataformat=AtomDataType.UTF8) for v in values]
        self._put(self._tags(), key, list(values))

    def has_lyrics(self) -> bool:
        return any(str(v).strip() for v in self._raw(_MP4_LYRICS))

    def pictures(self) -> list[Picture]:
        from mutagen.mp4 import MP4Cover

        found = []
        for i, c in enumerate(self._raw("covr")):
            mime = "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            found.append(Picture(FRONT_COVER if i == 0 else 0, mime, bytes(c)))
        return found

    def set_front_cover(self, data: bytes, mime: str) -> None:
        from mutagen.mp4 import MP4Cover

        kind = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        self._put(self._tags(), "covr", [MP4Cover(data, imageformat=kind)] + self._raw("covr")[1:])


def _freeform_text(values: list) -> list[str] | None:
    """A freeform atom's values as text; None when they are not UTF-8 text."""
    from mutagen.mp4 import AtomDataType

    if any(getattr(v, "dataformat", AtomDataType.UTF8) != AtomDataType.UTF8 for v in values):
        return None
    try:
        return [bytes(v).decode("utf-8") for v in values]
    except UnicodeDecodeError:
        return None
