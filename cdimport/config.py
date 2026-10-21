"""Configuration: built-in defaults merged with the user's config file."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

__version__ = "0.6.0"

# File names cdimport owns inside the working folder and album folders.
STATE_FILE_NAME = ".cdimport.json"
STAGING_PREFIX = ".cdimport-rip-"

DEFAULT_CONFIG: dict[str, Any] = {
    "work_dir": "",
    "library_dir": "",
    "library_layout": "{albumartist}/{folder}",
    "lyrics_mode": "ask",
    "editor": "eartag",
    "editors": {
        "eartag": {
            "name": "Ear Tag",
            # Ear Tag's sandbox only sees ~/Downloads and ~/Music, so the files
            # are passed through the document portal (--file-forwarding, @@).
            "commands": [
                ["flatpak", "run", "--file-forwarding", "app.drey.EarTag", "@@", "{files}", "@@"],
            ],
        },
        "kid3": {
            "name": "Kid3",
            "commands": [
                ["kid3", "{dir}"],
                ["flatpak", "run", "--file-forwarding", "org.kde.kid3", "@@", "{dir}", "@@"],
            ],
        },
        "picard": {
            "name": "MusicBrainz Picard",
            "commands": [
                ["picard", "{files}"],
                ["flatpak", "run", "--file-forwarding", "org.musicbrainz.Picard", "@@", "{files}", "@@"],
            ],
        },
    },
    "cyanrip": {
        "channel": "release",
        "path": "",
        "device": "",
        "offset": None,
        "mode": "fast",
        "deemphasis": True,
        "replaygain": True,
        "eject": True,
        # cyanrip's default without the " [FLAC]" suffix.
        "dir_scheme": "{album}{if #releasecomment# > #0# (|releasecomment|)}",
        "extra_args": [],
    },
    "lyrembed": {
        "path": "",
        "extra_args": [],
    },
    "cyanrip_source_dir": "",
    "cyanrip_build_dir": "",
    "color": "auto",
}

# What each setting does: the single place for it, written next to each value
# into config.example.json. A dict documents a section key by key; a string
# documents a whole section at once.
SETTING_HELP: dict[str, Any] = {
    "work_dir": (
        "Folder where rips land first and where unfinished albums are looked for. Empty: cdimport "
        "asks on first use and saves the answer here. -w DIR overrides it for one run."
    ),
    "library_dir": (
        "Your music library; at the end cdimport offers to move the album there. Empty: cdimport "
        "asks on first use (suggesting the desktop's music folder) and saves the answer here. "
        "-l DIR overrides it for one run."
    ),
    "library_layout": (
        "Where an album goes inside the library: {albumartist} comes from the tags, {folder} is "
        "the album folder's own name. '/' separates folder levels; the values are made safe as "
        "folder names, so 'AC/DC' cannot add a level."
    ),
    "lyrics_mode": (
        "\"ask\" (each run, automatic preselected), \"auto\" (lyrembed --auto: embed confident "
        "matches without questions), \"interactive\" (choose for each track; lyrembed 2.0 and newer "
        "do that on a page in the browser) or \"skip\"."
    ),
    "editor": "Tag editor to offer by default: a key from \"editors\", or \"skip\".",
    "editors": (
        "Tag editors, by key. \"commands\" lists alternatives; the first whose program (and, "
        "for 'flatpak run', the app) is installed is used. An argument that is exactly "
        "\"{files}\" becomes one argument per track; \"{dir}\" is replaced by the album folder. "
        "Flatpak editors get the files via --file-forwarding and @@ ... @@, because their sandbox "
        "may not see the working folder. Add your own entries here; the built-in ones stay."
    ),
    "cyanrip": {
        "channel": (
            "\"release\" installs the latest release (a distribution package where there is one, "
            "else a source build of the newest vX.Y.Z tag). \"master\" builds the master branch "
            "from source into ~/.local, also on systems with a package; use it when the release does "
            "not build against your FFmpeg. At start cdimport offers to update an outdated cyanrip, "
            "and to switch a release to master."
        ),
        "path": (
            "Path to the cyanrip program. Empty: search PATH, then ~/.local/bin (with channel "
            "master ~/.local/bin first, where that build goes)."
        ),
        "device": "CD drive, e.g. /dev/sr0. Empty: the drive cdimport finds an audio CD in.",
        "offset": (
            "Drive read offset in samples (-s). cyanrip refuses to rip without one, so with null "
            "cdimport asks before the first rip: measure it with cyanrip -f (needs a disc in the "
            "AccurateRip database; saved without asking when at least 4 tracks confirm it), type "
            "it in, or use 0 for one rip."
        ),
        "mode": (
            "\"fast\" rips without paranoia (-P none), checks every track against AccurateRip "
            "and rips the tracks that do not match again with -P max; discs not in the "
            "AccurateRip database are ripped securely. \"secure\" always uses cyanrip's paranoia "
            "(-P max): about twice as slow, every block read in overlapping, verified pieces. "
            "--rip-mode overrides it for one run, p in the disc menu for one disc."
        ),
        "deemphasis": (
            "true (cyanrip's default) undoes preemphasis on discs flagged with it, since players "
            "ignore the flag. false passes -W: bit-exact, but such discs then sound too bright."
        ),
        "replaygain": "Write ReplayGain tags (tags only, the audio is unchanged). false passes -K.",
        "eject": "Eject the disc after a successful rip (-Q).",
        "dir_scheme": (
            "Album folder naming scheme (-D), relative to the working folder; see cyanrip's README. "
            "Empty: cyanrip's own default, which adds \" [FLAC]\"."
        ),
        "extra_args": "More cyanrip options, one list element per argument, e.g. [\"-H\"].",
    },
    "lyrembed": {
        "path": "Path to the lyrembed program. Empty: search PATH, then ~/.local/bin.",
        "extra_args": "More lyrembed options, e.g. [\"--color\", \"never\"].",
    },
    "cyanrip_source_dir": (
        "Source checkout of a cyanrip built by cdimport (Fedora). Set by the installer; "
        "prefilled when rebuilding."
    ),
    "cyanrip_build_dir": "Meson build folder of that source build. Set by the installer.",
    "color": "\"auto\", \"always\" or \"never\". auto honours NO_COLOR and non-terminal output.",
}

EXAMPLE_ABOUT = [
    "cdimport does NOT read this file. It lists every setting with its built-in default.",
    "To change settings, create ~/.config/cdimport/config.json (or pass --config FILE)",
    "with only the keys you want to change; everything else keeps its default. Example:",
    "{ \"library_dir\": \"~/Music\", \"cyanrip\": { \"offset\": 6 } }",
    "Keys starting with an underscore, like this one, are comments and ignored.",
]

# Settings whose default is None but which accept a value of this type.
_NULLABLE: dict[tuple[str, ...], type] = {("cyanrip", "offset"): int}
# bool is a subclass of int in Python, but "offset": true is no offset.
_NOT_NULLABLE_TYPES = (bool,)

LYRICS_MODES = ("ask", "auto", "interactive", "skip")
RIP_MODES = ("fast", "secure")


class ConfigError(Exception):
    """A config file exists but cannot be used."""


def example_config() -> dict[str, Any]:
    """The content of config.example.json: the defaults, each value preceded by
    an '_<key>' explanation, which load_config ignores, so a copy still works."""
    example: dict[str, Any] = {"_about": EXAMPLE_ABOUT}
    for key, default in DEFAULT_CONFIG.items():
        help_text = SETTING_HELP[key]
        if isinstance(help_text, dict):
            section: dict[str, Any] = {}
            for sub_key, value in default.items():
                section[f"_{sub_key}"] = help_text[sub_key]
                section[sub_key] = copy.deepcopy(value)
            example[key] = section
        else:
            example[f"_{key}"] = help_text
            example[key] = copy.deepcopy(default)
    return example


def write_example(path: Path) -> None:
    """Regenerate the example after changing a default or its help text:

        python -c 'from pathlib import Path; from cdimport import config;
                   config.write_example(Path("config.example.json"))'

    test_example_config_is_current fails until that is done.
    """
    path.write_text(_dump(example_config()) + "\n", encoding="utf-8")


def _dump(value: Any, level: int = 0) -> str:
    """JSON with short lists of plain values on one line, so each editor
    command reads as one command instead of one argument per line."""
    pad, inner = "  " * level, "  " * (level + 1)
    if isinstance(value, dict) and value:
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {_dump(v, level + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, list) and value:
        one_line = json.dumps(value, ensure_ascii=False)
        if all(not isinstance(v, (dict, list)) for v in value) and len(one_line) <= 100:
            return one_line
        return "[\n" + ",\n".join(inner + _dump(v, level + 1) for v in value) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)


def config_dir() -> Path:
    """Per-user config folder: XDG on Linux, %APPDATA% on Windows."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "cdimport"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "cdimport"


