"""The interactive run: tools, disc, rip, tags, lyrics, finish."""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, cover, covertool, disc, install, library, lyrics, musicbrainz, proc, rip, state, tags, tools, ui
from .config import expand
from .state import AlbumState

POLL_SECONDS = 2


class Quit(Exception):
    """The user chose to stop; carries the exit code."""

    def __init__(self, code: int = 0) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Options:
    """This run's choices from the command line; empty means "use the config"."""
    folder: Path | None = None
    device: str = ""
    lyrics: str = ""
    editor: str = ""
    install_cyanrip: bool = False
    rip_mode: str = ""  # "fast" or "secure"; empty: config cyanrip.mode


@dataclass
class Session:
    cfg: dict[str, Any]
    options: Options
    work_dir: Path | None    # None until set: asked on first use
    library_dir: Path | None
    config_path: Path
    cyanrip: str | None = None
    lyrembed: str | None = None
    lyrembed_problem: str = ""  # found, but does not start
    lyrembed_romanize: bool = False  # lyrembed has --romanize for batch mode
    album: AlbumState | None = None  # the album being worked on, for the Ctrl+C message
    failed: bool = False
    logs: list[Path] = field(default_factory=list)  # rip logs moved out of the library


def new_session(cfg: dict[str, Any], options: Options, config_path: Path | None = None) -> Session:
    return Session(cfg, options,
                   expand(cfg["work_dir"]) if cfg["work_dir"] else None,
                   expand(cfg["library_dir"]) if cfg["library_dir"] else None,
                   config_path or config.default_config_path())


def run(session: Session) -> int:
    """The whole program after argument parsing; returns the exit code."""
    ask_missing_folders(session)
    check_tools(session)

    folder = session.options.folder
    while True:
        offer_staging_cleanup(session)
        session.album = None
        session.logs = []
        if folder:
            album = open_folder(folder)
            folder = None  # the folder from the command line is for the first run only
        else:
            album = offer_resume(session) or disc_and_rip(session)
        if album is None:
            break
        session.album = album
        continue_album(session, album)
        summary(session, album)
        ui.gap()
        if not ui.confirm("Start another run (next disc or album)?", default=False):
            break
    return 1 if session.failed else 0


# Suggestions for the first-run question; nothing is used without asking.
WORK_DIR_SUGGESTION = "~/Temp"


def ask_missing_folders(session: Session) -> None:
    """Ask for the working folder and library when the config has none, and
    save the answers, so this happens only on first use."""
    questions = []
    if session.work_dir is None:
        questions.append(("work_dir", "Working folder (rips land here first)", WORK_DIR_SUGGESTION))
    if session.library_dir is None:
        questions.append(("library_dir", "Music library (finished albums go here)",
                          config.music_dir_suggestion()))
    if not questions:
        return
    ui.gap()
    ui.info("cdimport needs to know where to work. The answers are saved in "
            f"{ui.tilde(session.config_path)}; change them there later.")
    ui.dim("Edit the suggestion or press Enter to accept it.")
    answers = {}
    for key, prompt, suggestion in questions:
        while True:
            answer = ui.ask_path(prompt, suggestion)
            if answer:
                break
            ui.warn("A folder is needed.")
        path = expand(answer)
        if not path.exists():
            ui.dim(f"{ui.tilde(path)} does not exist yet; it is created when needed.")
        elif not path.is_dir():
            ui.warn(f"{ui.tilde(path)} is a file; cdimport will fail to use it. Change it in the config.")
        answers[key] = ui.tilde(path)
        session.cfg[key] = answers[key]
        setattr(session, key, path)
    try:
        config.remember(session.config_path, answers)
        ui.success(f"Saved in {ui.tilde(session.config_path)}.")
    except (OSError, ValueError) as exc:
        ui.warn(f"Used for this run, but saving failed: {exc}")


# --- 0. Tools ----------------------------------------------------------------
# Notes under a checklist line start where its text does.
INDENT = " " * (4 + ui.LABEL_WIDTH)


