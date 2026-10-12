"""Terminal output and prompts."""

from __future__ import annotations

import glob
import os
import re
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
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "underline": "\033[4m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "blue": "\033[34m",
    "cyan": "\033[36m",
}
# Marks at the start of a message, so its kind shows without colour too.
OK, WARN, FAIL, SKIP = "✓", "!", "✗", "–"
_URL = re.compile(r"(?:https?|file)://[^\s)\]]+")
# Width of the label column in field() and check().
LABEL_WIDTH = 10
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


# Whether the last line written was empty, so gap() never doubles a blank line.
_blank = True


def _print(text: str = "", *, err: bool = False) -> None:
    global _blank
    print(text, file=sys.stderr if err else sys.stdout)
    _blank = text == ""


def gap() -> None:
    """One blank line between blocks (step, menu, tool output), but never two."""
    if not _blank:
        _print()


def links(text: str, *outer: str) -> str:
    """Underline web and file links and make them clickable (OSC 8), in
    terminals that support it; others show the plain underlined link.

    outer is the style of the surrounding text, restored after each link.
    """
    if not _color:
        return text
    restore = "".join(_ANSI[n] for n in outer)

    def link(match: re.Match[str]) -> str:
        url = match.group(0)
        return (f"\033]8;;{url}\033\\" + _ANSI["blue"] + _ANSI["underline"] + url
                + _ANSI["reset"] + restore + "\033]8;;\033\\")
    return _URL.sub(link, text)


def _styled(text: str, *names: str) -> str:
    return style(links(text, *names), *names) if names else links(text)


def step(number: int | None, title: str) -> None:
    """A section heading: the numbered steps, and "Summary" (no number)."""
    label = f"{number}. {title}" if number is not None else title
    gap()
    _print(style(f"── {label} ", "bold", "cyan") + style("─" * max(0, 53 - len(label)), "dim"))


def _marked(mark: str, msg: str, *names: str) -> str:
    """mark before the first line; later lines indented to the text."""
    first, *rest = msg.split("\n")
    lines = [style(mark, "bold", *names) + " " + _styled(first, *names)]
    lines += ["  " + _styled(line, *names) for line in rest]
    return "\n".join(lines)


def info(msg: str) -> None:
    _print(links(msg))


def success(msg: str) -> None:
    _print(_marked(OK, msg, "green"))


def warn(msg: str) -> None:
    _print(_marked(WARN, msg, "yellow"))


def error(msg: str) -> None:
    _print(_marked(FAIL, msg, "red"), err=True)


def dim(msg: str) -> None:
    _print(_styled(msg, "dim"))


def field(label: str, value: object) -> None:
    """One "label  value" line of an aligned list (summary, folders)."""
    # Indented like check()'s labels, so both kinds of line form one list.
    _print("    " + style(f"{label:<{LABEL_WIDTH}}", "dim") + links(str(value)))


def check(label: str, value: str, ok: bool | None, note: str = "") -> None:
    """One line of a checklist: ✓ found, ! missing or broken, – skipped.

    note goes on its own dimmed line below, aligned with the value.
    """
    mark, colour = (OK, "green") if ok else (SKIP, "dim") if ok is None else (WARN, "yellow")
    _print(f"  {style(mark, 'bold', colour)} {style(f'{label:<{LABEL_WIDTH}}', 'bold')}{links(value)}")
    for line in note.split("\n") if note else []:
        _print(" " * (4 + LABEL_WIDTH) + _styled(line, "dim"))


def tool_start(name: str, command: str = "") -> None:
    """A frame line above an external tool's output, which otherwise runs
    straight into cdimport's own text."""
    gap()
    _print(style("╭─ ", "dim") + style(name, "bold") + (style(f"  {command}", "dim") if command else ""))


def tool_end(name: str, code: int | None) -> None:
    """The closing frame line, with how the tool ended (None: failed, the
    message follows)."""
    if code == 0:
        status = style("done", "green")
    else:
        status = style("failed" if code is None else f"exit code {code}", "yellow")
    _print(style("╰─ ", "dim") + style(name, "dim") + " " + status)
    gap()


def _prompt(text: str, default: str = "") -> str:
    """The input prompt: question bold, default dimmed.

    readline counts every byte of the prompt as screen width unless escape
    codes are wrapped in \\001...\\002; without that, editing a long answer
    garbles the line.
    """
    if not _color:
        return f"{text} [{default}]: " if default else f"{text}: "
    def wrap(codes: str) -> str:
        return f"\001{codes}\002" if _readline is not None else codes
    reset = wrap(_ANSI["reset"])
    out = wrap(_ANSI["bold"]) + text + reset
    if default:
        out += " " + wrap(_ANSI["dim"]) + f"[{default}]" + reset
    return out + ": "


def _answered() -> None:
    global _blank
    _blank = False


def ask(prompt: str, default: str = "") -> str:
    """Read one line; EOF (Ctrl+D) counts as Ctrl+C, so a closed stdin
    cannot loop forever on a prompt."""
    try:
        answer = input(_prompt(prompt, default)).strip()
        _answered()
    except EOFError:
        print()
        raise KeyboardInterrupt from None
    return answer or default


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            answer = input(_prompt(prompt, hint)[:-2] + " ").strip().lower()
            _answered()
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
    gap()
    width = max(len(k) for k, _ in options)
    for key, description in options:
        marker = style("›", "bold", "green") if key == default else " "
        first, *rest = description.split("\n")
        _print(f" {marker} {style(f'{key:<{width}}', 'bold', 'cyan')}  {links(first)}")
        for line in rest:  # details such as a link, aligned with the text
            _print(" " * (width + 5) + _styled(line.strip(), "dim"))
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
            answer = input(_prompt(prompt)).strip()
            _answered()
            return answer
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