def default_config_path() -> Path:
    return config_dir() / "config.json"


def runtime_dir() -> Path:
    """cdimport's per-user folder for short-lived files (drive locks, the
    cover preview): shared by every cdimport of that user whatever its
    working folder (-w), private to the user, and emptied at logout."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime) / "cdimport"
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"cdimport-{uid}"


def make_runtime_dir() -> Path:
    path = runtime_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _merge(base: dict[str, Any], override: dict[str, Any], path: tuple[str, ...],
           problems: list[str]) -> None:
    """Merge override into base in place, checking each value's type.

    Type checks happen here because a wrong type (a string where a list
    belongs) would otherwise surface as a confusing crash deep inside a step.
    """
    for key, value in override.items():
        if str(key).startswith("_"):
            continue  # comment
        where = ".".join(path + (key,))
        # "editors" is open-ended: users add their own entries.
        if key not in base and path != ("editors",):
            problems.append(f"unknown setting '{where}'")
            continue
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge(current, value, path + (key,), problems)
            continue
        if current is None:
            expected = _NULLABLE.get(path + (key,))
            if expected and value is not None and (not isinstance(value, expected)
                                                   or isinstance(value, _NOT_NULLABLE_TYPES)):
                problems.append(f"'{where}' must be {expected.__name__} or null")
                continue
        elif type(value) is not type(current) and not (path == ("editors",) and isinstance(value, dict)):
            problems.append(f"'{where}' must be {type(current).__name__}, not {type(value).__name__}")
            continue
        base[key] = copy.deepcopy(value)


def validate(cfg: dict[str, Any]) -> list[str]:
    """Check values whose type is right but whose content is not."""
    return [message for _, message in _value_problems(cfg)]


def _value_problems(cfg: dict[str, Any]) -> list[tuple[tuple[str, ...], str]]:
    """validate()'s problems, each with the setting it is about."""
    problems: list[tuple[tuple[str, ...], str]] = []
    if cfg["cyanrip"]["channel"] not in ("release", "master"):
        problems.append((("cyanrip", "channel"), "'cyanrip.channel' must be \"release\" or \"master\""))
    if cfg["cyanrip"]["mode"] not in RIP_MODES:
        problems.append((("cyanrip", "mode"), "'cyanrip.mode' must be \"fast\" or \"secure\""))
    if cfg["lyrics_mode"] not in LYRICS_MODES:
        problems.append((("lyrics_mode",), f"'lyrics_mode' must be one of {', '.join(LYRICS_MODES)}"))
    for key, entry in cfg["editors"].items():
        commands = entry.get("commands") if isinstance(entry, dict) else None
        if not (isinstance(commands, list) and commands
                and all(isinstance(c, list) and c and all(isinstance(a, str) for a in c) for c in commands)):
            problems.append((("editors", key),
                             f"'editors.{key}.commands' must be a list of commands, each a list of strings"))
        elif not isinstance(entry.get("name", key), str):
            problems.append((("editors", key), f"'editors.{key}.name' must be a string"))
    if cfg["editor"] != "skip" and cfg["editor"] not in cfg["editors"]:
        problems.append((("editor",), f"'editor' is '{cfg['editor']}', which is not in 'editors' (or use \"skip\")"))
    try:
        cfg["library_layout"].format(albumartist="a", folder="f")
    except (KeyError, IndexError, ValueError) as exc:
        problems.append((("library_layout",),
                         f"'library_layout' may only use {{albumartist}} and {{folder}} ({exc})"))
    scheme = cfg["cyanrip"]["dir_scheme"]
    # cdimport finds the rip inside its own staging folder; an absolute
    # scheme would put it somewhere else.
    if scheme and (os.path.isabs(scheme) or scheme.startswith("~")):
        problems.append((("cyanrip", "dir_scheme"),
                         "'cyanrip.dir_scheme' must be relative (it is placed in the working folder)"))
    for tool in ("cyanrip", "lyrembed"):
        if not all(isinstance(a, str) for a in cfg[tool]["extra_args"]):
            problems.append(((tool, "extra_args"), f"'{tool}.extra_args' must contain strings only"))
    return problems