def check_tools(session: Session) -> None:
    ui.step(0, "Tools")
    cfg = session.cfg
    session.cyanrip = tools.find_cyanrip(cfg)
    session.lyrembed = tools.find_lyrembed(cfg)

    if session.cyanrip and (problem := tools.start_problem([session.cyanrip, "-v"])):
        ui.check("cyanrip", f"{ui.tilde(session.cyanrip)} does not start: {problem}", False)
        session.cyanrip = None
    if session.cyanrip:
        installed = install.installed_version(session.cyanrip)
        version = f" ({installed.version})" if installed else ""
        ui.check("cyanrip", f"{ui.tilde(session.cyanrip)}{version}", True)
        if installed and not session.options.install_cyanrip:
            offer_cyanrip_update(session, installed)
    else:
        ui.check("cyanrip", "not found: only albums ripped earlier can be continued", False)
    if session.options.install_cyanrip or not session.cyanrip:
        offer_cyanrip_install(session)

    editor_key = session.options.editor or cfg["editor"]
    if editor_key == "skip":
        ui.check("editor", "skipped (config)", None)
    else:
        editor = tools.find_editor(cfg, editor_key)
        if editor:
            ui.check("editor", editor.name, True)
        else:
            name = cfg["editors"].get(editor_key, {}).get("name", editor_key)
            others = [e.name for e in tools.available_editors(cfg)]
            ui.check("editor", f"{name} not found", False,
                     f"available: {', '.join(others)}" if others else "")

    if session.lyrembed and (problem := tools.start_problem([session.lyrembed, "--version"])):
        ui.check("lyrembed", f"{ui.tilde(session.lyrembed)} does not start: {problem}", False)
        session.lyrembed, session.lyrembed_problem = None, problem
    elif session.lyrembed:
        ui.check("lyrembed", ui.tilde(session.lyrembed), True)
        session.lyrembed_romanize = lyrics.can_romanize(session.lyrembed)
    else:
        ui.check("lyrembed", "not found: the lyrics step is disabled", False,
                 f"To install: {tools.LYREMBED_INSTALL_HINT}")
    ui.gap()
    ui.field("Working", ui.tilde(session.work_dir) if session.work_dir else "-")
    ui.field("Library", ui.tilde(session.library_dir) if session.library_dir else "-")


def offer_cyanrip_install(session: Session) -> None:
    """Install cyanrip when it is missing, or (re)build it on request."""
    route = install.choose_route(channel=session.cfg["cyanrip"]["channel"])
    if not session.options.install_cyanrip:
        ui.dim(f"{INDENT}To install: {install.route_hint(route)}")
        if route.kind == "download" or not ui.confirm("Install cyanrip now?", default=True):
            return
    ui.gap()
    found = install.install_cyanrip(session.cfg, session.config_path)
    if found:
        session.cyanrip = found
    elif not session.cyanrip:
        ui.warn("Continuing without cyanrip.")


def offer_cyanrip_update(session: Session, installed: install.Installed) -> None:
    """Compare the installed cyanrip with GitHub and the configured channel."""
    assert session.cyanrip
    try:
        remote = install.remote_info()
    except install.UpdateCheckError as exc:
        ui.dim(f"{INDENT}Update check skipped: {exc}")
        return
    offer = install.update_offer(session.cfg["cyanrip"]["channel"], installed, remote,
                                 install.is_self_built(session.cyanrip))
    if offer is None or not install.should_show(offer):
        return
    ui.info(f"{INDENT}{ui.style(offer.message, 'yellow')}")
    if not offer.can_build:
        ui.dim(f"{INDENT}Your package manager updates it once the distribution packages the new version.")
        return
    if not ui.confirm("Build the new cyanrip now?", default=True):
        return
    ui.gap()
    found = install.build_from_source(session.cfg, session.config_path, tools.read_os_release())
    if found:
        session.cyanrip = found


def offer_staging_cleanup(session: Session) -> None:
    """Interrupted rips leave a staging folder behind; deleting it needs a yes."""
    for staging in state.leftover_staging(session.work_dir):
        busy = rip.in_use(staging)
        if busy:
            ui.dim(f"A rip is running in another cdimport ({staging.name}); leaving it alone.")
            continue
        if busy is None:  # pragma: no cover - Windows
            ui.dim(f"Found {staging.name}; cannot tell whether a rip still uses it, so it stays.")
            continue
        files = [p for p in staging.rglob("*") if p.is_file()]
        if not files and rip.remove_empty_dirs(staging):
            ui.dim(f"Removed the empty folder of an earlier rip: {staging.name}")
            continue
        ui.warn(f"\nAn interrupted rip left {len(files)} file(s) in {staging}")
        for f in files[:5]:
            ui.dim(f"  {f.relative_to(staging)}")
        if len(files) > 5:
            ui.dim(f"  ... and {len(files) - 5} more")
        if ui.confirm("Delete this folder?", default=False):
            shutil.rmtree(staging)
            rip.lock_path(staging).unlink(missing_ok=True)
            ui.success("Deleted.")


def offer_resume(session: Session) -> AlbumState | None:
    unfinished = state.unfinished_albums(session.work_dir)
    if not unfinished:
        return None
    ui.gap()
    ui.info("Unfinished albums in the working folder:")
    options = [(str(i), f"{a.folder.name}  (next: {a.next_step()})") for i, a in enumerate(unfinished, 1)]
    options.append(("n", "start with a new disc"))
    answer = ui.choose("Continue with", options, default="1")
    if answer == "n":
        return None
    return unfinished[int(answer) - 1]


def open_folder(folder: Path) -> AlbumState | None:
    """An album ripped earlier: continue with tags, lyrics and finish."""
    folder = expand(folder).resolve()
    if not folder.is_dir():
        ui.error(f"Not a folder: {folder}")
        return None
    if not tags.audio_files(folder):
        ui.error(f"No audio files in {folder}")
        return None
    try:
        album = state.load(folder)
    except ValueError as exc:
        ui.error(str(exc))
        return None
    album.save()
    return album


