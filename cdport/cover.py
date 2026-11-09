"""Cover art for albums cyanrip ripped without one.

cyanrip embeds only an image the Cover Art Archive marks as the front cover.
Many releases have an image without that mark, or none, while another edition
of the same album has a front cover (seen 2026-10-11 with P1Harmony's
"때깔 (Killin' It)", 색깔 (Superb) ver.). This offers those instead.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config
from .config import __version__

MB_SITE = "https://musicbrainz.org"
MB_URL = f"{MB_SITE}/ws/2"
CAA_URL = "https://coverartarchive.org"
USER_AGENT = f"cdport/{__version__}"
# Ten tracks each carry a copy, so the original (often several MB) is too big.
THUMBNAIL = "1200"
FRONT_COVER = 3  # FLAC picture type
# MusicBrainz allows one request per second per client.
MB_INTERVAL_SECONDS = 1.1
_last_mb_request = 0.0
_mb_lock = threading.Lock()
# MusicBrainz answers 503 when it is busy or a client is too fast; it asks
# clients to back off and retry.
MB_RETRIES = 2
MB_RETRY_SECONDS = 3.0


class CoverError(Exception):
    """A download or lookup failed, or the file is not a usable picture."""


class NotFound(CoverError):
    pass


@dataclass
class Candidate:
    description: str
    url: str   # what gets downloaded
    page: str  # the full image, for the user to look at


def _wait_for_musicbrainz() -> None:
    """At most one request per second, also from several threads (the page
    serves each request in a thread of its own)."""
    global _last_mb_request
    with _mb_lock:
        wait = _last_mb_request + MB_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_mb_request = time.monotonic()


def http_get(url: str, timeout: float = 20) -> bytes:
    """The only network access in this module; tests replace it."""
    musicbrainz = url.startswith(MB_URL)
    for attempt in range(MB_RETRIES + 1 if musicbrainz else 1):
        if musicbrainz:
            _wait_for_musicbrainz()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(url) from exc
            if musicbrainz and exc.code == 503 and attempt < MB_RETRIES:
                time.sleep(MB_RETRY_SECONDS)
                continue
            raise CoverError(f"{url}: HTTP {exc.code}" + (" (MusicBrainz is busy; try again later)"
                                                         if exc.code == 503 else "")) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise CoverError(f"{url}: {getattr(exc, 'reason', exc)}") from exc
    raise AssertionError("unreachable")


def _https(url: str) -> str:
    # The archive's JSON mixes http:// and https:// links.
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _images(kind: str, mbid: str) -> list[dict]:
    try:
        data = json.loads(http_get(f"{CAA_URL}/{kind}/{mbid}"))
    except NotFound:
        return []
    except ValueError as exc:
        raise CoverError(f"unexpected answer from the Cover Art Archive: {exc}") from exc
    return data.get("images", [])


def _candidate(description: str, image: dict) -> Candidate:
    thumbs = image.get("thumbnails", {})
    url = thumbs.get(THUMBNAIL) or thumbs.get("large") or image["image"]
    return Candidate(description, _https(url), _https(image["image"]))


def release_group(release_id: str) -> str | None:
    try:
        data = json.loads(http_get(f"{MB_URL}/release/{release_id}?inc=release-groups&fmt=json"))
    except NotFound:
        return None
    except ValueError as exc:
        raise CoverError(f"unexpected answer from MusicBrainz: {exc}") from exc
    return data.get("release-group", {}).get("id")


def candidates(release_id: str) -> list[Candidate]:
    """Covers to offer for a MusicBrainz release, best first."""
    found: list[Candidate] = []
    images = _images("release", release_id)
    fronts = [i for i in images if i.get("front")]
    if fronts:
        found.append(_candidate("front cover of this release", fronts[0]))
    elif len(images) == 1 and not images[0].get("types"):
        found.append(_candidate("this release's only image (not marked as a cover)", images[0]))
    group = release_group(release_id)
    if group:
        for image in _images("release-group", group):
            if image.get("front"):
                found.append(_candidate("front cover of another edition of the album", image))
                break
    unique: list[Candidate] = []
    for c in found:
        if all(c.page != u.page for u in unique):
            unique.append(c)
    return unique


def group_candidates(group_id: str) -> list[Candidate]:
    """The front cover of a release group (album), for tracks without a release ID."""
    for image in _images("release-group", group_id):
        if image.get("front"):
            return [_candidate("front cover of the album", image)]
    return []


def _phrase(text: str) -> str:
    """A quoted Lucene phrase for MusicBrainz's search."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# A trailing edition note: "Floral Shoppe (Remastered)", "Album [Deluxe]".
_EDITION_SUFFIX = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def search_albums(album: str, artist: str = "", limit: int = 5) -> list[tuple[str, str]]:
    """MusicBrainz release groups matching the tags: (id, "Title - Artist (year, type)").

    MusicBrainz names the album without edition notes, so when the full tag
    finds nothing, the search is repeated without a trailing (...) or [...].
    """
    found = _search_albums(album, artist, limit)
    shorter = _EDITION_SUFFIX.sub("", album)
    if not found and shorter and shorter != album:
        found = _search_albums(shorter, artist, limit)
    return found


