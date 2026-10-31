"""A newer cdimport: found by the release tags on GitHub, installed by pipx.

Built after picktag's update check (core/selfupdate.py there, 2026-10-27).
The repository is private, so the check asks git (``git ls-remote``), which
uses the same access as ``pipx install`` (``gh auth setup-git``); it never
prompts for a password. A pipx install is updated by pipx: ``pipx upgrade``
for an install from the repository, ``pipx install --force`` with the new tag
for one pinned to a release (it stays pinned). Any other install gets the
command to run instead.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import __version__

NAME = "cdimport"
REPO = "https://github.com/sobakah/cdimport.git"
RELEASES = "https://github.com/sobakah/cdimport/releases"
_TAG = re.compile(r"refs/tags/(\d+)\.(\d+)\.(\d+)$")


class UpdateError(Exception):
    """GitHub cannot be asked (no git, no network, no access)."""


def parse(version: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    return tuple(int(n) for n in match.groups()) if match else None


def _git_env() -> dict[str, str]:
    """No password prompt, neither in the terminal nor in a window."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_ASKPASS", "SSH_ASKPASS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def latest(timeout: float = 30) -> str:
    """The newest release tag (x.y.z) of cdimport's repository."""
    git = shutil.which("git")
    if git is None:
        raise UpdateError("git isn't installed")
    try:
        result = subprocess.run([git, "-c", "core.askPass=", "ls-remote", "--tags", "--refs", REPO],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                                env=_git_env(), start_new_session=True)
    except subprocess.TimeoutExpired:
        raise UpdateError("GitHub didn't answer") from None
    except OSError as exc:
        raise UpdateError(f"git didn't start: {exc}") from None
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or [f"git ended with code {result.returncode}"])[-1]
        raise UpdateError(last.removeprefix("fatal: "))
    tags = [tuple(int(n) for n in m.groups()) for line in result.stdout.splitlines() if (m := _TAG.search(line))]
    if not tags:
        raise UpdateError("no releases found")
    return ".".join(map(str, max(tags)))


def newer(version: str, than: str = __version__) -> bool:
    a, b = parse(version), parse(than)
    return a is not None and b is not None and a > b


@dataclass
class Install:
    """How this cdimport was installed and how to update it: argv is run by
    cdimport (a pipx install), command is shown either way."""
    kind: str                     # "pipx", "checkout" or "other"
    command: str
    argv: list[str] | None = None


def pipx_metadata(prefix: str | Path | None = None) -> dict | None:
    try:
        return json.loads((Path(prefix or sys.prefix) / "pipx_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def how_installed(version: str, prefix: str | Path | None = None,
                  package_dir: Path = Path(__file__).resolve().parent) -> Install:
    """The way to version for this install (prefix: its venv, sys.prefix)."""
    meta = pipx_metadata(prefix)
    if meta is not None:
        main = meta.get("main_package") or {}
        name = main.get("package") or NAME
        url = main.get("package_or_url") or ""
        pipx = shutil.which("pipx") or "pipx"
        pinned = re.match(r"(.*git\+.+?\.git)@[^/]+$", url)
        if pinned:  # a release named in the address: the new one, still pinned
            argv = [pipx, "install", "--force", *meta.get("venv_args", []),
                    *(["--pip-args", shlex.join(main["pip_args"])] if main.get("pip_args") else []),
                    f"{pinned.group(1)}@{version}"]
        else:
            argv = [pipx, "upgrade", name]
        return Install("pipx", shlex.join(["pipx", *argv[1:]]), argv)
    top = package_dir.parent
    if (top / ".git").exists():
        return Install("checkout", f"git -C {shlex.quote(str(top))} pull")
    return Install("other", f"pipx install --force git+{REPO}@{version}")


def remember(state_file: Path, found: str) -> str | None:
    """Keep the release GitHub named, for a start that can't wait for the
    answer (known); the reason when that fails."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # whole or not at all: step 0 may read it while the background check writes it
        part = state_file.with_name(f"{state_file.name}.{os.getpid()}.part")
        part.write_text(json.dumps({"checked": time.time(), "latest": found}) + "\n", encoding="utf-8")
        os.replace(part, state_file)
    except OSError as exc:
        return str(exc)
    return None


def known(state_file: Path) -> str | None:
    """The newer release an earlier check found, without asking GitHub."""
    try:
        found = json.loads(state_file.read_text(encoding="utf-8")).get("latest")
    except (OSError, ValueError, AttributeError):
        return None
    return found if isinstance(found, str) and newer(found) else None


def restart_argv() -> list[str]:
    """The command cdimport was started with, to start it again after an update."""
    first = Path(sys.argv[0])
    # shutil.which: on Windows the launcher sets sys.argv[0] without its .exe.
    launcher = shutil.which(str(first)) if first.name == NAME else None
    if launcher:
        return [launcher, *sys.argv[1:]]
    if first.name == "__main__.py":  # python -m cdimport
        return [sys.executable, "-m", NAME, *sys.argv[1:]]
    return [sys.executable, *sys.argv]