def ask_folder() -> AlbumState | None:
    while True:
        answer = ui.ask_path("Album folder (empty to go back)")
        if not answer:
            return None
        album = open_folder(Path(answer))
        if album:
            return album


# --- 1. Disc -----------------------------------------------------------------
def disc_and_rip(session: Session) -> AlbumState | None:
    ui.step(1, "Disc")
    if not session.cyanrip:
        ui.warn("cyanrip is missing, so only an album ripped earlier can be continued.")
        return ask_folder()

    while True:
        found = find_disc(session)
        if isinstance(found, AlbumState):
            return found
        try:
            with disc.hold(found):
                album = preview_and_rip(session, found)
        except disc.DriveBusy as exc:  # taken between the check and here
            ui.warn(f"{exc}.")
            continue
        if album is not None:
            return album


def _wanted_device(session: Session) -> str:
    return session.options.device or session.cfg["cyanrip"]["device"]


def find_disc(session: Session) -> str | AlbumState:
    """Wait for an audio CD; returns its device ("" = cyanrip's default
    drive) or an existing album folder the user picked instead."""
    while True:
        drives = disc.find_drives()
        wanted = _wanted_device(session)
        if drives is None:
            ui.info("Disc detection is not available on this platform yet.")
            answer = ui.choose("What now", [("r", "rip from " + (wanted or "the default drive")),
                                            ("f", "continue with an existing album folder"),
                                            ("q", "quit")], default="r")
            if answer == "r":
                return wanted
        else:
            if wanted:
                drives = [d for d in drives if d.device == wanted]
            taken = {d.device: why for d in drives if (why := disc.busy(d.device))}
            for why in taken.values():
                ui.warn(f"{why}; leaving it alone.")
            drives = [d for d in drives if d.device not in taken]
            audio = [d for d in drives if d.has_audio]
            if len(audio) == 1:
                ui.success(audio[0].describe())
                return audio[0].device
            if len(audio) > 1:
                options = [(str(i), d.describe()) for i, d in enumerate(audio, 1)]
                return audio[int(ui.choose("Drive", options, default="1")) - 1].device
            if not drives and not taken:
                ui.warn(f"Drive {wanted} not found." if wanted else "No optical drive found.")
            for d in drives:
                ui.info(d.describe())
            answer = ui.choose("No free drive with an audio CD" if taken else "No audio CD", [("c", "check again"),
                                               ("w", "wait for a disc or a free drive (Ctrl+C to stop waiting)"),
                                               ("f", "continue with an existing album folder"),
                                               ("q", "quit")], default="c")
            if answer == "w":
                device = wait_for_disc(wanted)
                if device is not None:
                    return device
                continue
            if answer == "c":
                continue
        if answer == "q":
            raise Quit(0)
        album = ask_folder()
        if album:
            return album


def wait_for_disc(wanted: str) -> str | None:
    ui.dim("Waiting for an audio CD ...")
    try:
        while True:
            drives = disc.find_drives() or []
            for d in drives:
                if d.has_audio and (not wanted or d.device == wanted) and not disc.busy(d.device):
                    ui.success(d.describe())
                    return d.device
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print()
        return None


# --- 2. Rip ------------------------------------------------------------------
def describe_rip_settings(cfg: dict[str, Any], mode: str) -> str:
    opts = cfg["cyanrip"]
    parts = [MODE_LABELS[mode],
             "deemphasis " + ("on (changes the audio of preemphasised discs)" if opts["deemphasis"]
                              else "off (bit-exact)"),
             "ReplayGain tags " + ("on" if opts["replaygain"] else "off"),
             "eject " + ("on" if opts["eject"] else "off")]
    if opts["offset"] is not None:
        parts.append(f"offset {opts['offset']}")
    return ", ".join(parts)


