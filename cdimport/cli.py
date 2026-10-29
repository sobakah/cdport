"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import app, config, ui
from .config import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdimport",
        description="Rip an audio CD to tagged FLAC with cyanrip, revise the tags in a tag editor, "
                    "embed lyrics with lyrembed and move the album into the music library.",
    )
    parser.add_argument("folder", nargs="?", type=Path,
                        help="continue with an album ripped earlier (tags, lyrics, finish) instead of a disc")
    parser.add_argument("--version", action="version", version=f"cdimport {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE",
                        help=f"config file (default: {config.default_config_path()})")
    parser.add_argument("-w", "--work-dir", metavar="DIR", help="working folder for this run (default: config work_dir, asked on first use)")
    parser.add_argument("-l", "--library", metavar="DIR", help="music library for this run (default: config library_dir, asked on first use)")
    parser.add_argument("-d", "--device", metavar="DEV", help="CD drive, e.g. /dev/sr0")
    parser.add_argument("--editor", metavar="NAME",
                        help="tag editor from the config's 'editors' (eartag, kid3, picard) or 'skip'")
    parser.add_argument("--lyrics", choices=("auto", "interactive", "skip"),
                        help="lyrics mode for this run (default: ask)")
    parser.add_argument("--rip-mode", choices=config.RIP_MODES,
                        help="fast: no paranoia, checked with AccurateRip, mismatching tracks ripped again "
                             "securely; secure: paranoia for every track (default: config cyanrip.mode)")
    parser.add_argument("--install-cyanrip", action="store_true",
                        help="install cyanrip, or update and rebuild a source build, before starting")
    parser.add_argument("--update", action="store_true",
                        help="look for a newer cdimport now and wait for the answer (else in the background) and offer to install it")
    parser.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    page = parser.add_argument_group("browser (preview)")
    page.add_argument("--web", action="store_true",
                      help="work on a page in the browser instead of the terminal (only this computer can open it)")
    page.add_argument("--no-browser", action="store_true", help="with --web, print the page's address instead of opening it")
    page.add_argument("--return-url", metavar="URL",
                      help="with --web: when done, a button leads back to this local address (lyrembed's page)")
    page.add_argument("--return-name", metavar="NAME", default="",
                      help="with --return-url, the name on that button (default: 'the previous page')")
    return parser


def local_page(url: str) -> bool:
    """Whether *url* is a page on this computer: the only kind the page may
    lead back to, so cdimport cannot be used to send a tab elsewhere."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost") and parts.port is not None
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg, warnings = config.load_config(args.config)
    except config.ConfigError as exc:
        ui.error(str(exc))
        return 2
    if args.work_dir:
        cfg["work_dir"] = args.work_dir
    if args.library:
        cfg["library_dir"] = args.library
    if args.color:
        cfg["color"] = args.color
    if args.no_browser and not args.web:
        parser.error("--no-browser only applies together with --web")
    if (args.return_url or args.return_name) and not args.web:
        parser.error("--return-url and --return-name only apply together with --web")
    if args.return_name and not args.return_url:
        parser.error("--return-name only applies together with --return-url")
    if args.return_url and not local_page(args.return_url):
        parser.error("--return-url must be a page on this computer, such as http://127.0.0.1:8000/")
    if args.web and args.install_cyanrip:
        parser.error("--install-cyanrip needs the terminal (it may ask for your password); run it without --web")
    if args.update and args.return_url:
        parser.error("--update does not apply with --return-url")
    if args.editor and args.editor != "skip" and args.editor not in cfg["editors"]:
        parser.error(f"unknown editor '{args.editor}' (known: {', '.join(cfg['editors'])}, skip)")

    ui.setup_color(cfg["color"])
    for message in warnings:
        ui.warn(f"Warning: {message}")

    options = app.Options(folder=args.folder, device=args.device or "",
                          lyrics=args.lyrics or "", editor=args.editor or "",
                          install_cyanrip=args.install_cyanrip, rip_mode=args.rip_mode or "",
                          # Started by another program's page: that one stays in charge.
                          self_update="now" if args.update else "never" if args.return_url
                          else "start" if cfg["check_for_updates"] else "never")
    session = app.new_session(cfg, options, args.config)
    if args.web:
        from . import web  # not needed in the terminal

        return web.serve(session, open_browser=not args.no_browser,
                         back=(args.return_url, args.return_name) if args.return_url else None,
                         warnings=[f"Warning: {message}" for message in warnings])
    try:
        return run_session(session)
    except app.Restart as restart:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp(restart.argv[0], restart.argv)
        return 0  # pragma: no cover - execvp does not return


def run_session(session: app.Session) -> int:
    """The flow with its ending: the exit code for quitting, Ctrl+C (130) or
    a finished run. The page runs it too, in a thread of its own."""
    try:
        return app.run(session)
    except app.Quit as stop:
        _resume_hint(session)
        return stop.code
    except KeyboardInterrupt:
        if ui.in_terminal():  # the cursor is still after ^C
            print()
        ui.warn("Stopped.")
        _resume_hint(session)
        return 130


def _resume_hint(session: app.Session) -> None:
    album = session.album
    if album and album.next_step() and album.path.exists():
        ui.info(f"Finished steps are saved. Run cdimport again to continue with {album.folder.name}.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
