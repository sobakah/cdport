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
        prog="cdport",
        description="Rip an audio CD to tagged FLAC with cyanrip, revise the tags in a tag editor, "
                    "embed lyrics with lyrembed and move the album into the music library.",
    )
    parser.add_argument("folder", nargs="?", type=Path,
                        help="continue with an album ripped earlier (tags, lyrics, finish) instead of a disc")
    parser.add_argument("--version", action="version", version=f"cdport {__version__}")
    jump = parser.add_mutually_exclusive_group()
    jump.add_argument("--step", choices=STEP_CHOICES,
                      help="with FOLDER: begin with this step instead of the next open one; the others follow in order")
    jump.add_argument("--edit-tags", action="store_true",
                      help="with FOLDER: only edit its tags (and cover), then end; leaves no trace in the folder")
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
                        help="look for a newer cdport now and wait for the answer (else in the background) and offer to install it")
    parser.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    page = parser.add_argument_group("browser (preview)")
    page.add_argument("--web", action="store_true",
                      help="work on a page in the browser instead of the terminal (only this computer can open it)")
    page.add_argument("--no-browser", action="store_true", help="with --web, print the page's address instead of opening it")
    page.add_argument("--return-url", metavar="URL",
                      help="with --web: when done, a button leads back to this local address (lyrembed's page)")
    # For dbus_service's page runs: end when nobody opens the page.
    page.add_argument("--unvisited-timeout", type=float, default=0, metavar="SECONDS", help=argparse.SUPPRESS)
    page.add_argument("--return-name", metavar="NAME", default="",
                      help="with --return-url, the name on that button (default: 'the previous page')")
    entry = page.add_mutually_exclusive_group()
    entry.add_argument("--install-desktop-entry", action="store_true",
                       help="add cdport to the app menu (Linux/BSD), starting this cdport's page, then exit")
    entry.add_argument("--remove-desktop-entry", action="store_true", help="remove cdport from the app menu, then exit")
    bus = parser.add_argument_group("D-Bus (for Jewelcase and other programs, also from a Flatpak)")
    service = bus.add_mutually_exclusive_group()
    service.add_argument("--install-dbus-service", action="store_true",
                         help="let the session bus start this cdport when a program calls it, then exit")
    service.add_argument("--remove-dbus-service", action="store_true", help="undo --install-dbus-service, then exit")
    service.add_argument("--dbus", action="store_true",
                         help="serve the D-Bus interface until idle (what the bus starts)")
    return parser


# The steps --step can begin with: the album's, and its cover.
STEP_CHOICES = ("cover", "tags", "lyrics", "finish")


def local_page(url: str) -> bool:
    """Whether *url* is a page on this computer: the only kind the page may
    lead back to, so cdport cannot be used to send a tab elsewhere."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost") and parts.port is not None
    except ValueError:
        return False


# After an update on the page: the new cdport takes over the page's address
# and token, so its open tab still fits. Taken out of the environment at once,
# as tools started from the page must not see the token.
RESTART_ENV = "CDPORT_RESTART"
# What cdimport (the old name) set when it updated itself to cdport on the page.
OLD_RESTART_ENV = "CDIMPORT_RESTART"


def _take_restart() -> tuple[int, str]:
    value = os.environ.pop(RESTART_ENV, "")
    old = os.environ.pop(OLD_RESTART_ENV, "")
    port, _, token = (value or old).partition(":")
    return (int(port), token) if port.isdigit() and token else (0, "")


def _restart(restart: app.Restart) -> int:
    """Start the cdport just installed in this process's place."""
    sys.stdout.flush()
    sys.stderr.flush()
    if restart.address:
        os.environ[RESTART_ENV] = restart.address
    try:
        os.execvp(restart.argv[0], restart.argv)
    except OSError as exc:
        os.environ.pop(RESTART_ENV, None)
        ui.error(f"Could not start the new cdport ({exc}); start it again yourself.")
        return 1
    return 0  # pragma: no cover - execvp does not return