def preview_and_rip(session: Session, device: str) -> AlbumState | None:
    """Show the MusicBrainz match, let the user correct it, then rip.

    For a disc MusicBrainz does not know, the user can search for the release
    (cyanrip takes its tags by ID), type the tags in, or rip without any.
    A failed rip is reported with cyanrip's reason and offered again; None
    means back to disc detection.
    """
    assert session.cyanrip
    release = ""
    no_mb = False
    typed: rip.ManualTags | None = None
    mode = session.options.rip_mode or session.cfg["cyanrip"]["mode"]
    shown: rip.Preview | None = None
    refresh = False
    while True:
        if shown is None or refresh:  # the first time, and after another release or tags
            refresh = False
            ui.tool_start("cyanrip", "disc info and MusicBrainz match")
            shown = rip.preview(session.cyanrip, session.cfg, device=device, release=release,
                                no_musicbrainz=no_mb, tags=typed)
            ui.tool_end("cyanrip", shown.code)
        if shown.needs_tags:
            ui.warn("MusicBrainz does not know this disc, and cyanrip does not rip it without tags.")
        elif shown.releases:
            ui.warn("Several MusicBrainz releases have this disc; pick one to see its tags.")
        elif shown.code != 0:
            ui.warn(f"cyanrip -I exited with code {shown.code}:{rip.explain(shown.tail) or ' see above.'}")
        if shown.disc_unknown and shown.submission_url and not no_mb:
            explain_submission(shown.submission_url)
        ui.dim(f"Rip settings: {describe_rip_settings(session.cfg, mode)}")
        options = [(str(i), f"{label}\n      {musicbrainz.release_page(mbid)}")
                   for i, (mbid, label) in enumerate(shown.releases, 1)]
        if not (shown.needs_tags or shown.releases):
            options.append(("r", "rip"))
        options += [
            ("s", "search MusicBrainz for the release (album and artist)"),
            ("m", "pick a MusicBrainz release (list number, release ID or link)"),
            ("t", "type in the tags (album, artist, year, track titles)"),
            ("n", "rip without MusicBrainz metadata"),
            ("p", "rip securely with paranoia instead (about twice as slow)" if mode == "fast"
             else "rip fast instead (checked with AccurateRip)"),
            ("b", "back to disc detection"),
            ("q", "quit"),
        ]
        default = "1" if shown.releases else "s" if shown.needs_tags else "r"
        answer = ui.choose("Rip this disc", options, default=default)
        if answer == "p":
            mode = "secure" if mode == "fast" else "fast"
            continue
        # Only "p" keeps the preview; everything else may change what cyanrip shows.
        refresh = True
        if answer.isdigit():
            release, no_mb = shown.releases[int(answer) - 1][0], False
            continue
        if answer == "s":
            found = search_release(typed, shown.tracks)
            if found:
                release, no_mb = found, False
            continue
        if answer == "m":
            text = ui.ask("Release number, ID or link (empty to go back)")
            if text:
                found = musicbrainz.parse_release(text)
                if found:
                    release, no_mb = found, False
                else:
                    ui.warn("That is neither a list number nor a MusicBrainz release ID or link.")
            continue
        if answer == "t":
            typed = ask_tags(typed, shown.tracks)
            continue
        if answer == "n":
            no_mb = True
            continue
        if answer == "b":
            return None
        if answer == "q":
            raise Quit(0)
        if not ensure_offset(session, device):
            continue

        ui.step(2, "Rip")
        fast = mode == "fast" and shown.accuraterip == "found"
        if mode == "fast" and not fast:
            ui.info("This disc is not in the AccurateRip database, so a fast rip could not be "
                    "checked: ripping securely with paranoia.")
        session.work_dir.mkdir(parents=True, exist_ok=True)
        ui.tool_start("cyanrip", "ripping fast (no paranoia)" if fast else "ripping securely (paranoia)")
        try:
            # A fast rip keeps the disc in: tracks that fail the check are read again.
            folder, leftover = rip.rip(session.cyanrip, session.cfg, session.work_dir,
                                       device=device, release=release, no_musicbrainz=no_mb, tags=typed,
                                       paranoia="none" if fast else "max",
                                       eject=False if fast else None)
        except rip.RipError as exc:
            ui.tool_end("cyanrip", None)
            ui.error(str(exc))
            answer = ui.choose("Then", [("r", "back to the disc preview, to try again"),
                                        ("b", "back to disc detection"),
                                        ("q", "quit")], default="r")
            if answer == "b":
                return None
            if answer == "q":
                raise Quit(1)
            continue
        ui.tool_end("cyanrip", 0)
        album = AlbumState(folder)
        album.mark("rip")
        ui.success(f"Ripped to {ui.tilde(folder)}")
        if leftover:
            ui.warn(f"cyanrip also left files in {leftover}; check them.")
        if fast:
            check_fast_rip(session, folder, device, release=release, no_mb=no_mb, typed=typed)
        if shown.disc_unknown and shown.submission_url:
            ui.info(f"To add this disc to MusicBrainz later: {shown.submission_url}")
        return album


