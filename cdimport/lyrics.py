"""Step 4: embed lyrics with lyrembed, run as a separate program."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import proc

# lyrembed's exit codes.
EXIT_OK = 0
EXIT_BATCH_FAILURES = 1
EXIT_UNUSABLE_INPUT = 2
EXIT_INTERRUPTED = 130


def lyrembed_command(lyrembed: str, cfg: dict[str, Any], folder: Path, mode: str,
                     romanize: bool | None = None) -> list[str]:
    """mode is "auto" (batch, never prompts) or "interactive".

    romanize applies to "auto" only; None leaves it to lyrembed's own
    settings.auto_romanize.
    """
    cmd = [lyrembed, str(folder)]
    if mode == "auto":
        cmd.append("--auto")
        if romanize is not None:
            cmd.append("--romanize" if romanize else "--no-romanize")
    return cmd + cfg["lyrembed"]["extra_args"]


def run_lyrembed(lyrembed: str, cfg: dict[str, Any], folder: Path, mode: str,
                 romanize: bool | None = None) -> int:
    return proc.run(lyrembed_command(lyrembed, cfg, folder, mode, romanize))


def can_romanize(lyrembed: str) -> bool:
    """Whether this lyrembed has --romanize for batch mode (added after 1.0.0).

    Older versions reject the flag, so it is only offered when --help lists it.
    """
    result = proc.capture([lyrembed, "--help"])
    return result is not None and "--romanize" in result.stdout
