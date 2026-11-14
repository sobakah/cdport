"""Installing cyanrip: the distro package where one exists, else a source build.

Nothing runs without the user seeing the exact command and saying yes; sudo
is used only for system package managers. Packaging status is from Repology
on 2026-10-11, the dependency list from cyanrip's src/meson.build of the same
day - re-check both rather than trust them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config, container, proc, tools, ui

REPO_URL = "https://github.com/cyanreg/cyanrip.git"
DEFAULT_SOURCE_DIR = "~/.local/src/cyanrip"
# The channel besides releases is the master branch. It is not called
# "nightly": the repository's tag of that name is from 2020 and GitHub has no
# nightly release (checked 2026-10-11).
MASTER_BRANCH = "master"
RELEASE_TAG = re.compile(r"^v(\d+(?:\.\d+)+)$")
CHECK_INTERVAL_SECONDS = 24 * 3600

# pkg-config name and minimum version, from src/meson.build.
REQUIRED_LIBS = [
    ("libavcodec", "59.24.100"), ("libavformat", "58.13.100"), ("libswresample", "4.5.100"),
    ("libavfilter", "7.16.100"), ("libavutil", "57.25.100"), ("libcdio", "2.0"),
    ("libcdio_paranoia", "10.2"), ("libmusicbrainz5", "5.1"), ("libcurl", "7.66.0"),
]
# What cyanrip says it does without them.
OPTIONAL_LIBS = [
    ("libqrencode", "4.0", "show MusicBrainz links as QR codes"),
    ("libchromaprint", "1.4", "fingerprint tracks for AcoustID"),
]
BUILD_TOOLS = ["git", "pkg-config", "meson", "ninja", "cc"]

FEDORA_PACKAGES = {
    "git": "git", "pkg-config": "pkgconf-pkg-config", "meson": "meson", "ninja": "ninja-build",
    "cc": "gcc", "libcdio": "libcdio-devel", "libcdio_paranoia": "libcdio-paranoia-devel",
    "libmusicbrainz5": "libmusicbrainz5-devel", "libcurl": "libcurl-devel",
    "libqrencode": "qrencode-devel", "libchromaprint": "libchromaprint-devel",
}
_FFMPEG_LIBS = {"libavcodec", "libavformat", "libswresample", "libavfilter", "libavutil"}
# Debian/Ubuntu and Arch names, checked 2026-10-27 in the debian:trixie,
# ubuntu:24.04 and archlinux images (Arch's packages carry their headers).
DEBIAN_PACKAGES = {
    "git": "git", "pkg-config": "pkgconf", "meson": "meson", "ninja": "ninja-build", "cc": "gcc",
    **{name: f"{name}-dev" for name in _FFMPEG_LIBS},
    "libcdio": "libcdio-dev", "libcdio_paranoia": "libcdio-paranoia-dev",
    "libmusicbrainz5": "libmusicbrainz5-dev", "libcurl": "libcurl4-openssl-dev",
    "libqrencode": "libqrencode-dev", "libchromaprint": "libchromaprint-dev",
}
ARCH_PACKAGES = {
    "git": "git", "pkg-config": "pkgconf", "meson": "meson", "ninja": "ninja", "cc": "gcc",
    **{name: "ffmpeg" for name in _FFMPEG_LIBS},
    "libcdio": "libcdio", "libcdio_paranoia": "libcdio-paranoia", "libmusicbrainz5": "libmusicbrainz5",
    "libcurl": "curl", "libqrencode": "qrencode", "libchromaprint": "chromaprint",
}
# zypper finds libraries by their pkg-config name; with the minimum version,
# as Tumbleweed offers FFmpeg 4 to 9 for each (checked 2026-10-27).
SUSE_TOOLS = {"git": "git", "pkg-config": "pkgconf-pkg-config", "meson": "meson", "ninja": "ninja", "cc": "gcc"}
_MIN_VERSIONS = {name: version for name, version, *_ in REQUIRED_LIBS + OPTIONAL_LIBS}


IMAGE_BASED = ("cyanrip can't be built on an image-based system such as Fedora Silverblue or Bluefin: "
               "install cdport in a toolbox and start it there, as its app menu entry made there does "
               "(see cdport's README)")


@dataclass
class Route:
    kind: str  # "package", "source", "download" or "elsewhere" (cdport can't install it here)
    description: str
    command: list[str] | None = None


def _sudo(cmd: list[str]) -> list[str]:
    if sys.platform != "win32" and os.geteuid() == 0:
        return cmd
    return ["sudo"] + cmd


def _distro_ids(os_release: dict[str, str]) -> list[str]:
    return [os_release.get("ID", "")] + os_release.get("ID_LIKE", "").split()


def choose_route(os_release: dict[str, str] | None = None, platform: str | None = None,
                 which: Callable[[str], str | None] = shutil.which, channel: str = "release") -> Route:
    """How cyanrip gets onto this system."""
    platform = platform or sys.platform
    if platform == "win32":
        if channel == "master":
            return Route("download", "No cyanrip.exe of master is published; build it yourself or "
                                     "set \"channel\": \"release\"")
        return Route("download", "Download the latest cyanrip.exe from "
                                 "https://github.com/cyanreg/cyanrip/releases (automatic download comes later)")
    if platform.startswith("linux") and container.image_based_host():
        return Route("elsewhere", IMAGE_BASED)
    if channel == "master":
        # Packages are releases; master only comes from source.
        return Route("source", "master is built from source into ~/.local")
    if platform.startswith("freebsd"):
        return Route("package", "FreeBSD package", _sudo(["pkg", "install", "cyanrip"]))
    if os_release is None:
        os_release = tools.read_os_release()
    packages = {
        "debian": _sudo(["apt", "install", "cyanrip"]),
        "ubuntu": _sudo(["apt", "install", "cyanrip"]),
        "opensuse-tumbleweed": _sudo(["zypper", "install", "cyanrip"]),
        "alpine": _sudo(["apk", "add", "cyanrip"]),
        "void": _sudo(["xbps-install", "cyanrip"]),
        "nixos": ["nix", "profile", "install", "nixpkgs#cyanrip"],
    }
    for distro_id in _distro_ids(os_release):
        if distro_id in packages:
            return Route("package", "distribution package", packages[distro_id])
        if distro_id == "arch":
            # AUR only; the helpers ask for sudo themselves.
            for helper in ("yay", "paru"):
                if which(helper):
                    return Route("package", "AUR package", [helper, "-S", "cyanrip"])
    return Route("source", "no package for this system: build from source into ~/.local")


def route_hint(route: Route) -> str:
    return proc.format_command(route.command) if route.command else route.description


# --- dependencies ------------------------------------------------------------
def missing_libs(libs: list[tuple]) -> list[str]:
    """pkg-config names that are missing or too old. Without pkg-config
    nothing can be checked, so everything counts as missing."""
    missing = []
    for name, version, *_ in libs:
        result = proc.capture(["pkg-config", "--exists", f"{name} >= {version}"])
        if result is None or result.returncode != 0:
            missing.append(name)
    return missing


def missing_tools(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    return [t for t in BUILD_TOOLS if not which(t)]


def fedora_packages(names: list[str]) -> list[str]:
    """Fedora package names for missing tools and pkg-config libraries.

    FFmpeg's headers must match the installed FFmpeg: RPM Fusion's ffmpeg-libs
    needs ffmpeg-devel, Fedora's own ffmpeg-free its -free-devel packages,
    which 'pkgconfig(...)' lets dnf pick.
    """
    rpmfusion_ffmpeg = None
    packages: list[str] = []
    for name in names:
        if name in _FFMPEG_LIBS:
            if rpmfusion_ffmpeg is None:
                result = proc.capture(["rpm", "-q", "ffmpeg-libs"])
                rpmfusion_ffmpeg = result is not None and result.returncode == 0
            package = "ffmpeg-devel" if rpmfusion_ffmpeg else f"pkgconfig({name})"
        else:
            package = FEDORA_PACKAGES.get(name, f"pkgconfig({name})")
        if package not in packages:
            packages.append(package)
    return packages


def _mapped(table: dict[str, str], names: list[str]) -> list[str]:
    return list(dict.fromkeys(table[name] for name in names))


def _suse_packages(names: list[str]) -> list[str]:
    return [SUSE_TOOLS[name] if name in SUSE_TOOLS else f"pkgconfig({name}) >= {_MIN_VERSIONS[name]}"
            for name in names]


def dependency_command(os_release: dict[str, str], names: list[str]) -> tuple[str, list[str]] | None:
    """The package manager and the command that installs these build tools
    and pkg-config libraries; None on a system cdport has no names for."""
    for distro_id in _distro_ids(os_release):
        if distro_id in ("fedora", "rhel", "centos"):
            return "dnf", _sudo(["dnf", "install"] + fedora_packages(names))
        if distro_id in ("debian", "ubuntu"):
            return "apt", _sudo(["apt", "install"] + _mapped(DEBIAN_PACKAGES, names))
        if distro_id == "arch":
            return "pacman", _sudo(["pacman", "-S", "--needed"] + _mapped(ARCH_PACKAGES, names))
        if distro_id in ("suse", "opensuse") or distro_id.startswith("opensuse-"):
            return "zypper", _sudo(["zypper", "install"] + _suse_packages(names))
    return None


def run_package_manager(cmd: list[str], what: str) -> bool:
    """Run a package manager's command after a yes; True when it ran.

    It asks for a sudo password and its own yes. The terminal passes both
    on; a page cannot, so there the user runs the command in a terminal and
    says when it has finished. The caller checks what it installed.
    """
    if ui.in_terminal():
        if not ui.confirm("Run this command?", default=True):
            return False
        if proc.run(cmd) != 0:
            ui.error(f"{what} failed.")
            return False
        return True
    ui.info("Run it in a terminal: it asks for your password and a yes, which this page cannot pass on.")
    return ui.confirm("Has it finished?", default=True)


def install_dependencies(os_release: dict[str, str]) -> bool:
    """Make sure everything the build needs is there; True when it is."""
    tools_missing = missing_tools()
    libs_missing = missing_libs(REQUIRED_LIBS)
    optional_names = missing_libs(OPTIONAL_LIBS)
    optional_missing = [lib for lib in OPTIONAL_LIBS if lib[0] in optional_names]
    if not (tools_missing or libs_missing or optional_missing):
        return True

    if dependency_command(os_release, []):
        wanted = tools_missing + libs_missing
        if optional_missing:
            ui.info("Optional for cyanrip:")
            for name, _, purpose in optional_missing:
                ui.info(f"  {name}: {purpose}")
            if ui.confirm("Install the optional libraries too?", default=False):
                wanted += [name for name, *_ in optional_missing]
        found = dependency_command(os_release, wanted) if wanted else None
        if found:
            manager, cmd = found
            ui.info("Build dependencies to install:")
            ui.info(f"  {proc.format_command(cmd)}")
            ui.dim(f"{manager} lists what it will install before it changes anything.")
            if not run_package_manager(cmd, "Installing the build dependencies"):
                return False
    else:
        ui.warn("Install these with your package manager first:")
        for name in tools_missing:
            ui.warn(f"  program {name}")
        for name in libs_missing:
            ui.warn(f"  development files for {name} (pkg-config name)")
        for name, _, purpose in optional_missing:
            ui.dim(f"  optional: {name} - {purpose}")
        if tools_missing or libs_missing:
            ui.wait_for_enter("Press Enter when they are installed")

    still_tools, still_libs = missing_tools(), missing_libs(REQUIRED_LIBS)
    if still_tools or still_libs:
        ui.error("Still missing: " + ", ".join(still_tools + still_libs))
        return False
    return True


# --- source build ------------------------------------------------------------
def check_source_dir(path: Path) -> str:
    """"clone" (absent or empty), "update" (a cyanrip checkout) or an error message."""
    if not path.exists() or (path.is_dir() and not any(path.iterdir())):
        return "clone"
    if not path.is_dir():
        return f"{path} is a file"
    if not (path / ".git").exists():
        return f"{path} is not empty and not a git checkout"
    result = proc.capture(["git", "-C", str(path), "remote", "get-url", "origin"])
    if result is None or result.returncode != 0 or "cyanrip" not in result.stdout:
        return f"{path} is a git checkout of something other than cyanrip"
    return "update"


def check_build_dir(build: Path, source: Path) -> str | None:
    """None when meson can use the folder, else what is wrong with it."""
    if build.resolve() == source.resolve():
        return "the build folder must differ from the source folder"
    if build.exists() and not build.is_dir():
        return f"{build} is a file"
    if build.is_dir() and any(build.iterdir()) and not (build / "meson-private").is_dir():
        return f"{build} is not empty and not a meson build folder"
    return None


def _build_prefill(cfg: dict[str, Any], source: Path, source_text: str) -> str:
    """The remembered build folder if it belongs to this source, else source/build."""
    remembered = cfg["cyanrip_source_dir"]
    if cfg["cyanrip_build_dir"] and remembered and config.expand(remembered) == source:
        return ui.tilde(config.expand(cfg["cyanrip_build_dir"]))
    return os.path.join(source_text, "build")


def ask_dirs(cfg: dict[str, Any]) -> tuple[Path, str, Path] | None:
    """Ask for the source and build folders, both prefilled and editable.

    Returns (source, "clone" or "update", build), or None when the user
    clears a line to cancel.
    """
    ui.info("Where should cyanrip be cloned and built? Both folders stay for updates and rebuilds.")
    ui.dim("Edit the suggestion or press Enter to accept it; clear the line to cancel.")
    prefill = ui.tilde(config.expand(cfg["cyanrip_source_dir"] or DEFAULT_SOURCE_DIR))
    while True:
        source_text = ui.ask_path("Source folder (git clone)", prefill)
        if not source_text:
            return None
        source = config.expand(source_text)
        state = check_source_dir(source)
        if state in ("clone", "update"):
            break
        ui.error(state)
        prefill = source_text
    prefill = _build_prefill(cfg, source, source_text)
    while True:
        build_text = ui.ask_path("Build folder (meson)", prefill)
        if not build_text:
            return None
        build = config.expand(build_text)
        problem = check_build_dir(build, source)
        if problem is None:
            return source, state, build
        ui.error(problem)
        prefill = build_text


def build_commands(source: Path, state: str, build: Path,
                   tag: str | None = None) -> list[tuple[list[str], Path | None]]:
    """The commands of a source build, each with its working folder.

    tag is the release to build; None builds master.
    """
    git = ["git", "-C", str(source)]
    if state == "clone":
        fetch = [["git", "clone", REPO_URL, str(source)]]
    else:
        fetch = [git + ["fetch", "--tags", "origin"]]
    if tag:
        fetch.append(git + ["checkout", "--detach", tag])
    elif state == "update":
        fetch += [git + ["checkout", MASTER_BRANCH], git + ["merge", "--ff-only", f"origin/{MASTER_BRANCH}"]]
    prefix = str(Path.home() / ".local")
    setup = ["meson", "setup", str(build), f"--prefix={prefix}", "--buildtype=release"]
    if (build / "meson-private").is_dir():
        setup.append("--reconfigure")
    return ([(cmd, None) for cmd in fetch]
            + [(setup, source), (["meson", "compile", "-C", str(build)], source),
               (["meson", "install", "-C", str(build)], source)])


def build_from_source(cfg: dict[str, Any], config_path: Path, os_release: dict[str, str]) -> str | None:
    channel = cfg["cyanrip"]["channel"]
    tag = None
    if channel == "release":
        try:
            tag = remote_info(max_age=0).latest_tag
        except UpdateCheckError as exc:
            ui.error(f"Cannot find the latest cyanrip release: {exc}")
            return None
        if tag is None:
            ui.error("cyanrip's repository has no release tags.")
            return None
    ui.info(f"cdport can build cyanrip {tag or MASTER_BRANCH} from source into ~/.local.")
    if not install_dependencies(os_release):
        return None
    chosen = ask_dirs(cfg)
    if chosen is None:
        return None
    source, state, build = chosen
    commands = build_commands(source, state, build, tag)
    ui.info("Commands to run:")
    for cmd, cwd in commands:
        ui.info(f"  {proc.format_command(cmd)}" + (f"   (in {cwd})" if cwd else ""))
    if not ui.confirm("Run them?", default=True):
        return None
    if state == "clone":
        source.parent.mkdir(parents=True, exist_ok=True)
    for cmd, cwd in commands:
        ui.tool_start(Path(cmd[0]).name, proc.format_command(cmd))
        code = proc.run_shown(cmd, cwd=cwd)
        ui.tool_end(Path(cmd[0]).name, code)
        if code != 0:
            ui.error(f"Failed (exit code {code}): {proc.format_command(cmd)}\n"
                     f"The source stays in {source}; fix the problem and install again.")
            if tag and cmd[0] == "meson":
                # Seen 2026-10-11: v0.9.3.1 does not compile against FFmpeg 9.
                ui.warn("Releases can lag behind new FFmpeg versions. To build master instead, set "
                        f"\"cyanrip\": {{\"channel\": \"master\"}} in {config_path}")
            return None

    folders = {"cyanrip_source_dir": ui.tilde(source), "cyanrip_build_dir": ui.tilde(build)}
    cfg.update(folders)
    try:
        config.remember(config_path, folders)
        ui.dim(f"Source and build folder saved in {config_path}")
    except (OSError, ValueError) as exc:
        ui.warn(f"Could not save the folders in {config_path}: {exc}")
    return _found_after_install(cfg)


def _found_after_install(cfg: dict[str, Any]) -> str | None:
    found = tools.find_cyanrip(cfg)
    if not found:
        ui.error("The install finished, but cyanrip still cannot be found.")
        return None
    ui.success(f"cyanrip installed: {found}")
    local_bin = str(Path.home() / ".local" / "bin")
    on_path = local_bin in os.environ.get("PATH", "").split(os.pathsep)
    if found.startswith(local_bin) and not on_path:
        hint = ("fish_add_path ~/.local/bin" if os.environ.get("SHELL", "").endswith("fish")
                else "add ~/.local/bin to PATH")
        ui.dim(f"~/.local/bin is not on PATH; cdport finds it anyway. For the shell: {hint}")
    return found


def install_package(cfg: dict[str, Any], route: Route) -> str | None:
    assert route.command
    ui.info(f"cyanrip is available as a {route.description}:")
    ui.info(f"  {proc.format_command(route.command)}")
    if not run_package_manager(route.command, "Installing cyanrip"):
        return None
    return _found_after_install(cfg)


def install_cyanrip(cfg: dict[str, Any], config_path: Path, os_release: dict[str, str] | None = None,
                    platform: str | None = None) -> str | None:
    """Install or rebuild cyanrip; returns its path, None when not installed."""
    if os_release is None:
        os_release = tools.read_os_release()
    route = choose_route(os_release, platform, channel=cfg["cyanrip"]["channel"])
    if route.kind in ("download", "elsewhere"):
        ui.info(route.description)
        return None
    if route.kind == "package":
        return install_package(cfg, route)
    return build_from_source(cfg, config_path, os_release)


# --- versions and updates ----------------------------------------------------
class UpdateCheckError(Exception):
    """GitHub cannot be asked (no git, no network)."""


@dataclass
class Installed:
    version: str          # "0.9.4-rc3"
    commit: str | None    # "b40c1f0" for builds from git


@dataclass
class Remote:
    latest_tag: str | None  # "v0.9.3.1"
    master: str | None      # full commit hash


@dataclass
class UpdateOffer:
    message: str
    can_build: bool  # cdport can rebuild it; else the package manager's job
    daily: bool      # a news item, shown at most once a day


# Pre-release markers, oldest first; they sort below the release itself.
_PRE_RELEASES = ("dev", "alpha", "beta", "pre", "rc")
_PRE_RELEASE = re.compile(r"[-._~]?(" + "|".join(_PRE_RELEASES) + r")\.?(\d*)")


def version_key(version: str) -> tuple:
    """Sort key: 0.9.3.1 < 0.9.4-rc1 < 0.9.4-rc3 < 0.9.4.

    Only a pre-release marker ranks below the release. Any other suffix (a
    packager's "-1" or "+dfsg") counts as the release, so it never makes a
    release of the same number look newer than what is installed.
    """
    match = re.match(r"(\d+(?:\.\d+)*)(.*)", version)
    if not match:
        return ((), 0, ())
    numbers = tuple(int(n) for n in match.group(1).split("."))
    pre = _PRE_RELEASE.match(match.group(2).lower())
    if pre:
        return (numbers, 0, (_PRE_RELEASES.index(pre.group(1)), int(pre.group(2) or 0)))
    return (numbers, 1, ())


VERSION_OPTION = "-h"

# cyanrip takes -R for a disc MusicBrainz does not know from master bde6a1e
# (2026-10-10), first in 0.9.4-rc3. Before, -R only picks among the releases
# the disc ID leads to, so for such a disc it ends with "Unable to find
# release info" (checked in the v0.9.2 and v0.9.3.1 sources, which Ubuntu
# 24.04, Debian 13 and openSUSE Tumbleweed package).
RELEASE_FOR_UNKNOWN_DISC = "0.9.4-rc3"


def takes_release_for_unknown_disc(version: str | None) -> bool:
    """Whether this cyanrip can rip a disc MusicBrainz does not know with a
    release given by ID. An unknown version gets the benefit of the doubt."""
    return version is None or version_key(version) >= version_key(RELEASE_FOR_UNKNOWN_DISC)


def installed_version(cyanrip: str) -> Installed | None:
    """Parse 'cyanrip 0.9.4-rc3 (b40c1f0)', the first line of cyanrip -h.

    Not -v: releases up to 0.9.3.1 (Debian's, Ubuntu's, openSUSE's) do not
    know it, while -h starts with that line in every version (checked
    2026-10-27 in containers and in the sources).
    """
    result = proc.capture([cyanrip, VERSION_OPTION])
    if result is None:
        return None
    match = re.search(r"cyanrip\s+(\d[\w.\-]*)(?:\s+\(([0-9a-f]{7,40})\))?", result.stdout + result.stderr)
    if not match:
        return None
    return Installed(match.group(1), match.group(2))


def cache_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "cdport"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "cdport"


def _cache_path() -> Path:
    return cache_dir() / "cyanrip-update.json"


def _read_cache() -> dict[str, Any]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # no cache yet, or a broken one: it is only a cache
    return data if isinstance(data, dict) else {}


def _write_cache(data: dict[str, Any]) -> None:
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        ui.dim(f"Could not write {_cache_path()}: {exc}")


def remote_info(max_age: float = CHECK_INTERVAL_SECONDS) -> Remote:
    """Latest release tag and master commit, asked from GitHub at most once
    per max_age seconds; the answer is cached in ~/.cache/cdport."""
    cache = _read_cache()
    if time.time() - cache.get("checked", 0) < max_age:
        return Remote(cache.get("latest_tag"), cache.get("master"))
    result = proc.capture(["git", "ls-remote", "--refs", REPO_URL,
                           f"refs/heads/{MASTER_BRANCH}", "refs/tags/v*"], timeout=20)
    if result is None:
        raise UpdateCheckError("git is missing or GitHub did not answer")
    if result.returncode != 0:
        raise UpdateCheckError(result.stderr.strip() or f"git ls-remote exited with {result.returncode}")
    tags, master = [], None
    for line in result.stdout.splitlines():
        commit, _, ref = line.partition("\t")
        if ref == f"refs/heads/{MASTER_BRANCH}":
            master = commit
        elif ref.startswith("refs/tags/") and RELEASE_TAG.match(ref[10:]):
            tags.append(ref[10:])
    latest = max(tags, key=lambda t: version_key(t[1:]), default=None)
    cache.update(checked=time.time(), latest_tag=latest, master=master)
    _write_cache(cache)
    return Remote(latest, master)


def is_self_built(cyanrip: str) -> bool:
    """Installed by a source build into ~/.local (by cdport or by hand)."""
    return Path(cyanrip).resolve().parent == (Path.home() / ".local" / "bin").resolve()


def update_offer(channel: str, installed: Installed, remote: Remote, self_built: bool) -> UpdateOffer | None:
    """What to suggest, given what is installed and what the config asks for."""
    latest = remote.latest_tag[1:] if remote.latest_tag else None
    on_master = bool(installed.commit and remote.master and remote.master.startswith(installed.commit))
    if channel == "master":
        if on_master:
            return None
        if latest and version_key(installed.version) <= version_key(latest):
            return UpdateOffer(f"cyanrip {installed.version} is a release; the config asks for "
                               f"{MASTER_BRANCH}.", can_build=True, daily=False)
        if installed.commit and remote.master:
            return UpdateOffer(f"{MASTER_BRANCH} has new commits since your cyanrip build ({installed.commit}).",
                               can_build=self_built, daily=True)
        return None
    if latest and version_key(installed.version) < version_key(latest):
        # v0.9.3.1's meson.build still says 0.9.3, which Debian's and openSUSE's
        # package print (checked 2026-10-27); a package is compared on the
        # first three numbers, so it is not told about the release it is.
        if not self_built and _three_numbers(installed.version) >= _three_numbers(latest):
            return None
        return UpdateOffer(f"cyanrip {latest} is out; installed is {installed.version}.",
                           can_build=self_built, daily=True)
    return None


def _three_numbers(version: str) -> tuple:
    numbers, release, pre = version_key(version)
    return (numbers[:3], release, pre)


def should_show(offer: UpdateOffer) -> bool:
    """News at most once a day; a mismatch with the config every time."""
    if not offer.daily:
        return True
    cache = _read_cache()
    if cache.get("shown") == offer.message and time.time() - cache.get("shown_at", 0) < CHECK_INTERVAL_SECONDS:
        return False
    cache.update(shown=offer.message, shown_at=time.time())
    _write_cache(cache)
    return True
