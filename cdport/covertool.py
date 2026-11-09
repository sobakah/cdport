"""Embed missing cover art: step of cdport, and the cdport-cover command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config, cover, tags, ui
from .config import __version__, expand


def offer(folder: Path, release_id: str = "") -> None:
    """Embed a cover in the album's FLAC files, or change the one they have.

    Tracks without a picture get an offer right away. When all have one, the
    user may keep it (the default) or change it. Covers come from the Cover
    Art Archive (by the release ID in the tags or given, else by searching
    MusicBrainz for the album) or from a file the user names.
    """
    files = tags.audio_files(folder)
    flac = [f for f in files if f.suffix.lower() == ".flac"]
    if not flac:
        ui.dim("No FLAC tracks; only FLAC files get covers.")
        return
    try:
        without = cover.missing(files)
        first = tags.read_tags(files[0])
        present = cover.current(flac) if not without else ""
    except Exception as exc:  # mutagen missing, or one of its many errors for a broken file
        ui.warn(f"Cover check skipped: {exc}")
        return
    replace = not without
    if replace:
        shown = _show_preview(flac)
        try:
            answer = ui.choose(f"Cover ({present} in every track)", [
                ("k", "keep it"),
                ("c", "change it (replaces the embedded front cover and cover.jpg/.png)"),
            ], default="k", info={"images": {"k": {"file": shown}} if shown else {}})
        finally:
            if shown:
                shown.unlink(missing_ok=True)
        if answer == "k":
            return
    else:
        ui.warn(f"No front cover is embedded in {len(without)} track(s).")
    found = _lookup(release_id or first.get("musicbrainz_albumid", ""), first)
    while True:
        options = [(str(i), f"{c.description}\n      {c.page}") for i, c in enumerate(found, 1)]
        options += [("f", "a picture file of your own (JPEG or PNG)"),
                    ("s", "keep the current cover" if replace else "no cover")]
        covers = {str(i): {"url": c.url} for i, c in enumerate(found, 1)}
        answer = ui.choose("Cover", options, default="1" if found else "s", info={"images": covers})
        if answer == "s":
            return
        try:
            if answer == "f":
                path = ui.ask_path("Picture file (empty to go back)")
                if not path:
                    continue
                data = expand(path).read_bytes()
            else:
                data = cover.http_get(found[int(answer) - 1].url)
            changed = cover.embed(files, data, replace=replace)
            saved = cover.save_file(folder, data, replace=replace)
        except (cover.CoverError, OSError) as exc:
            ui.error(f"Cover not embedded: {exc}")
            continue
        verb = "changed" if replace else "embedded"
        ui.success(f"Cover {verb} in {changed} track(s)" + (f", also saved as {saved.name}." if saved else "."))
        return


def _show_preview(flac: list[Path]) -> Path | None:
    """A link to the embedded cover, like the one each downloadable cover
    gets; returns the preview file, to delete once the question is answered."""
    try:
        path = cover.preview(flac)
    except OSError as exc:
        ui.warn(f"Cannot show the current cover: {exc}")
        return None
    if path and ui.in_terminal():  # a page shows the picture itself; it cannot open file:// links
        ui.info(f"Current cover: {path.as_uri()}")
    return path


def _lookup(release_id: str, first_tags: dict[str, str]) -> list[cover.Candidate]:
    """Covers for the release, or for the album the user picks from a search."""
    try:
        if release_id:
            return cover.candidates(release_id)
        album = first_tags.get("album", "")
        artist = first_tags.get("albumartist") or first_tags.get("artist", "")
        if not album:
            ui.dim("The tracks have neither a MusicBrainz release ID nor an album tag to search for.")
            return []
        ui.dim(f"No MusicBrainz release ID in the tags; searching for \"{album}\""
               + (f" by {artist}" if artist else "") + " ...")
        albums = cover.search_albums(album, artist)
        if not albums:
            ui.dim("MusicBrainz found no such album.")
            return []
        options = [(str(i), f"{label}\n      {cover.group_page(group)}")
                   for i, (group, label) in enumerate(albums, 1)] + [("n", "none of these")]
        covers = {str(i): {"url": cover.group_thumbnail(group)} for i, (group, _) in enumerate(albums, 1)}
        answer = ui.choose("Which album is it", options, default="1", info={"images": covers})
        if answer == "n":
            return []
        return cover.group_candidates(albums[int(answer) - 1][0])
    except cover.CoverError as exc:
        ui.warn(f"Cannot ask MusicBrainz or the Cover Art Archive: {exc}")
        return []


def album_folders(paths: list[Path], recursive: bool) -> list[Path]:
    """The given folders, or with recursive every folder below them that holds audio files."""
    found: list[Path] = []
    for path in paths:
        candidates = [path] + (sorted(p for p in path.rglob("*") if p.is_dir()) if recursive else [])
        for folder in candidates:
            if folder not in found and tags.audio_files(folder):
                found.append(folder)
    return found


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdport-cover",
        description="Embed cover art in FLAC albums, from the Cover Art Archive or a picture "
                    "file. Albums with a cover can keep it or change it.",
    )
    parser.add_argument("folders", nargs="+", type=Path, metavar="FOLDER", help="album folder(s)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="also every album folder below the given ones")
    parser.add_argument("--release", metavar="MBID",
                        help="MusicBrainz release ID to take the cover from (one album only)")
    parser.add_argument("--version", action="version", version=f"cdport-cover {__version__}")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="colour handling (default: auto)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui.setup_color(args.color)
    paths = [config.expand(p) for p in args.folders]
    missing = [p for p in paths if not p.is_dir()]
    if missing:
        parser.error("not a folder: " + ", ".join(str(p) for p in missing))
    folders = album_folders(paths, args.recursive)
    if not folders:
        ui.error("No audio files found" + ("." if args.recursive else "; add -r to look in subfolders."))
        return 2
    if args.release and len(folders) > 1:
        parser.error("--release applies to one album, but there are several folders")
    try:
        for folder in folders:
            ui.gap()
            ui.info(ui.style(ui.tilde(folder), "bold"))
            offer(folder, args.release or "")
    except KeyboardInterrupt:
        print()
        ui.warn("Stopped.")
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