def _search_albums(album: str, artist: str, limit: int) -> list[tuple[str, str]]:
    query = f"releasegroup:{_phrase(album)}" + (f" AND artist:{_phrase(artist)}" if artist else "")
    url = f"{MB_URL}/release-group/?query={urllib.parse.quote(query)}&limit={limit}&fmt=json"
    try:
        data = json.loads(http_get(url))
    except ValueError as exc:
        raise CoverError(f"unexpected answer from MusicBrainz: {exc}") from exc
    found = []
    for group in data.get("release-groups", []):
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in group.get("artist-credit", []))
        details = ", ".join(filter(None, (group.get("first-release-date", "")[:4], group.get("primary-type", ""))))
        found.append((group["id"], f"{group.get('title', '?')} - {credit or '?'}" + (f" ({details})" if details else "")))
    return found


def image_type(data: bytes) -> tuple[str, str]:
    """MIME type and file extension, from the first bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    raise CoverError("not a JPEG or PNG picture")


def missing(files: list[Path]) -> list[Path]:
    """FLAC files without an embedded front cover (other formats are not
    checked). A back cover or booklet page alone is no cover for a player."""
    from mutagen.flac import FLAC

    return [f for f in files if f.suffix.lower() == ".flac"
            and not any(p.type == FRONT_COVER for p in FLAC(f).pictures)]


def current(files: list[Path]) -> str:
    """A short description of the first embedded front cover, e.g. 'JPEG, 103 KB'."""
    from mutagen.flac import FLAC

    for f in files:
        if f.suffix.lower() != ".flac":
            continue
        for picture in FLAC(f).pictures:
            if picture.type == FRONT_COVER:
                kind = picture.mime.split("/")[-1].upper() if picture.mime else "picture"
                return f"{kind}, {len(picture.data) // 1024} KB"
    return "no front cover (other pictures only)"


def release_thumbnail(release_id: str) -> str:
    """A small front cover of the release, for the page to show next to it
    (the Cover Art Archive answers 404 when there is none)."""
    return f"{CAA_URL}/release/{release_id}/front-250"


def group_thumbnail(group_id: str) -> str:
    return f"{CAA_URL}/release-group/{group_id}/front-250"


def group_page(group_id: str) -> str:
    """The album's MusicBrainz page, so a search hit can be checked before picking it."""
    return f"{MB_SITE}/release-group/{group_id}"


def preview(files: list[Path]) -> Path | None:
    """Write the first embedded front cover to a temporary file and return it
    (the caller deletes it).

    The keep-or-change question shows it as a link, so the user can look at
    the cover they would keep. cover.jpg may be missing or differ from what
    the tracks carry (a tag editor can change one without the other).
    """
    from mutagen.flac import FLAC

    for f in files:
        if f.suffix.lower() != ".flac":
            continue
        for picture in FLAC(f).pictures:
            if picture.type == FRONT_COVER:
                try:
                    _, ext = image_type(picture.data)
                except CoverError:
                    return None
                # Per user and per process: no other user's file in the way,
                # no other cdport overwriting it while it is looked at.
                path = config.make_runtime_dir() / f"cover-preview-{os.getpid()}{ext}"
                path.write_bytes(picture.data)
                return path
    return None


def embed(files: list[Path], data: bytes, replace: bool = False) -> int:
    """Embed the picture as front cover; returns how many files changed.

    Without replace only FLAC files without a front cover get it. With replace
    every FLAC file's front cover is swapped; other pictures (back cover,
    booklet) stay.
    """
    from mutagen.flac import FLAC, Picture

    mime, _ = image_type(data)
    targets = [f for f in files if f.suffix.lower() == ".flac"] if replace else missing(files)
    for f in targets:
        audio = FLAC(f)
        if replace:
            keep = [p for p in audio.pictures if p.type != FRONT_COVER]
            audio.clear_pictures()
            for p in keep:
                audio.add_picture(p)
        picture = Picture()
        picture.type = FRONT_COVER
        picture.mime = mime
        picture.desc = ""
        picture.data = data
        audio.add_picture(picture)
        audio.save()
    return len(targets)


def save_file(folder: Path, data: bytes, replace: bool = False) -> Path | None:
    """Also keep the picture as cover.jpg/.png for players that look for one.

    Without replace an existing cover file stays and None is returned. With
    replace (the user chose to change the cover) the new file takes the place
    of cover.jpg and cover.png.
    """
    _, ext = image_type(data)
    path = folder / f"cover{ext}"
    if replace:
        for old in (folder / "cover.jpg", folder / "cover.png"):
            if old != path:
                old.unlink(missing_ok=True)
    elif path.exists():
        return None
    path.write_bytes(data)
    return path
