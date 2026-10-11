"""Terminal output and prompts."""

from __future__ import annotations

import glob
import os
import sys

# The stdlib module is POSIX-only; on Windows pyreadline3 may stand in, else
# prompts work without prefill and completion.
try:  # pragma: no cover - platform dependent
    import readline as _readline
except ImportError:  # pragma: no cover
    try:
        import pyreadline3 as _readline  # type: ignore[no-redef]
    except ImportError:
        _readline = None

_ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "red": "\033[31m",
    "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m",
}
_color = False


def setup_color(mode: str) -> None:
    """Decide once whether to colour output: "auto", "always" or "never"."""
    global _color
    mode = mode.lower()
    if mode == "always":
        _color = True
    elif mode == "never":
        _color = False
    else:
        # no-color.org: only a non-empty value counts.
        _color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if _color and sys.platform == "win32":  # pragma: no cover - platform dependent
        os.system("")  # enables ANSI sequences in the Windows console


def style(text: str, *names: str) -> str:
    if not _color:
        return text
    return "".join(_ANSI[n] for n in names) + text + _ANSI["reset"]


def step(number: int, title: str) -> None:
    print()
    print(style(f"── {number}. {title} ", "bold", "cyan") + style("─" * max(0, 50 - len(title)), "dim"))


def info(msg: str) -> None:
    print(msg)


def success(msg: str) -> None:
    print(style(msg, "green"))


def warn(msg: str) -> None:
    print(style(msg, "yellow"))


def error(msg: str) -> None:
    print(style(msg, "red"), file=sys.stderr)


def dim(msg: str) -> None:
    print(style(msg, "dim"))


def ask(prompt: str, default: str = "") -> str:
    """Read one line; EOF (Ctrl+D) counts as Ctrl+C, so a closed stdin
    cannot loop forever on a prompt."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        print()
        raise KeyboardInterrupt from None
    return answer or default


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            answer = input(f"{prompt} [{hint}] ").strip().lower()
        except EOFError:
            print()
            raise KeyboardInterrupt from None
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        warn("Please answer y or n.")


def choose(prompt: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    """Show lettered options and return the chosen key.

    Options are (key, description). Enter picks the default when there is one.
    """
    for key, description in options:
        marker = style("›", "green") if key == default else " "
        print(f" {marker} {style(key, 'bold')}  {description}")
    keys = {k for k, _ in options}
    while True:
        answer = ask(prompt, default or "").lower()
        if answer in keys:
            return answer
        warn(f"Please type one of: {', '.join(k for k, _ in options)}")


def wait_for_enter(prompt: str) -> None:
    ask(prompt)


def _complete_path(text: str, state: int) -> str | None:
    # glob.escape: folder names like "Album [FLAC]" are not character classes.
    matches = sorted(glob.glob(glob.escape(os.path.expanduser(text)) + "*"))
    results = [m + os.sep if os.path.isdir(m) else m for m in matches]
    return results[state] if state < len(results) else None


def ask_path(prompt: str, prefill: str = "") -> str:
    """Ask for a path with the answer prefilled and editable, and Tab completion.

    Without readline the prefill is shown in brackets and an empty answer
    accepts it. With readline an empty answer means the user cleared the line.
    """
    hook = getattr(_readline, "set_startup_hook", None)
    insert = getattr(_readline, "insert_text", None)
    if not (hook and insert):
        return ask(prompt, prefill)
    old_completer = _readline.get_completer()
    old_delims = _readline.get_completer_delims()
    try:
        hook(lambda: insert(prefill))
        _readline.set_completer(_complete_path)
        _readline.set_completer_delims("\t\n")  # spaces belong to the path
        _readline.parse_and_bind("tab: complete")
        try:
            return input(f"{prompt}: ").strip()
        except EOFError:
            print()
            raise KeyboardInterrupt from None
    finally:
        hook(None)
        _readline.set_completer(old_completer)
        _readline.set_completer_delims(old_delims)


def tilde(path: str | os.PathLike[str]) -> str:
    """Show a path under the home folder as ~/..., shorter to read and edit."""
    text = str(path)
    home = os.path.expanduser("~")
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text
