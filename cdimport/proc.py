"""Running the external tools."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# How long a tool may take to finish after Ctrl+C before it is terminated.
INTERRUPT_GRACE_SECONDS = 15
# Exit code for a tool that could not be started, as in the shell.
CANNOT_START = 127


def format_command(cmd: list[str]) -> str:
    """Shell-like rendering for display, so the user can rerun it by hand."""
    if sys.platform == "win32":  # pragma: no cover - platform dependent
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def run(cmd: list[str], cwd: Path | None = None) -> int:
    """Run a tool attached to the terminal and return its exit code.

    The tool gets the terminal's Ctrl+C itself (same process group). When it
    arrives, wait for the tool to clean up (cyanrip finishes its log, lyrembed
    its current file) and then re-raise, so the step stops cleanly.
    """
    try:
        proc = subprocess.Popen(cmd, cwd=cwd)
    except OSError as exc:
        _cannot_start(cmd, exc)
        return CANNOT_START
    try:
        return proc.wait()
    except KeyboardInterrupt:
        _let_finish(proc)
        raise


def _cannot_start(cmd: list[str], exc: OSError) -> None:
    """Report a tool that does not start (missing, or a launcher whose
    interpreter is gone) the way a shell would, so callers handle it like
    any other failed run instead of crashing."""
    print(f"cdimport: cannot start {cmd[0]}: {exc.strerror or exc}", file=sys.stderr)


def _let_finish(proc: subprocess.Popen) -> None:
    """After Ctrl+C: give the tool time to clean up, then terminate it."""
    try:
        proc.wait(timeout=INTERRUPT_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def last_lines(output: bytes, count: int = 3) -> list[str]:
    """The last non-empty lines of terminal output, without colour codes.

    Progress displays redraw one line with \\r, so \\r ends a line too.
    """
    text = _ANSI_ESCAPE.sub("", output.decode(errors="replace"))
    lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text)]
    return [line for line in lines if line][-count:]


def _use_pty() -> bool:
    return sys.platform != "win32" and sys.stdout.isatty()


def run_tail(cmd: list[str], cwd: Path | None = None, lines: int = 3,
             keep_bytes: int = 8192) -> tuple[int, list[str]]:
    """Like run(), and also return the last lines the tool printed.

    Those lines usually say why a tool failed, and cdimport repeats them in
    its error message, where they cannot scroll away. The tool writes to a
    pseudo-terminal that cdimport copies to the real one: it still sees a
    terminal, so its progress display and output buffering stay as they are.
    Without a terminal (or on Windows) this is plain run() with no lines.
    Only the last keep_bytes of output are searched for those lines.
    """
    if not _use_pty():
        return run(cmd, cwd), []
    import pty

    master, slave = pty.openpty()
    _copy_window_size(slave)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=slave, stderr=slave)
    except OSError as exc:
        os.close(master)
        _cannot_start(cmd, exc)
        return CANNOT_START, [f"cannot start {cmd[0]}: {exc.strerror or exc}"]
    finally:
        os.close(slave)
    tail = b""
    try:
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: the tool closed the terminal
                break
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            tail = (tail + data)[-keep_bytes:]
        code = proc.wait()
    except KeyboardInterrupt:
        _let_finish(proc)
        raise
    finally:
        os.close(master)
    return code, last_lines(tail, lines)


def _copy_window_size(fd: int) -> None:
    """Give the pseudo-terminal the real one's size, so progress lines fit.

    Purely cosmetic: when it fails, the tool assumes 80 columns.
    """
    import fcntl
    import termios

    try:
        size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    except OSError:
        return


def capture(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str] | None:
    """Run a quick query; None when the program is missing or hangs."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


@dataclass
class Launched:
    """A GUI program started in the background."""
    process: subprocess.Popen[bytes]
    stderr_file: tempfile._TemporaryFileWrapper[bytes]  # type: ignore[name-defined]

    def failure(self) -> str | None:
        """The error output if the program has already exited with an error.

        Single-instance apps (Ear Tag) exit 0 right away when a window is
        already open, so an early exit alone is not a failure.
        """
        code = self.process.poll()
        if code is None or code == 0:
            return None
        self.stderr_file.seek(0)
        text = self.stderr_file.read().decode(errors="replace").strip()
        return text or f"exited with code {code}"


def launch(cmd: list[str], settle_seconds: float = 2.0) -> Launched:
    """Start a GUI program detached from the terminal.

    Do not wait for it: a single-instance app returns at once. Its stderr goes
    to a temporary file so a failed start can still be reported. Raises
    OSError when the program cannot be started at all.
    """
    stderr_file = tempfile.TemporaryFile()
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": stderr_file}
    if sys.platform == "win32":  # pragma: no cover - platform dependent
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Keeps a Ctrl+C in the terminal from closing the editor window.
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    try:
        process.wait(timeout=settle_seconds)
    except subprocess.TimeoutExpired:
        pass
    return Launched(process, stderr_file)
