"""cdport on the D-Bus session bus, for Gatefold and other programs.

Why: a sandboxed program (Gatefold as a Flatpak) can neither start host
programs nor read cdport's config or working folder, but it may talk to a
bus name it is allowed (``--talk-name=io.github.sobakah.cdport``). The bus
starts this service when it is called (a service file in
``~/.local/share/dbus-1/services``, written by ``--install-dbus-service``),
so cdport can stay a pipx program on the host.

The service starts each import as a page run of its own (``cdport --web
--no-browser``, a child process) and hands back the page's address, which
the caller opens in the browser. It offers fixed operations only: no
command and no arbitrary folder to work on (an album is continued only when
it is one of UnfinishedAlbums), so it is no way out of a sandbox.

The bus name is the app ID cdport would have as a Flatpak, whose own
service file would then take over.

Interface ``io.github.sobakah.cdport1`` on ``/io/github/sobakah/cdport``:

- ``GetInfo() -> a{sv}``: Version, InterfaceVersion, LibraryDir, WorkDir,
  Editor (the config's; "" when unset).
- ``UnfinishedAlbums() -> as``: album folders in the working folder with
  steps still open.
- ``Import(a{sv} options) -> (s run, s url)``: start a page run; options
  ``Library`` (s, as -l) and ``Folder`` (s, one of UnfinishedAlbums). The
  answer comes once the page is up.
- ``Runs() -> a(ss)``: the runs still going, with their page addresses.
- ``Stop(s run)``: as Ctrl+C: a running tool finishes first (cyanrip its log).
- Signal ``Finished(s run, i exit_code, s album)``: a run ended; album is the
  folder it finished ("" when none), the same as the "finished" line.
"""

from __future__ import annotations

import itertools
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, state

BUS_NAME = "io.github.sobakah.cdport"
OBJECT_PATH = "/io/github/sobakah/cdport"
INTERFACE = "io.github.sobakah.cdport1"
INTERFACE_VERSION = 1
ERROR = INTERFACE + ".Error"
# Without runs or calls for this long, the service ends; the bus starts it
# again at the next call. Not staying around also means not running an old
# version after pipx updated cdport.
IDLE_EXIT = 60.0
# How long Import waits for the page's address before giving up on the run.
URL_TIMEOUT = 60.0

INTROSPECTION = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTERFACE}">
    <method name="GetInfo"><arg name="info" type="a{{sv}}" direction="out"/></method>
    <method name="UnfinishedAlbums"><arg name="folders" type="as" direction="out"/></method>
    <method name="Import">
      <arg name="options" type="a{{sv}}" direction="in"/>
      <arg name="run" type="s" direction="out"/>
      <arg name="url" type="s" direction="out"/>
    </method>
    <method name="Runs"><arg name="runs" type="a(ss)" direction="out"/></method>
    <method name="Stop"><arg name="run" type="s" direction="in"/></method>
    <signal name="Finished">
      <arg name="run" type="s"/><arg name="exit_code" type="i"/><arg name="album" type="s"/>
    </signal>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg name="xml" type="s" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Peer">
    <method name="Ping"/>
  </interface>
</node>
"""

# The lines a page run prints (web.serve), under either name.
_PAGE = re.compile(r"is running at (http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)")
_FINISHED = re.compile(r"^(?:cdport|cdimport) finished: (.+)$")

NEEDS_JEEPNEY = ("The D-Bus service needs the Python package jeepney, which pipx installs with cdport "
                 "(in a checkout: pip install jeepney).")


class ServiceError(Exception):
    """A call that cannot be answered: becomes a D-Bus error reply."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


@dataclass
class PageRun:
    id: str
    process: Any                  # subprocess.Popen, or a test's stand-in
    folder: str = ""              # the album it continues, if any
    url: str = ""
    album: str = ""               # from the "finished" line
    waiting: Any = None           # the Import call still waiting for url
    started: float = field(default_factory=time.monotonic)