def main(argv: list[str] | None = None) -> int:
    port, token = _take_restart()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.install_desktop_entry or args.remove_desktop_entry:
        return _desktop_entry(install=args.install_desktop_entry)
    if args.dbus:
        from . import dbus_service

        return dbus_service.serve()
    if args.install_dbus_service or args.remove_dbus_service:
        return _dbus_service(install=args.install_dbus_service)

    moved = config.move_old_config()
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
    if args.unvisited_timeout and not args.web:
        parser.error("--unvisited-timeout only applies together with --web")
    if (args.step or args.edit_tags) and not args.folder:
        parser.error(f"--{'step' if args.step else 'edit-tags'} needs an album FOLDER")
    if args.update and args.return_url:
        parser.error("--update does not apply with --return-url")
    if args.editor and args.editor != "skip" and args.editor not in cfg["editors"]:
        parser.error(f"unknown editor '{args.editor}' (known: {', '.join(cfg['editors'])}, skip)")

    ui.setup_color(cfg["color"])
    if moved:
        (ui.info if moved[0] else ui.warn)(moved[1])
    for message in warnings:
        ui.warn(f"Warning: {message}")

    options = app.Options(folder=args.folder, device=args.device or "",
                          lyrics=args.lyrics or "", editor=args.editor or "",
                          install_cyanrip=args.install_cyanrip, rip_mode=args.rip_mode or "",
                          step=args.step or "", edit_tags=args.edit_tags,
                          # Started by another program's page: that one stays in charge.
                          self_update="now" if args.update else "never" if args.return_url
                          else "start" if cfg["check_for_updates"] else "never")
    session = app.new_session(cfg, options, args.config)
    try:
        if args.web:
            from . import web  # not needed in the terminal

            return web.serve(session, open_browser=not args.no_browser, port=port, token=token,
                             back=(args.return_url, args.return_name) if args.return_url else None,
                             warnings=([moved[1]] if moved and not moved[0] else [])
                             + [f"Warning: {message}" for message in warnings],
                             notes=[moved[1]] if moved and moved[0] else [],
                             unvisited=args.unvisited_timeout)
        return run_session(session)
    except app.Restart as restart:
        return _restart(restart)


def _desktop_entry(install: bool) -> int:
    from . import desktop

    try:
        if install:
            entry, icon, command, leftover = desktop.install()
            ui.success(f"Added cdport to the app menu: {entry}")
            if leftover:
                ui.warn(f"cdimport's old entry is still there: {leftover}")
            ui.info(f"It starts: {' '.join(command + desktop.PAGE_ARGUMENTS)}")
            ui.info("Run this again after installing cdport elsewhere (pipx, another checkout).")
        else:
            removed = desktop.remove()
            ui.success("Removed: " + ", ".join(str(p) for p in removed) if removed else "There was no app-menu entry.")
    except desktop.DesktopError as exc:
        ui.error(str(exc))
        return 2
    return 0


def _dbus_service(install: bool) -> int:
    from . import dbus_service

    try:
        if install:
            path, command, unreloaded = dbus_service.install_service_file()
            ui.success(f"The session bus starts cdport for {dbus_service.BUS_NAME}: {path}")
            ui.info(f"It starts: {' '.join(command)} --dbus")
            if unreloaded:
                ui.warn(f"Could not tell the bus to read it ({unreloaded}); it does at the next login.")
            ui.info("Run this again after installing cdport elsewhere (pipx, another checkout).")
        else:
            removed = dbus_service.remove_service_file()
            ui.success(f"Removed: {removed}" if removed else "There was no D-Bus service file.")
    except OSError as exc:
        ui.error(f"Could not {'write' if install else 'remove'} the D-Bus service file: {exc}")
        return 2
    return 0


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
        ui.info(f"Finished steps are saved. Run cdport again to continue with {album.folder.name}.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
