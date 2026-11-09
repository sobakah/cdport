"""Finding the MusicBrainz release for a disc MusicBrainz does not know yet.

A disc ID that is not attached to a release makes cyanrip's lookup fail,
even when the album is on MusicBrainz. cyanrip takes the tags from a release
given by ID (-R) all the same, so the user only has to find the release.
Requests go through cover.http_get, which keeps MusicBrainz's rate limit.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass

from . import cover

_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@dataclass
class Release:
    id: str
    label: str                # "Title - Artist (2025, KR) · CD 6 tracks"
    track_counts: list[int]   # per medium, to compare with the disc

    @property
    def page(self) -> str:
        return release_page(self.id)


def release_page(release_id: str) -> str:
    return f"{cover.MB_SITE}/release/{release_id}"


def parse_release(text: str) -> str:
    """What cyanrip's -R takes, from the user's answer: a list number as it
    is, or the release ID, also out of a pasted MusicBrainz link. "" if neither."""
    text = text.strip()
    if text.isdigit():
        return text
    match = _MBID.search(text)
    return match.group(0).lower() if match else ""


def search_releases(album: str, artist: str = "", limit: int = 10) -> list[Release]:
    """Releases matching the album title (and artist), best first.

    As in cover.search_albums, the search is repeated without a trailing
    edition note like "(Deluxe)" when the full title finds nothing.
    """
    found = _search(album, artist, limit)
    shorter = cover._EDITION_SUFFIX.sub("", album)
    if not found and shorter and shorter != album:
        found = _search(shorter, artist, limit)
    return found


def _search(album: str, artist: str, limit: int) -> list[Release]:
    query = f"release:{cover._phrase(album)}" + (f" AND artist:{cover._phrase(artist)}" if artist else "")
    url = f"{cover.MB_URL}/release/?query={urllib.parse.quote(query)}&limit={limit}&fmt=json"
    try:
        data = json.loads(cover.http_get(url))
    except ValueError as exc:
        raise cover.CoverError(f"unexpected answer from MusicBrainz: {exc}") from exc
    return [_release(r) for r in data.get("releases", [])]


def _release(data: dict) -> Release:
    credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in data.get("artist-credit", []))
    details = ", ".join(filter(None, (data.get("date", "")[:4], data.get("country", ""),
                                      data.get("disambiguation", ""))))
    media = data.get("media", [])
    counts = [m.get("track-count", 0) for m in media]
    formats = " + ".join(f"{m.get('format') or '?'} {_tracks(m.get('track-count'))}" for m in media)
    label = f"{data.get('title', '?')} - {credit or '?'}" + (f" ({details})" if details else "")
    return Release(data["id"], label + (f" · {formats}" if formats else ""), counts)


def _tracks(count: int | None) -> str:
    return "? tracks" if count is None else f"{count} track" + ("" if count == 1 else "s")