def page_command(folder: str = "", library: str = "") -> list[str]:
    """A page run of the cdport this service runs from (pipx launcher or
    checkout), like Gatefold's `cdimport --web --no-browser [-l LIB]`."""
    from .desktop import launch_command

    return [*launch_command(), *([folder] if folder else []), "--web", "--no-browser",
            *(["-l", library] if library else [])]


def spawn_page_run(command: list[str]) -> subprocess.Popen:
    # Its own session: the service's end (idle, or the bus going away) must
    # not take a running rip with it. stderr goes where the service's goes
    # (the journal, when the bus started it).
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            start_new_session=True)


class Service:
    """The calls and the runs, without the bus: call() answers a method
    call, outgoing() turns what the runs reported into replies and signals."""

    def __init__(self, spawn: Callable[[list[str]], Any] = spawn_page_run,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.runs: dict[str, PageRun] = {}
        self.events: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._spawn = spawn
        self._clock = clock
        self._ids = itertools.count(1)

    # --- method calls -----------------------------------------------------
    def call(self, member: str, signature: str, args: tuple, caller: Any) -> tuple[str, tuple] | None:
        """(signature, body) of the reply, or None when it comes later
        (Import, once the page is up). Raises ServiceError."""
        expected = {"GetInfo": "", "UnfinishedAlbums": "", "Import": "a{sv}", "Runs": "", "Stop": "s"}
        if member not in expected:
            raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod", f"No method {member} in {INTERFACE}.")
        if signature != expected[member]:
            raise ServiceError("org.freedesktop.DBus.Error.InvalidArgs",
                               f"{member} takes ({expected[member]}), not ({signature}).")
        if member == "GetInfo":
            return "a{sv}", (self.info(),)
        if member == "UnfinishedAlbums":
            return "as", ([str(a.folder) for a in self.unfinished()],)
        if member == "Import":
            self.start(args[0], caller)
            return None
        if member == "Runs":
            return "a(ss)", ([(r.id, r.url) for r in self.runs.values() if r.url],)
        self.stop(args[0])
        return "", ()

    def _config(self) -> dict[str, Any]:
        try:
            return config.load_config()[0]   # read at each call: it may have changed
        except config.ConfigError as exc:   # pragma: no cover - only for an explicit path
            raise ServiceError(ERROR + ".Config", str(exc)) from exc

    def info(self) -> dict[str, tuple[str, Any]]:
        cfg = self._config()
        folder = lambda key: str(config.expand(cfg[key])) if cfg[key] else ""  # noqa: E731
        return {"Version": ("s", config.__version__),
                "InterfaceVersion": ("u", INTERFACE_VERSION),
                "LibraryDir": ("s", folder("library_dir")),
                "WorkDir": ("s", folder("work_dir")),
                "Editor": ("s", cfg["editor"] or "")}

    def unfinished(self) -> list[state.AlbumState]:
        cfg = self._config()
        return state.unfinished_albums(config.expand(cfg["work_dir"])) if cfg["work_dir"] else []

    def start(self, options: dict[str, tuple[str, Any]], caller: Any) -> PageRun:
        known = {"Library", "Folder"}
        for key, (sig, _) in options.items():
            if key not in known:
                raise ServiceError("org.freedesktop.DBus.Error.InvalidArgs",
                                   f"Unknown option {key} (known: {', '.join(sorted(known))}).")
            if sig != "s":
                raise ServiceError("org.freedesktop.DBus.Error.InvalidArgs", f"{key} is a string (s), not {sig}.")
        library = options.get("Library", ("s", ""))[1]
        folder = options.get("Folder", ("s", ""))[1]
        if library and not (Path(library).is_absolute() and Path(library).is_dir()):
            raise ServiceError("org.freedesktop.DBus.Error.InvalidArgs", f"Library is not a folder: {library}")
        if folder:
            # Only what cdport itself lists: the interface must not let a
            # caller point cdport at any folder it likes.
            found = {str(a.folder.resolve()): str(a.folder) for a in self.unfinished()}
            folder = found.get(str(Path(folder).resolve()), "")
            if not folder:
                raise ServiceError(ERROR + ".NotUnfinished",
                                   f"{options['Folder'][1]} is not an unfinished album in cdport's working folder.")
            if any(r.folder == folder for r in self.runs.values()):
                raise ServiceError(ERROR + ".Busy", f"A run is already working on {folder}.")
        try:
            process = self._spawn(page_command(folder, library))
        except OSError as exc:
            raise ServiceError(ERROR + ".Failed", f"Could not start cdport: {exc}") from exc
        run = PageRun(str(next(self._ids)), process, folder=folder, waiting=caller, started=self._clock())
        self.runs[run.id] = run
        threading.Thread(target=self._read, args=(run,), name=f"cdport-dbus-run-{run.id}", daemon=True).start()
        return run

    def stop(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError(ERROR + ".UnknownRun", f"No run {run_id!r} (it may have ended).")
        self._interrupt(run)

    @staticmethod
    def _interrupt(run: PageRun) -> None:
        try:
            run.process.send_signal(signal.SIGINT)   # as Ctrl+C: web.serve stops the flow
        except (ProcessLookupError, OSError):
            pass   # ended meanwhile; its exit is reported as usual

    # --- what the runs report -----------------------------------------------
    def _read(self, run: PageRun) -> None:
        """In a thread per run: its output, until it ends. Reads to the end
        so a run never blocks on a full pipe."""
        for line in run.process.stdout:
            line = line.rstrip("\n")
            if page := _PAGE.search(line):
                self.events.put(("url", run.id, page.group(1)))   # again after an update on the page
            elif done := _FINISHED.match(line):
                self.events.put(("album", run.id, done.group(1)))
        self.events.put(("exit", run.id, run.process.wait()))

    def outgoing(self) -> list[tuple]:
        """Replies and signals to send now: ("return", caller, signature,
        body), ("error", caller, name, message) or ("signal", member,
        signature, body)."""
        out: list[tuple] = []
        while True:
            try:
                kind, run_id, value = self.events.get_nowait()
            except queue.Empty:
                break
            run = self.runs.get(run_id)
            if run is None:
                continue
            if kind == "url":
                run.url = value
                if run.waiting is not None:
                    out.append(("return", run.waiting, "ss", (run.id, value)))
                    run.waiting = None
            elif kind == "album":
                run.album = value
            else:
                del self.runs[run_id]
                if run.waiting is not None:
                    out.append(("error", run.waiting, ERROR + ".Failed",
                                f"cdport ended (exit code {value}) before its page was up."))
                else:
                    out.append(("signal", "Finished", "sis", (run.id, int(value), run.album)))
        for run in self.runs.values():
            if run.waiting is not None and self._clock() - run.started > URL_TIMEOUT:
                out.append(("error", run.waiting, ERROR + ".Failed",
                            f"cdport's page was not up after {URL_TIMEOUT:.0f} s; the run is stopped."))
                run.waiting = None
                self._interrupt(run)
        return out


# --- on the bus --------------------------------------------------------------
def serve(service: Service | None = None, idle_exit: float = IDLE_EXIT) -> int:
    """Own the bus name and answer calls until idle; the exit code."""
    try:
        from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_return, new_signal
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        print(NEEDS_JEEPNEY, file=sys.stderr)
        return 2
    service = service or Service()
    # Started by the bus: answer on the bus that started us.
    bus = os.environ.get("DBUS_STARTER_ADDRESS") or "SESSION"
    try:
        conn = open_dbus_connection(bus=bus)
    except (OSError, KeyError, ValueError) as exc:
        print(f"cdport: cannot connect to the D-Bus session bus: {exc}", file=sys.stderr)
        return 1
    with conn:
        try:
            owner, inbox = _request_name(conn, message_bus.RequestName(BUS_NAME, 4))  # 4: don't queue
        except (OSError, EOFError, ValueError, TimeoutError) as exc:
            print(f"cdport: could not take the name {BUS_NAME}: {exc}", file=sys.stderr)
            return 1
        if owner not in (1, 4):   # 1: now the owner, 4: already was
            print(f"cdport: {BUS_NAME} is already taken by another cdport.", file=sys.stderr)
            return 0
        emitter = DBusAddress(OBJECT_PATH, interface=INTERFACE)
        last = time.monotonic()
        while True:
            for item in service.outgoing():
                if item[0] == "return":
                    conn.send(new_method_return(item[1], item[2], item[3]))
                elif item[0] == "error":
                    conn.send(new_error(item[1], item[2], "s", (item[3],)))
                else:
                    conn.send(new_signal(emitter, item[1], item[2], item[3]))
            if service.runs:
                last = time.monotonic()
            elif time.monotonic() - last > idle_exit:
                return 0
            try:
                msg = inbox.pop(0) if inbox else conn.receive(timeout=0.25)
            except TimeoutError:
                continue
            except (OSError, EOFError, ValueError):   # the bus went away (logout)
                return 0
            if msg.header.message_type != MessageType.method_call:
                continue   # signals such as NameAcquired
            last = time.monotonic()
            fields = msg.header.fields
            interface = fields.get(HeaderFields.interface)
            member = fields.get(HeaderFields.member)
            signature = fields.get(HeaderFields.signature, "")
            try:
                if interface == "org.freedesktop.DBus.Introspectable" and member == "Introspect":
                    answer: tuple[str, tuple] | None = ("s", (INTROSPECTION,))
                elif interface == "org.freedesktop.DBus.Peer" and member == "Ping":
                    answer = ("", ())
                elif fields.get(HeaderFields.path) != OBJECT_PATH or interface not in (INTERFACE, None):
                    raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod",
                                       f"No {interface}.{member} on {fields.get(HeaderFields.path)}.")
                else:
                    answer = service.call(member, signature, msg.body, msg)
            except ServiceError as exc:
                conn.send(new_error(msg, exc.name, "s", (str(exc),)))
                continue
            if answer is not None:
                conn.send(new_method_return(msg, answer[0] or None, answer[1]))


def _request_name(conn: Any, request: Any, timeout: float = 10) -> tuple[int, list]:
    """RequestName's answer, and what came before it. Started by the bus,
    the call that started us arrives as soon as the name is ours, which can
    be before the reply: jeepney's send_and_get_reply would drop it."""
    from jeepney import HeaderFields

    serial = next(conn.outgoing_serial)
    conn.send(request, serial=serial)
    early, deadline = [], time.monotonic() + timeout
    while True:
        msg = conn.receive(timeout=max(0.0, deadline - time.monotonic()))
        if msg.header.fields.get(HeaderFields.reply_serial) == serial:
            if msg.header.message_type.name == "error":
                raise ValueError(f"the bus refused: {msg.body}")
            return msg.body[0], early
        early.append(msg)


# --- the service file ---------------------------------------------------------
def service_file_path() -> Path:
    from .desktop import _data_home

    return _data_home() / "dbus-1" / "services" / f"{BUS_NAME}.service"


def service_file_text(command: list[str]) -> str:
    # The bus splits Exec like a shell (quotes and backslashes).
    return "\n".join(["[D-BUS Service]", f"Name={BUS_NAME}",
                      "Exec=" + " ".join(shlex.quote(a) for a in [*command, "--dbus"]), ""])


def install_service_file() -> tuple[Path, list[str], str | None]:
    """Write the service file for the cdport that runs this; returns its
    path, the command, and why the bus could not be told (None when it was)."""
    from .desktop import launch_command

    command = launch_command()
    path = service_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(service_file_text(command), encoding="utf-8")
    return path, command, _reload_bus()


def remove_service_file() -> Path | None:
    path = service_file_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    _reload_bus()
    return path


def _reload_bus() -> str | None:
    """dbus-daemon notices a new service file by itself; dbus-broker reads
    them when told to reload."""
    try:
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return NEEDS_JEEPNEY
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            reply = conn.send_and_get_reply(message_bus.ReloadConfig(), timeout=5)
    except (OSError, KeyError, ValueError, TimeoutError) as exc:
        return str(exc)
    return None if reply.header.message_type.name == "method_return" else str(reply.body)
