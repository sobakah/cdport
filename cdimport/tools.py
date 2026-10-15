"""Finding the external tools. Installing cyanrip is in install.py."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import proc
from .config import expand


def _find_executable(name: str, configured: str, prefer_local: bool = False) -> str | None:
    """A configured path, else PATH, else ~/.local/bin.

    ~/.local/bin is checked explicitly because pipx and a meson build with
    prefix ~/.local install there, and fish on Fedora does not have it on
    PATH unless the user added it.
    """
    if configured:
        path = expand(configured)
        return str(path) if path.is_file() else None
    local = Path.home() / ".local" / "bin" / name
    if prefer_local and local.is_file():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    if local.is_file():
        return str(local)
    return None


def find_cyanrip(cfg: dict[str, Any]) -> str | None:
    # A master build sits in ~/.local/bin, possibly next to a distribution
    # package in /usr/bin that PATH would find first.
    return _find_executable("cyanrip", cfg["cyanrip"]["path"],
                            prefer_local=cfg["cyanrip"]["channel"] == "master")


def find_lyrembed(cfg: dict[str, Any]) -> str | None:
    return _find_executable("lyrembed", cfg["lyrembed"]["path"])


def start_problem(cmd: list[str]) -> str | None:
    """Why a found tool does not run (e.g. 'lyrembed --version'), or None.

    A file on PATH is not enough: a pipx launcher whose Python was removed by
    a system upgrade still exists but cannot start (seen 2026-10-11 after
    Fedora moved from Python 3.14 to 3.15).
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None  # it started; slowness is not this check's business
    except OSError as exc:
        reason = exc.strerror or str(exc)
        if "pipx" in str(Path(cmd[0]).resolve()):
            reason += f" - its pipx environment is broken; repair it with: pipx reinstall {Path(cmd[0]).name}"
        return reason
    if result.returncode == 0:
        return None
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else f"exit code {result.returncode}"


# The options of 'flatpak run' that take a value, which may also follow as
# the next argument ("--arch x86_64"); from flatpak 1.16's 'flatpak run --help'.
_FLATPAK_VALUE_OPTIONS = frozenset(
    "--installation --arch --command --cwd --branch --runtime --runtime-version --parent-pid "
    "--app-path --app-fd --usr-path --usr-fd --bind-fd --ro-bind-fd --share --unshare --share-if "
    "--socket --nosocket --socket-if --device --nodevice --device-if --allow --disallow --allow-if "
    "--filesystem --nofilesystem --env --env-fd --unset-env --own-name --talk-name --no-talk-name "
    "--system-own-name --system-talk-name --system-no-talk-name --add-policy --remove-policy "
    "--usb --nousb --usb-list --usb-list-file --persist --instance-id-fd".split())


def _flatpak_app_id(cmd: list[str]) -> str | None:
    """The app ID in 'flatpak run [options] APP ...'."""
    if len(cmd) < 3 or Path(cmd[0]).name != "flatpak" or cmd[1] != "run":
        return None
    args = iter(cmd[2:])
    for arg in args:
        if arg in _FLATPAK_VALUE_OPTIONS:
            next(args, None)  # its value
        elif not arg.startswith("-"):
            return arg
    return None


def command_available(cmd: list[str]) -> bool:
    """Whether a command template can run: the program exists, and for
    'flatpak run APP' the app is installed."""
    if not shutil.which(cmd[0]):
        return False
    app_id = _flatpak_app_id(cmd)
    if app_id is None:
        return True
    result = proc.capture(["flatpak", "info", app_id])
    return result is not None and result.returncode == 0


@dataclass
class Editor:
    key: str
    name: str
    command: list[str]  # the first available template


def find_editor(cfg: dict[str, Any], key: str) -> Editor | None:
    entry = cfg["editors"].get(key)
    if not entry:
        return None
    for cmd in entry["commands"]:
        if command_available(cmd):
            return Editor(key, entry.get("name", key), cmd)
    return None


def available_editors(cfg: dict[str, Any]) -> list[Editor]:
    return [e for e in (find_editor(cfg, k) for k in cfg["editors"]) if e]


# --- system information -------------------------------------------------------
def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    """Parse os-release; empty when the file is missing (non-Linux)."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# The repository is private for now: this needs access to it on GitHub.
LYREMBED_INSTALL_HINT = "pipx install git+https://github.com/sobakah/lyrembed.git"
