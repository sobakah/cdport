"""ISRCs from MusicBrainz for the tracks of a new rip.

cyanrip writes an ISRC only when the disc carries one, which few discs do.
Jewelcase and Jewelcase Mobile match Spotify songs to library files by ISRC
first, and Jewelcase never writes music files, so cdport adds them while the
album is new: one request for the release cyanrip tagged the tracks with
(`release/<id>?inc=recordings+isrcs`), and each track without an ISRC gets
all of its recording's ISRCs: a recording often has several (a reissue,
another label), a Spotify copy may carry any of them, and Jewelcase matches
by any. An ISRC a file already has (the disc's) is never replaced.

No UI. A failed lookup is `cover.CoverError` (`cover.NotFound` for a
release MusicBrainz does not have); a file that cannot be read or written
is reported as a problem, not raised.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import audiotags, cover

_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")
_NUMBER = re.compile(r"^\s*(\d+)")
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# MusicBrainz refusing (503; Picard working at the same time is enough):
# waits before asking again. Longer than the cover lookups', where someone
# waits at the page: after a rip, only the ISRCs wait.
BUSY_WAITS = (10.0, 30.0, 60.0, 120.0)


@dataclass
class ReleaseTrack:
    """A track of the release on MusicBrainz."""
    disc: int        # the medium's position
    number: int      # the track's position on it
    recording: str   # recording MBID
    isrcs: list[str]  # the recording's ISRCs, valid ones, in MusicBrainz's order
    title: str = ""
    # The number printed on the release, when it is a plain one: it goes on
    # across discs where the position starts again (disc 2 from 6).
    printed: int = 0


@dataclass
class Track:
    """What a file says about itself, as far as the ISRCs need it."""
    path: Path
    release: str = ""     # MUSICBRAINZ_ALBUMID
    recording: str = ""   # MUSICBRAINZ_TRACKID (Picard's name for the recording)
    title: str = ""
    disc: int | None = None
    number: int | None = None
    isrcs: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """What `match` found for the tracks of one release."""
    write: dict[Path, list[str]] = field(default_factory=dict)  # file -> ISRCs
    without: list[Path] = field(default_factory=list)      # MusicBrainz has none for them
    unmatched: list[Path] = field(default_factory=list)    # no track of the release fits


def is_isrc(value: str) -> bool:
    return bool(_ISRC.match(value))


def _normal(name: str) -> str:
    # Vorbis "MUSICBRAINZ_ALBUMID", Picard's ID3/MP4 "MusicBrainz Album Id",
    # FFmpeg's ID3 "musicbrainz_albumid": the same tag.
    return re.sub(r"[\s_]", "", name).upper()


def _first(other: dict[str, list[str]], name: str) -> str:
    for key, values in other.items():
        if _normal(key) == name:
            for value in values:
                if value.strip():
                    return _mbid(value)
    return ""


def _mbid(value: str) -> str:
    """The MusicBrainz ID in a tag's value. A rip was found with
    "MUSICBRAINZ_ALBUMID=<id>" as its value; anything without an ID stays."""
    match = _MBID.search(value)
    return (match.group(0) if match else value.strip()).lower()


def _number(values: list[str]) -> int | None:
    """3 from "3" or "3/12"."""
    match = _NUMBER.match(values[0]) if values else None
    return int(match.group(1)) if match else None


def read(files: list[Path]) -> tuple[list[Track], list[str]]:
    """The files of a format cdport tags, and what could not be read."""
    tracks, problems = [], []
    for path in audiotags.editable(files):
        try:
            tags = audiotags.open_file(path)
            other = tags.other()
            tracks.append(Track(path, release=_first(other, "MUSICBRAINZALBUMID"),
                                recording=_first(other, "MUSICBRAINZTRACKID"),
                                disc=_number(tags.get("discnumber")),
                                number=_number(tags.get("tracknumber")),
                                title=(tags.get("title") or [""])[0],
                                isrcs=tags.isrcs()))
        except audiotags.TagError as exc:
            problems.append(str(exc))
    return tracks, problems


def releases_to_look_up(tracks: list[Track]) -> list[str]:
    """The releases whose tracks still lack an ISRC; none when every track
    has one, so a disc with its own ISRCs costs no request."""
    return sorted({t.release for t in tracks if t.release and not t.isrcs})


def fetch(release_id: str, on_busy=None) -> list[ReleaseTrack]:
    """The release's tracks with their recordings' ISRCs (one request, asked
    again after BUSY_WAITS while MusicBrainz refuses; *on_busy* hears of
    each wait in seconds)."""
    url = f"{cover.MB_URL}/release/{release_id}?inc=recordings+isrcs&fmt=json"
    try:
        data = json.loads(cover.http_get(url, busy_waits=BUSY_WAITS, on_busy=on_busy))
    except ValueError as exc:
        raise cover.CoverError(f"unexpected answer from MusicBrainz: {exc}") from exc
    return parse_release(data)


def parse_release(data: dict) -> list[ReleaseTrack]:
    found = []
    for medium in data.get("media") or []:
        disc = medium.get("position")
        if not isinstance(disc, int):
            continue
        for track in medium.get("tracks") or []:
            number = track.get("position")
            if not isinstance(number, int):
                continue
            recording = track.get("recording") or {}
            isrcs = [i.strip().upper() for i in recording.get("isrcs") or [] if isinstance(i, str)]
            printed = str(track.get("number") or "")
            found.append(ReleaseTrack(disc, number, str(recording.get("id", "")).lower(),
                                      [i for i in dict.fromkeys(isrcs) if is_isrc(i)],
                                      str(track.get("title") or recording.get("title") or ""),
                                      int(printed) if printed.isdigit() else 0))
    return found


def match(tracks: list[Track], release_id: str, release: list[ReleaseTrack]) -> Plan:
    """Which of the release's tracks without an ISRC get which one.

    A track's recording decides when the release has it: a position would
    give the ISRC of another recording to a track MusicBrainz numbers
    differently. Without it (a format whose recording ID cdport does not
    read, or a recording merged on MusicBrainz since the rip), disc and
    track number decide, but only when the title fits too (lyrembed's rule):
    a wrong ISRC sends a Spotify song to the wrong file, a missing one only
    leaves it unmatched. cyanrip copies MusicBrainz's titles, so right
    after a rip they are equal. A file without a disc number is on the
    release's only medium, if it has one. A track number is the position on
    the disc or the number printed on the release (going on across discs, as
    cyanrip numbers); where both fit a track each, the title decides.
    """
    by_recording: dict[str, list[str]] = {}
    for t in release:
        if t.recording and (t.isrcs or t.recording not in by_recording):
            by_recording[t.recording] = t.isrcs
    discs = {t.disc for t in release}
    plan = Plan()
    for track in tracks:
        if track.isrcs or track.release != release_id.lower():
            continue
        if track.recording in by_recording:
            found = by_recording[track.recording]
        else:
            disc = track.disc if track.disc is not None else (next(iter(discs)) if len(discs) == 1 else None)
            there = next((t for t in release if t.disc == disc and track.number in (t.number, t.printed)
                          and _same_title(track.title, t.title)), None)
            if there is None:
                plan.unmatched.append(track.path)
                continue
            found = there.isrcs
        if found:
            plan.write[track.path] = found
        else:
            plan.without.append(track.path)
    return plan


def _same_title(ours: str, theirs: str) -> bool:
    """Equal apart from case and spacing; an unknown title does not object."""
    def norm(title: str) -> str:
        return " ".join(title.casefold().split())
    return not norm(ours) or not norm(theirs) or norm(ours) == norm(theirs)


def write(plan: Plan) -> tuple[list[Path], list[str]]:
    """Write the planned ISRCs; the files written and what failed. Each file
    is read again first, so one that got an ISRC meanwhile keeps it."""
    written, problems = [], []
    for path, values in plan.write.items():
        try:
            tags = audiotags.open_file(path)
            if tags.isrcs():
                continue
            tags.add_isrcs(values)
            tags.save()
        except audiotags.TagError as exc:
            problems.append(str(exc))
            continue
        written.append(path)
    return written, problems
