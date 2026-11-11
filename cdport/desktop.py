"""An app-menu entry for Linux and BSD desktops (freedesktop.org), as in lyrembed.

pipx and pip cannot install desktop files, so cdport writes its own entry
on request, pointing at the cdport that wrote it: a pipx launcher, or the
Python and run.py of a checkout. Run it again after moving to another
installation. The entry starts the page: from the menu there is no terminal
to answer in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ICON_SOURCE = Path(__file__).with_name("static") / "icon.svg"
ENTRY_NAME = "cdport.desktop"
# What cdimport (the old name) wrote: replaced by install, removed by remove.
OLD_ENTRY_NAME = "cdimport.desktop"
OLD_ICON_NAME = "cdimport.svg"
PAGE_ARGUMENTS = ["--web"]

# Characters that make an Exec argument need quotes (Desktop Entry spec,
# "The Exec key"); inside quotes, these four need a backslash.
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")
_ESCAPED_IN_QUOTES = re.compile(r'(["`$\\])')


class DesktopError(Exception):
    """The entry could not be written or removed."""


def supported() -> bool:
    return sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd"))


def _data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def entry_path() -> Path:
    return _data_home() / "applications" / ENTRY_NAME


def icon_path(name: str = "cdport.svg") -> Path:
    return _data_home() / "icons" / "hicolor" / "scalable" / "apps" / name


def old_entry() -> list[Path]:
    """cdimport's entry and icon, if cdimport wrote them: an entry by that
    name that someone else wrote is left alone."""
    entry = _data_home() / "applications" / OLD_ENTRY_NAME
    try:
        ours = "Name=cdimport\n" in entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    icon = icon_path(OLD_ICON_NAME)
    return [entry] + ([icon] if icon.exists() else []) if ours else []


def launch_command() -> list[str]:
    """How the running cdport was started, so the menu starts the same one."""
    started = Path(sys.argv[0]).resolve()
    if started.name == "__main__.py":
        # python -m cdport: the module is only found from its checkout.
        launcher = started.parent.parent / "run.py"
        return [sys.executable, str(launcher)] if launcher.is_file() else [sys.executable, "-m", "cdport"]
    if started.suffix == ".py":
        return [sys.executable, str(started)]
    return [str(started)]


def exec_argument(argument: str) -> str:
    """*argument* as the Exec key needs it: quoted when it has reserved
    characters, then with the string escapes every desktop-file value has.
    The spec applies the string escape first when reading, so a literal
    backslash in a quoted argument ends up as four."""
    if any(c in _RESERVED for c in argument):
        argument = '"' + _ESCAPED_IN_QUOTES.sub(r"\\\1", argument) + '"'
    return argument.replace("\\", "\\\\").replace("%", "%%")


def entry_text(command: list[str], icon: Path) -> str:
    # %f: a folder dropped on the launcher is an album ripped earlier.
    exec_line = " ".join(exec_argument(a) for a in command + PAGE_ARGUMENTS) + " %f"
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=cdport",
        "GenericName=CD ripper",
        "Comment=Rip an audio CD to a tagged FLAC album with lyrics",
        f"Exec={exec_line}",
        f"Icon={icon}",
        # The page opens in the browser; a terminal would only show its address.
        "Terminal=false",
        # The browser's window is not cdport's, so a startup spinner would
        # keep turning until it times out.
        "StartupNotify=false",
        "Categories=AudioVideo;Audio;",
        "Keywords=cd;rip;ripper;flac;cyanrip;musicbrainz;lyrics;",
        "",
    ])


def _refresh(applications: Path) -> None:
    """Some menus only notice a new entry after this; none needs it to work."""
    tool = shutil.which("update-desktop-database")
    if tool:
        try:
            subprocess.run([tool, str(applications)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


def install() -> tuple[Path, Path, list[str], str | None]:
    """Write the icon and the entry; returns their paths, the command, and
    why cdimport's old entry is still there (None when it is not)."""
    if not supported():
        raise DesktopError("App-menu entries are written for Linux and BSD desktops only.")
    command = launch_command()
    entry, icon = entry_path(), icon_path()
    try:
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ICON_SOURCE, icon)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(entry_text(command, icon), encoding="utf-8")
    except OSError as exc:
        raise DesktopError(f"Could not write the app-menu entry: {exc}") from exc
    # Two entries for one program; the old one starts the cdimport alias.
    # The new one works either way, so a failure here is only reported.
    try:
        _unlink(old_entry())
        leftover = None
    except DesktopError as exc:
        leftover = str(exc)
    _refresh(entry.parent)
    return entry, icon, command, leftover


def _unlink(paths: list[Path]) -> list[Path]:
    removed = []
    for path in paths:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DesktopError(f"Could not remove '{path}': {exc}") from exc
    return removed


def remove() -> list[Path]:
    """Delete the entry and the icon, and cdimport's; returns what was there."""
    removed = _unlink([entry_path(), icon_path(), *old_entry()])
    if removed:
        _refresh(entry_path().parent)
    return removed