def check_fast_rip(session: Session, folder: Path, device: str, *, release: str, no_mb: bool,
                   typed: rip.ManualTags | None) -> None:
    """After a fast rip: read the tracks AccurateRip does not confirm again,
    securely, in place of the fast ones; then eject as configured."""
    assert session.cyanrip
    eject = session.cfg["cyanrip"]["eject"]
    log = rip.rip_log(folder)
    try:
        results = rip.accuraterip_results(log.read_text(encoding="utf-8", errors="replace")) if log else {}
    except OSError as exc:
        results = {}
        ui.warn(f"Cannot read the rip log: {exc}")
    unconfirmed = sorted(n for n, ok in results.items() if not ok)
    if not results:
        ui.warn("The rip log says nothing about AccurateRip, so the fast rip is unchecked. "
                "Rip the disc again with p (secure) if in doubt.")
    elif not unconfirmed:
        ui.success(f"AccurateRip confirms all {len(results)} tracks.")
    else:
        listed = ", ".join(str(n) for n in unconfirmed)
        ui.warn(f"AccurateRip does not confirm track(s) {listed}; ripping them again securely.")
        ui.tool_start("cyanrip", f"secure re-rip of track(s) {listed}")
        try:
            replaced, relog = rip.rerip(session.cyanrip, session.cfg, folder, session.work_dir, unconfirmed,
                                        device=device, release=release, no_musicbrainz=no_mb, tags=typed,
                                        eject=eject)
        except rip.RipError as exc:
            ui.tool_end("cyanrip", None)
            ui.error(f"{exc}\nThe fast-ripped track(s) {listed} stay, unconfirmed.")
            return
        ui.tool_end("cyanrip", 0)
        again = {}
        if relog:
            again = rip.accuraterip_results(relog.read_text(encoding="utf-8", errors="replace"))
        still = [n for n in unconfirmed if not again.get(n)]
        ui.success(f"Replaced {len(replaced)} track(s) with the secure rip.")
        if still:
            ui.warn(f"AccurateRip does not confirm track(s) {', '.join(map(str, still))} after the secure "
                    "rip either: a pressing AccurateRip does not know, or a damaged disc. The secure "
                    "rip is kept; its log says more.")
        else:
            ui.success("AccurateRip now confirms every track.")
        return  # the re-rip ejected the disc (-Q) if configured
    if eject and (problem := disc.eject(device)):
        ui.warn(f"Cannot eject the disc ({problem}); take it out with the drive's button.")


def explain_submission(url: str) -> None:
    """How to tell MusicBrainz about the disc, so the next lookup finds it."""
    ui.gap()
    ui.info("MusicBrainz has no release with this disc's ID. To add it (needs a MusicBrainz account):")
    ui.info(f"  1. Open this link while logged in:\n     {url}")
    ui.info("  2. Search for the release by artist or title. If it is listed, select it\n"
            "     (the edition with the same number of tracks) and attach the disc ID.\n"
            "     If it is not, add the release there.")
    ui.info("  3. Enter an edit note (e.g. barcode or catalog number) and submit.")
    ui.dim("The edit may need up to 7 days of voting; after that cyanrip finds the disc by itself.\n"
           "Until then, s or m below rips with the release's tags all the same.")


def search_release(typed: rip.ManualTags | None, tracks: tuple[int, int] | None) -> str:
    """A release ID from a MusicBrainz search, or "" when none was picked."""
    album = ui.ask("Album title (empty to go back)", typed.album if typed else "")
    if not album:
        return ""
    artist = ui.ask("Artist (optional)", typed.artist if typed else "")
    try:
        found = musicbrainz.search_releases(album, artist)
    except cover.CoverError as exc:
        ui.warn(f"Cannot search MusicBrainz: {exc}")
        return ""
    if not found:
        ui.warn("MusicBrainz found no such release.")
        return ""
    count = tracks[1] - tracks[0] + 1 if tracks else None
    options = []
    for i, r in enumerate(found, 1):
        same = "  ← same track count as the disc" if count in r.track_counts else ""
        options.append((str(i), f"{r.label}{same}\n      {r.page}"))
    options.append(("n", "none of these"))
    answer = ui.choose("Which release is it", options, default="1")
    return "" if answer == "n" else found[int(answer) - 1].id


def ask_tags(previous: rip.ManualTags | None, tracks: tuple[int, int] | None) -> rip.ManualTags | None:
    """Tags for cyanrip's -a/-t; None when the user leaves the album empty.

    Only what cyanrip needs to name the folder and files. The tag step can
    add the rest (per-track artists, genre) afterwards.
    """
    prev = previous or rip.ManualTags()
    album = ui.ask("Album (empty: no tags of your own)", prev.album)
    if not album:
        return None
    artist = ui.ask("Album artist", prev.artist)
    date = ui.ask("Year or date (optional)", prev.date)
    if tracks:
        numbers = range(tracks[0], tracks[1] + 1)
    else:
        count = ui.ask("Number of tracks (empty: no track titles)")
        numbers = range(1, int(count) + 1) if count.isdigit() else range(0)
    titles = {}
    if numbers:
        ui.dim('A track without a title is called "Unknown track".')
    for n in numbers:
        title = ui.ask(f"Track {n} title", prev.titles.get(n, ""))
        if title:
            titles[n] = title
    return rip.ManualTags(album, artist, date, titles)


MODE_LABELS = {
    "fast": "fast (no paranoia, checked with AccurateRip, mismatches ripped again securely)",
    "secure": "secure (paranoia)",
}


# cyanrip's confidence is the number of tracks that confirmed the offset.
# From this many on, a measured offset is saved without asking.
MIN_OFFSET_CONFIDENCE = 4
# cyanrip -f's last line, e.g. "Drive offset of +6 found (confidence: 4)!"
# (src/cyanrip_main.c, master b40c1f0).
_OFFSET_FOUND = re.compile(r"Drive offset of ([+-]\d+) found \(confidence: (\d+)\)")


