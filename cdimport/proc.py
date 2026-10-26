"""Running the external tools."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import ui

# How long a tool may take to finish after Ctrl+C before it is terminated.
INTERRUPT_GRACE_SECONDS = 15
# Exit code for a tool that could not be started, as in the shell.
CANNOT_START = 127


def format_command(cmd: list[str]) -> str:
    """Shell-like rendering for display, so the user can rerun it by hand."""
    if sys.platform == "win32":  # pragma: no cover - platform dependent
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


# The tool running right now, if any, so a front end without a terminal can
# send it the Ctrl+C a terminal would (interrupt_tool).
_running: subprocess.Popen | None = None
_running_lock = threading.Lock()


def _track(proc: subprocess.Popen | None) -> None:
    global _running
    with _running_lock:
        _running = proc


def interrupt_tool() -> bool:
    """Send the running tool SIGINT, as Ctrl+C in a terminal does: cyanrip
    then finishes its log and stops. True when there was one."""
    with _running_lock:
        proc = _running
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.send_signal(signal.SIGINT)
    except OSError:
        return False
    return True


def terminal_interrupts_tools() -> bool:
    """Whether a Ctrl+C in cdimport's terminal also reaches the tools.

    They run in cdimport's process group, which gets the terminal's SIGINT
    when it is in the foreground. Sending them another one would be a
    second Ctrl+C, on which cyanrip quits without finishing its log.
    """
    if sys.platform == "win32":  # pragma: no cover - platform dependent
        return True
    try:
        return os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
    except (OSError, ValueError):  # no terminal: the SIGINT came from elsewhere
        return False


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
    _track(proc)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        _let_finish(proc)
        raise
    finally:
        _track(None)


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


# Colours and cursor moves (CSI), and links and titles (OSC, ended by BEL or ST).
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def last_lines(output: bytes, count: int = 3) -> list[str]:
    """The last non-empty lines of terminal output, without colour codes.

    Progress displays redraw one line with \\r, so \\r ends a line too.
    """
    text = ANSI_ESCAPE.sub("", output.decode(errors="replace"))
    lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text)]
    return [line for line in lines if line][-count:]


def _use_pty() -> bool:
    # The page has no terminal, but shows a tool's progress live: it wants
    # the pseudo-terminal too, or cyanrip buffers its output in the pipe.
    return sys.platform != "win32" and ui.wants_pty()


def run_tail(cmd: list[str], cwd: Path | None = None, lines: int = 3,
             keep_bytes: int = 8192) -> tuple[int, list[str]]:
    """Like run(), and also return the last lines the tool printed.

    Those lines usually say why a tool failed, and cdimport repeats them in
    its error message, where they cannot scroll away; the disc preview and
    the offset measurement read their results from them. The tool writes to
    a pseudo-terminal that cdimport copies to the real one: it still sees a
    terminal, so its progress display and output buffering stay as they are.
    Without a terminal (output redirected, or Windows) it writes into a pipe
    instead: the same lines, but the tool may buffer them, so its progress
    shows in bursts. Only the last keep_bytes of output are kept.
    """
    try:
        output = _PtyOutput(cmd, cwd) if _use_pty() else _PipeOutput(cmd, cwd)
    except OSError as exc:
        _cannot_start(cmd, exc)
        return CANNOT_START, [f"cannot start {cmd[0]}: {exc.strerror or exc}"]
    tail = b""
    _track(output.proc)

    def copy() -> None:
        nonlocal tail
        while data := output.read():
            ui.tool_output(data)
            tail = (tail + data)[-keep_bytes:]

    try:
        copy()
        code = output.proc.wait()
    except KeyboardInterrupt:
        # Keep copying while the tool cleans up: a tool whose output is not
        # read blocks once the terminal's or the pipe's buffer is full.
        reader = threading.Thread(target=copy, daemon=True)
        reader.start()
        _let_finish(output.proc)
        reader.join(timeout=1)
        raise
    finally:
        _track(None)
        output.close()
    return code, last_lines(tail, lines)


class _PtyOutput:
    """The tool's output through a pseudo-terminal."""

    def __init__(self, cmd: list[str], cwd: Path | None) -> None:
        import pty

        self.master, slave = pty.openpty()
        _copy_window_size(slave)
        try:
            self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=slave, stderr=slave)
        except OSError:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self._closed = False

    def read(self) -> bytes:
        """The next output, b"" at the end."""
        if self._closed:
            return b""
        try:
            return os.read(self.master, 4096)
        except OSError:  # EIO: the tool closed the terminal
            return b""

    def close(self) -> None:
        self._closed = True
        os.close(self.master)


class _PipeOutput:
    """The tool's output through a pipe, where there is no terminal."""

    def __init__(self, cmd: list[str], cwd: Path | None) -> None:
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert self.proc.stdout is not None
        self._pipe = self.proc.stdout

    def read(self) -> bytes:
        try:
            return self._pipe.read1(4096)
        except (OSError, ValueError):  # closed meanwhile
            return b""

    def close(self) -> None:
        self._pipe.close()


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

    def close(self) -> None:
        """Done checking: close cdimport's end of the error output (the
        program keeps writing to its own)."""
        self.stderr_file.close()


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