def _restore_default(cfg: dict[str, Any], where: tuple[str, ...]) -> None:
    """Undo one setting: back to its default, or gone if it has none (a user's editor)."""
    default: Any = DEFAULT_CONFIG
    for key in where:
        default = default.get(key, _MISSING) if isinstance(default, dict) else _MISSING
    parent = cfg
    for key in where[:-1]:
        parent = parent[key]
    if default is _MISSING:
        parent.pop(where[-1], None)
    else:
        parent[where[-1]] = copy.deepcopy(default)


_MISSING = object()


def load_config(path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Return the merged config and warnings.

    An explicit path that cannot be used is fatal (the user asked for that
    file). At the default location a typo never blocks ripping a CD: a file
    that is not JSON only warns and falls back to the defaults, and a
    setting with a wrong value is reported and replaced by its default,
    while the others (folders, drive offset) still apply.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    explicit = path is not None
    path = path or default_config_path()
    if not path.exists():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        return cfg, []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("the top level must be an object")
    except (OSError, ValueError) as exc:
        message = f"cannot read config file {path}: {exc}"
        if explicit:
            raise ConfigError(message) from exc
        return copy.deepcopy(DEFAULT_CONFIG), [message + " - using the defaults"]

    problems: list[str] = []
    _merge(cfg, loaded, (), problems)  # leaves a value of the wrong type at its default
    found = _value_problems(cfg)
    if explicit and (problems or found):
        raise ConfigError(f"problems in {path}:\n  " + "\n  ".join(problems + [m for _, m in found]))
    # Restoring one setting can uncover another ("editor" naming a removed
    # editor), so check again until nothing is left.
    while found:
        problems += [message for _, message in found]
        for where, _ in found:
            _restore_default(cfg, where)
        found = _value_problems(cfg)
    if problems:
        return cfg, [f"problems in {path}:\n  " + "\n  ".join(problems)
                     + "\n  These settings use their defaults; the others apply."]
    return cfg, []


def remember(path: Path, values: dict[str, Any]) -> None:
    """Save settings in the user's config file, keeping everything else.

    Nested dicts are merged, so {"cyanrip": {"offset": 6}} keeps the user's
    other cyanrip settings. Raises ValueError for a file that is not a JSON
    object: rewriting it would destroy whatever the user meant to write there.
    """
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("the top level is not an object")
    _update_nested(data, values)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _update_nested(target: dict[str, Any], values: dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _update_nested(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def music_dir_suggestion() -> str:
    """The desktop's music folder (XDG_MUSIC_DIR, e.g. ~/Musik on a German
    system), else ~/Music. Only a suggestion for the first-run question."""
    base = os.environ.get("XDG_CONFIG_HOME")
    dirs_file = (Path(base) if base else Path.home() / ".config") / "user-dirs.dirs"
    try:
        lines = dirs_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []  # not a freedesktop system, or no such file
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key == "XDG_MUSIC_DIR":
            value = value.strip().strip('"')
            if value.startswith("$HOME"):
                return "~" + value[len("$HOME"):]
            return value
    return "~/Music"


def expand(path: str | Path) -> Path:
    """Expand ~ and environment variables; config paths are written by hand."""
    return Path(os.path.expandvars(os.path.expanduser(str(path))))