def parse_offset(lines: list[str]) -> tuple[int, int] | None:
    """(offset, confidence) from cyanrip -f's output, if it found one."""
    for line in reversed(lines):
        match = _OFFSET_FOUND.search(line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def ensure_offset(session: Session, device: str) -> bool:
    """Get the drive's read offset: cyanrip refuses to rip without one.

    False when the user goes back instead.
    """
    if session.cfg["cyanrip"]["offset"] is not None:
        return True
    assert session.cyanrip
    ui.gap()
    ui.warn("cyanrip needs the drive's read offset before it rips. Once set, it is saved in the config.")
    ui.dim("Drives are listed at https://www.accuraterip.com/driveoffsets.htm; one marked [Purged] "
           "has no fixed offset and must be measured.")
    while True:
        answer = ui.choose("Offset", [
            ("f", "measure it with cyanrip -f (needs a disc that is in the AccurateRip database)"),
            ("e", "enter it"),
            ("0", "use 0 for this rip only (shifted by a few samples, inaudible, "
                  "but AccurateRip cannot verify the rip)"),
            ("b", "back"),
        ], default="f")
        if answer == "b":
            return False
        if answer == "0":
            session.cfg["cyanrip"]["offset"] = 0
            return True
        if answer == "f":
            offset = measure_offset(session, device)
        else:
            offset = ask_offset("Offset in samples, e.g. +6")
        if offset is None:
            continue
        save_offset(session, offset)
        return True


def measure_offset(session: Session, device: str) -> int | None:
    """Run cyanrip -f; the offset to save, or None to choose again."""
    assert session.cyanrip
    cmd = [session.cyanrip, "-f"] + (["-d", device] if device else [])
    ui.tool_start("cyanrip", proc.format_command(cmd))
    # cyanrip writes nothing here as far as seen, but runs in a throwaway
    # folder in case a version does.
    with tempfile.TemporaryDirectory(prefix="cdimport-offset-") as scratch:
        code, tail = proc.run_tail(cmd, cwd=Path(scratch), lines=10)
    ui.tool_end("cyanrip", code)
    found = parse_offset(tail)
    if found is None:
        if code != 0:
            ui.warn(f"cyanrip could not measure the offset:{rip.explain(tail[-3:]) or ' see above.'}\n"
                    "A widely sold CD is most likely to be in the AccurateRip database.")
            return None
        # Output not captured (no terminal) or worded differently.
        return ask_offset("Offset cyanrip reported")
    offset, confidence = found
    if confidence >= MIN_OFFSET_CONFIDENCE:
        ui.success(f"Drive offset {offset:+d} confirmed by {confidence} tracks.")
        return offset
    ui.warn(f"cyanrip found {offset:+d}, but only {confidence} track(s) confirm it; cdimport saves "
            f"an offset on its own from {MIN_OFFSET_CONFIDENCE} on. Another disc with more tracks in "
            "the AccurateRip database gives more certainty.")
    answer = ui.choose("Then", [("u", f"use {offset:+d} and save it"),
                                ("b", "back to the offset choices")], default="b")
    return offset if answer == "u" else None


def save_offset(session: Session, offset: int) -> None:
    session.cfg["cyanrip"]["offset"] = offset
    try:
        config.remember(session.config_path, {"cyanrip": {"offset": offset}})
        ui.success(f"Offset {offset:+d} saved in {session.config_path}")
    except (OSError, ValueError) as exc:
        ui.warn(f"Offset {offset:+d} is used for this rip, but saving it failed: {exc}")


def ask_offset(prompt: str) -> int | None:
    """An offset in samples; None when the answer is empty or not a number."""
    answer = ui.ask(f"{prompt} (empty to go back)")
    if not answer:
        return None
    try:
        return int(answer)
    except ValueError:
        ui.warn(f"'{answer}' is not a whole number of samples.")
        return None


# --- 3.-5. -------------------------------------------------------------------
def continue_album(session: Session, album: AlbumState) -> None:
    handlers = {"tags": tags_step, "lyrics": lyrics_step, "finish": finish_step}
    # Every time an album is worked on: after a rip, and whenever it is opened
    # again (resumed, or given as a folder), whatever step comes next. Asks
    # only when tracks lack a cover.
    if album.next_step() is not None:
        offer_cover(album.folder)
    while (step := album.next_step()) is not None:
        if step == "rip":  # unreachable: folders without a rip have no state file
            album.mark("rip")
            continue
        if not handlers[step](session, album):
            return  # the step failed or the user stopped; state stays resumable


def tags_step(session: Session, album: AlbumState) -> bool:
    ui.step(3, "Tags")
    cfg = session.cfg
    key = session.options.editor or cfg["editor"]
    editor = tools.find_editor(cfg, key) if key != "skip" else None
    edited = False
    while True:
        options = []
        if editor:
            options.append(("o", f"open {editor.name}"))
        others = [e for e in tools.available_editors(cfg) if not editor or e.key != editor.key]
        if others:
            options.append(("e", "use another editor"))
        options += [("c", "continue without editing" if not edited else "continue"),
                    ("q", "stop here (continue later)")]
        answer = ui.choose("Tags", options, default="o" if editor and not edited else "c")
        if answer == "q":
            raise Quit(0)
        if answer == "e":
            picked = ui.choose("Editor", [(str(i), e.name) for i, e in enumerate(others, 1)], default="1")
            editor = others[int(picked) - 1]
            answer = "o"
        if answer == "o" and editor:
            if run_editor(editor, album.folder):
                edited = True
            continue
        # "c": check before lyrembed needs the tags.
        if check_tags(album.folder) or ui.confirm("Continue anyway?", default=False):
            album.mark("tags", skipped=not edited)
            return True


def offer_cover(folder: Path) -> None:
    covertool.offer(folder)


def run_editor(editor: tools.Editor, folder: Path) -> bool:
    cmd = tags.expand_command(editor.command, folder, tags.audio_files(folder))
    ui.dim(f"$ {proc.format_command(cmd)}")
    try:
        launched = tags.open_editor(editor, folder)
    except OSError as exc:
        ui.error(f"Cannot start {editor.name}: {exc}")
        return False
    problem = launched.failure()
    if problem:
        ui.error(f"{editor.name} failed to start:\n{problem}")
        return False
    # Single-instance apps return at once, so the process says nothing about
    # when editing is done; only the user knows.
    ui.wait_for_enter(f"Edit and save in {editor.name}, then press Enter here")
    problem = launched.failure()
    if problem:
        ui.warn(f"{editor.name} reported an error:\n{problem}")
    return True


def check_tags(folder: Path) -> bool:
    """Warn about tracks lyrembed cannot search for; True when all are fine."""
    try:
        problems = tags.missing_tags(tags.audio_files(folder))
    except tags.TagReadError as exc:
        ui.warn(f"Tag check skipped: {exc}")
        return True
    if not problems:
        ui.success("Every track has an artist and a title.")
        return True
    ui.warn("Tracks missing tags that the lyrics search needs:")
    for path, missing in problems:
        ui.warn(f"  {path.name}: no {' or '.join(missing)}")
    return False


def lyrics_step(session: Session, album: AlbumState) -> bool:
    ui.step(4, "Lyrics")
    if not session.lyrembed and session.lyrembed_problem:
        ui.error(f"lyrembed does not start: {session.lyrembed_problem}")
        answer = ui.choose("Then", [("q", "stop here; repair lyrembed and run cdimport again to continue"),
                                    ("s", "skip lyrics for this album")], default="q")
        if answer == "q":
            raise Quit(1)
        album.mark("lyrics", skipped=True)
        return True
    if not session.lyrembed:
        ui.warn(f"lyrembed is not installed, skipping. To install: {tools.LYREMBED_INSTALL_HINT}")
        album.mark("lyrics", skipped=True)
        return True
    mode = session.options.lyrics or session.cfg["lyrics_mode"]
    if mode == "ask":
        mode = {"a": "auto", "i": "interactive", "s": "skip"}[ui.choose("Lyrics", [
            ("a", "automatic: embed confident matches, no questions"),
            ("i", "interactive: choose for each track"),
            ("s", "skip"),
        ], default="a")]
    romanize = ask_romanize(session) if mode == "auto" else None
    while True:
        if mode == "skip":
            album.mark("lyrics", skipped=True)
            return True
        cmd = lyrics.lyrembed_command(session.lyrembed, session.cfg, album.folder, mode, romanize)
        ui.tool_start("lyrembed", proc.format_command(cmd))
        code = lyrics.run_lyrembed(session.lyrembed, session.cfg, album.folder, mode, romanize)
        ui.tool_end("lyrembed", code)
        if code == lyrics.EXIT_OK:
            album.mark("lyrics")
            if mode == "auto":
                offer_lyrics_check(session, album)
            return True
        if code == lyrics.EXIT_INTERRUPTED:
            raise KeyboardInterrupt
        if code == lyrics.EXIT_BATCH_FAILURES and mode == "auto":
            ui.warn("lyrembed could not decide some tracks.")
            answer = ui.choose("Then", [("i", "go through them interactively"),
                                        ("c", "continue without them")], default="i")
            if answer == "c":
                album.mark("lyrics")
                offer_lyrics_check(session, album)
                return True
            mode = "interactive"
            continue
        reason = {lyrics.EXIT_UNUSABLE_INPUT: "it could not use the folder",
                  proc.CANNOT_START: "it could not be started (see above)"}.get(code, f"exit code {code}")
        ui.error(f"lyrembed failed: {reason}.")
        answer = ui.choose("Then", [("r", "try again"), ("s", "skip lyrics"),
                                    ("q", "stop here (continue later)")], default="r")
        if answer == "s":
            mode = "skip"
        elif answer == "q":
            session.failed = True
            return False


def offer_lyrics_check(session: Session, album: AlbumState) -> None:
    """After automatic mode, let the user look over what it embedded.

    Interactive lyrembed shows every track, including those that now have
    lyrics, so it doubles as the review. The step is already marked done:
    the lyrics are embedded whatever happens in the check.
    """
    answer = ui.choose("Lyrics embedded", [
        ("c", "continue"),
        ("l", "check them: open the album in lyrembed (interactive)"),
    ], default="c")
    if answer == "c":
        return
    cmd = lyrics.lyrembed_command(session.lyrembed, session.cfg, album.folder, "interactive")
    ui.tool_start("lyrembed", proc.format_command(cmd))
    code = lyrics.run_lyrembed(session.lyrembed, session.cfg, album.folder, "interactive")
    ui.tool_end("lyrembed", code)
    if code == lyrics.EXIT_INTERRUPTED:
        raise KeyboardInterrupt
    if code != lyrics.EXIT_OK:
        ui.warn(f"lyrembed ended with exit code {code}; the lyrics embedded before stay.")


def ask_romanize(session: Session) -> bool | None:
    """Whether automatic mode romanizes; None (Enter) leaves it to lyrembed's
    own settings.auto_romanize, so its config stays the default."""
    if not session.lyrembed_romanize:
        ui.dim("This lyrembed cannot romanize in automatic mode; update it for that.")
        return None
    answer = ui.choose("Romanize the lyrics", [
        ("l", "as set in lyrembed (settings.auto_romanize, off unless changed there)"),
        ("y", "yes: romanized, layout from lyrembed's settings.romanization_layout"),
        ("n", "no: as found"),
    ], default="l")
    return {"l": None, "y": True, "n": False}[answer]


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def finish_step(session: Session, album: AlbumState) -> bool:
    ui.step(5, "Finish")
    if _inside(album.folder, session.library_dir):
        ui.info("The album is already in the music library.")
        album.mark("finish")
        album.remove()
        return True
    files = tags.audio_files(album.folder)
    try:
        artist = tags.album_artist(files)
    except tags.TagReadError as exc:
        ui.warn(f"Cannot read the album artist: {exc}")
        artist = ""
    target = library.destination(session.library_dir, session.cfg["library_layout"], album.folder, artist)
    while True:
        exists = target.exists()
        merge = None
        ui.info(f"Library folder: {ui.tilde(target)}")
        if exists:
            try:
                merge = library.plan_disc_merge(album.folder, target)
            except (library.MoveError, tags.TagReadError, OSError) as exc:
                ui.warn(f"That folder holds this album, but this disc cannot join it: {exc}")
            if merge:
                ui.info(f"It holds disc {', '.join(merge.discs_there)} of this album; this is disc "
                        f"{', '.join(merge.discs_here)}.")
            else:
                ui.warn("That folder already exists; cdimport will not overwrite it.")
        if merge:
            options = [("a", f"add disc {', '.join(merge.discs_here)} to that folder")]
        else:
            options = [] if exists else [("m", "move the album there")]
        options += [("e", "enter another folder"), ("k", "keep it in the working folder")]
        answer = ui.choose("Move", options, default="a" if merge else "k" if exists else "m")
        if answer == "e":
            entered = ui.ask_path("Folder", ui.tilde(target))
            if entered:
                target = expand(entered)
            continue
        if answer == "k":
            album.mark("finish")
            album.remove()
            return True
        try:
            if merge:
                library.merge_disc(merge, target)
                album.remove()  # the state file stays behind in the working folder otherwise
                album.folder.rmdir()
            else:
                library.move_album(album.folder, target)
        except (library.MoveError, OSError) as exc:
            ui.error(f"Move failed: {exc}")
            continue
        album.folder = target
        album.mark("finish")
        album.remove()
        ui.success(f"Added disc {', '.join(merge.discs_here)} to {ui.tilde(target)}" if merge
                   else f"Moved to {ui.tilde(target)}")
        try:
            session.logs = library.keep_logs_out(target, session.work_dir / LOG_FOLDER)
        except OSError as exc:
            ui.warn(f"The rip log stayed in the library folder: {exc}")
        return True


# Rip logs of albums moved into the library, inside the working folder.
LOG_FOLDER = "logs"


def summary(session: Session, album: AlbumState) -> None:
    ui.step(None, "Summary")
    ui.field("Album", ui.tilde(album.folder))
    ui.field("Tracks", len(tags.audio_files(album.folder)))
    for name in ("rip", "tags", "lyrics", "finish"):
        if name in album.done:
            ui.check(name.capitalize(), "done", True)
        elif name in album.skipped:
            ui.check(name.capitalize(), "skipped", None)
        else:
            ui.check(name.capitalize(), "open: run cdimport again to continue", False)
    logs = session.logs or sorted(album.folder.glob("*.log"))
    if logs:
        ui.field("Log", ui.tilde(logs[0]))
