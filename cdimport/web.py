"""The browser page: cdimport's flow, unchanged, with a page as its front end.

The flow in app.py runs in a thread of its own with WebFront as ui's front
end: messages and tool output collect here, and a question waits until the
page answers it. The page asks for what is new a few times a second. Only
this machine can connect, and only with the token from the address
cdimport prints, which the first visit turns into a cookie (as in
lyrembed).
"""

from __future__ import annotations

import codecs
import copy
import hmac
import json
import logging
import re
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import app, cover, proc, tageditor, ui

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",  # cdimport's own icon only
}
_CSP = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'")
# Cookies belong to a host, not a port: two pages on 127.0.0.1 (a second
# cdimport, lyrembed and cdimport handing a tab to each other) would overwrite
# each other's token under one name. The port goes into the name.
COOKIE = "cdimport_token"


def cookie_name(port: int) -> str:
    return f"{COOKIE}_{port}"
# As in lyrembed: the page pings every 15 s, and Chrome runs a hidden tab's
# timers once a minute, so only longer silence means it is gone.
HEARTBEAT_TIMEOUT = 90.0
CLOSE_GRACE = 10.0
MAX_BODY = 1024 * 1024
# Lines of a tool's output the page keeps; cyanrip's preview of a long disc
# fits, its progress lines overwrite each other.
TOOL_LINES = 400
_ANSI = proc.ANSI_ESCAPE
_LINE_BREAK = re.compile(r"\r\n|\n|\r")
# lyrembed prints its page's address like this (lyrembed/web.py, serve).
_HANDOFF = re.compile(r"is running at (http://(?:127\.0\.0\.1|localhost):\d+/\?token=[A-Za-z0-9_-]+)")
# After the other page sends the tab back, cdimport's page needs a moment to
# load: until then its "closing" from leaving does not count.
HANDOFF_GRACE = 30.0


class Cancelled(KeyboardInterrupt):
    """The page quit, or was closed, while the flow waited for an answer:
    the flow stops as it does on Ctrl+C."""


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --- the front end -----------------------------------------------------------
class WebFront(ui.Front):
    """Collects what the flow shows and holds the question it waits on.

    Every change gets a new revision number; the page asks for everything
    newer than the last one it saw. A tool's output is one event whose
    lines grow (and whose last line a progress display redraws).
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._rev = 0
        self.events: list[dict] = []
        self.question: dict | None = None
        self._answers: dict[int, object] = {}
        self._next_question = 0
        self._tool: dict | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        # Pictures the flow handed over with a question, by id: the only ones
        # /api/image serves, so the page cannot make cdimport fetch anything else.
        self._images: dict[str, tuple[str, str]] = {}
        self._image_cache: dict[str, tuple[bytes, str] | Exception] = {}
        self._pending = ""  # output after the last line break: a line still being written
        self._redrawn = ""  # the newest state of a line a progress display redraws with \r
        self.finished: int | None = None
        self._handoff_end: float | None = None
        # Set by stop(): every question from then on, and a pause, ends the
        # flow, so it stops after a tool it let finish instead of asking on.
        self._stopping = False
        self._tags_moved: Callable[[Path], None] | None = None  # the open tag editor's edit_tags(moved=...)

    # recording
    def _bump(self, event: dict) -> None:
        self._rev += 1
        event["rev"] = self._rev
        self._cond.notify_all()

    def message(self, kind: str, text: str = "", **details: object) -> None:
        with self._cond:
            if kind == "gap":
                return
            event = {"id": len(self.events), "kind": kind, "text": text, **details}
            if kind == "tool_start":
                event.update({"lines": [], "running": True, "code": None})
                self._tool = event
                self._pending = self._redrawn = ""
                self._decoder.reset()
            elif kind == "tool_end":
                tool = self._tool
                if tool is not None:
                    last = _ANSI.sub("", self._pending.rstrip("\r") or self._redrawn).rstrip()
                    if last:
                        tool["lines"].append(last)
                    tool.update({"running": False, "code": details.get("code"), "partial": ""})
                    if tool.get("handoff"):
                        self._handoff_end = time.monotonic()
                    self._pending = self._redrawn = ""
                    self._tool = None
                    self._bump(tool)
                return
            self.events.append(event)
            self._bump(event)

    def tool_output(self, data: bytes) -> None:
        with self._cond:
            tool = self._tool
            if tool is None:  # output without a frame: give it one
                self.events.append({"id": len(self.events), "kind": "tool_start", "text": "", "name": "output",
                                    "lines": [], "running": True, "code": None})
                tool = self._tool = self.events[-1]
            text = self._pending + self._decoder.decode(data)
            # A read can end between the \r and \n of a line break (the
            # pseudo-terminal turns every \n into \r\n): a trailing \r waits
            # for the next output, or the line would count as redrawn.
            held = "\r" if text.endswith("\r") else ""
            text = text[:-1] if held else text
            parts = _LINE_BREAK.split(text)
            separators = _LINE_BREAK.findall(text)
            lines = tool["lines"]
            for part, sep in zip(parts, separators):
                if sep == "\r":
                    self._redrawn = part  # redrawn: only its newest state counts
                else:
                    line = _ANSI.sub("", part).rstrip()
                    lines.append(line)
                    self._redrawn = ""
                    found = _HANDOFF.search(line) if tool.get("handoff") and not tool.get("url") else None
                    if found:
                        # The page switches its tab there; the other page leads back.
                        tool["url"] = found.group(1)
                        self.events.append({"id": len(self.events), "kind": "handoff", "text": "",
                                            "name": tool.get("name", ""), "url": found.group(1), "tool": tool["id"]})
                        self._bump(self.events[-1])
            tool["partial"] = _ANSI.sub("", parts[-1] or self._redrawn).rstrip()
            self._pending = parts[-1] + held
            del lines[:-TOOL_LINES]
            self._bump(tool)

    # questions
    def _ask(self, kind: str, prompt: str, **details: object) -> object:
        with self._cond:
            if self._stopping:
                raise Cancelled()
            self._next_question += 1
            qid = self._next_question
            self.question = {"id": qid, "kind": kind, "prompt": prompt, **details}
            self._rev += 1
            self._cond.notify_all()
            while qid not in self._answers:
                self._cond.wait()
            answer = self._answers.pop(qid)
            self.question = None
            self._rev += 1
            self._cond.notify_all()
        if isinstance(answer, Cancelled):
            raise answer
        # What was answered stays in the page's history, as in a terminal.
        self.message("answer", _answer_text(kind, answer, details), prompt=prompt)
        return answer

    def answer(self, qid: int, value: object) -> None:
        """The page's answer to question *qid*, checked against what it asked."""
        with self._cond:
            question = self.question
            if question is None or question["id"] != qid:
                raise ApiError(409, "That question is no longer open.")
            kind = question["kind"]
            if kind == "choose":
                keys = [key for key, _ in question["options"]]
                if value not in keys:
                    raise ApiError(400, f"Choose one of: {', '.join(keys)}")
            elif kind == "confirm":
                if not isinstance(value, bool):
                    raise ApiError(400, "Answer yes or no.")
            elif kind == "tag_editor":
                value = (question["folder"], value if value in ("done", "cover") else "done")
            elif kind in ("ask", "ask_path"):
                if not isinstance(value, str):
                    raise ApiError(400, "Expected text.")
                value = value.strip()
                if kind == "ask" and not value:
                    value = question.get("default", "")
            else:
                value = None
            self._answers[qid] = value
            self._cond.notify_all()

    def stop(self) -> bool:
        """Stop the flow: at the open question or pause at once, else at the
        next one. False when no question was open."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            if self.question is None:
                return False
            self._answers[self.question["id"]] = Cancelled()
            return True

    def pause(self, seconds: float) -> None:
        with self._cond:
            if not self._stopping:
                self._cond.wait_for(lambda: self._stopping, timeout=seconds)
            if self._stopping:
                raise Cancelled()

    def finish(self, code: int) -> None:
        with self._cond:
            self.finished = code
            self._rev += 1
            self._cond.notify_all()

    def ask(self, prompt: str, default: str = "") -> str:
        return self._ask("ask", prompt, default=default)

    def confirm(self, prompt: str, default: bool = True) -> bool:
        return self._ask("confirm", prompt, default=default)

    def choose(self, prompt: str, options: list[tuple[str, str]], default: str | None = None,
               info: dict | None = None) -> str:
        images = {}
        for key, source in ((info or {}).get("images") or {}).items():
            if source:
                images[key] = f"/api/image/{self._register_image(source)}"
        return self._ask("choose", prompt, options=[[k, d] for k, d in options], default=default, images=images)

    def _register_image(self, source: dict) -> str:
        """An id for a picture the flow names: {"url": ...} or {"file": ...}.

        A file is read now, under a new id each time: the cover preview is
        deleted after its question and rewritten under the same name with
        another cover, which an old id would show from the browser's cache.
        A download keeps its id, so going back and forth costs no requests.
        """
        if "file" in source:
            image_id = secrets.token_urlsafe(8)
            try:
                data = Path(source["file"]).read_bytes()
                found = (data, cover.image_type(data)[0])
            except (OSError, cover.CoverError) as exc:
                found = exc
            with self._cond:
                self._images[image_id] = ("file", str(source["file"]))
                self._image_cache[image_id] = found
            return image_id
        value = str(source["url"])
        with self._cond:
            for image_id, known in self._images.items():
                if known == ("url", value):
                    return image_id
            image_id = secrets.token_urlsafe(8)
            self._images[image_id] = ("url", value)
            return image_id

    def image(self, image_id: str) -> tuple[bytes, str]:
        """The picture behind *image_id* and its type: a file as read when its
        question came, a download fetched once."""
        with self._cond:
            source = self._images.get(image_id)
            cached = self._image_cache.get(image_id)
        if source is None:
            raise ApiError(404, "No such picture.")
        if isinstance(cached, Exception):  # the file could not be read
            raise ApiError(502, f"Cannot get the picture: {cached}")
        if cached is not None:
            return cached
        _, value = source
        try:
            data = cover.http_get(value)
            mime, _ = cover.image_type(data)
        except cover.NotFound as exc:
            raise ApiError(404, "There is no such cover.") from exc
        except (cover.CoverError, OSError) as exc:
            raise ApiError(502, f"Cannot get the picture: {exc}") from exc
        with self._cond:
            self._image_cache[image_id] = (data, mime)
        return data, mime

    def ask_path(self, prompt: str, prefill: str = "") -> str:
        return self._ask("ask_path", prompt, prefill=prefill)

    def wait_for_enter(self, prompt: str) -> None:
        self._ask("wait", prompt)

    def supports(self, feature: str) -> bool:
        return feature == "tag_editor"

    def edit_tags(self, folder: Path, moved: Callable[[Path], None] | None = None) -> tuple[Path, str]:
        """The page's tag editor; the question holds the folder, which a save
        that renames it updates, and is answered when the user is done or
        wants to change the cover."""
        self._tags_moved = moved
        try:
            folder_now, then = self._ask("tag_editor", "Edit the tags", folder=str(folder))
        finally:
            self._tags_moved = None
        return Path(folder_now), then

    def tag_folder(self) -> Path:
        """The folder of the open tag-editor question."""
        with self._cond:
            question = self.question
            if question is None or question["kind"] != "tag_editor":
                raise ApiError(409, "The tag editor is not open.")
            return Path(question["folder"])

    def moved_tags(self, folder: Path) -> None:
        """A save renamed the album's folder: the editor and the flow follow."""
        with self._cond:
            if self.question is None or self.question["kind"] != "tag_editor":
                return
            self.question["folder"] = str(folder)
            self._rev += 1
            self._cond.notify_all()
            moved = self._tags_moved
        if moved is not None:
            moved(folder)

    # the page's view
    @property
    def tool_running(self) -> bool:
        return self._tool is not None and self._tool.get("running", False)

    @property
    def away(self) -> bool:
        """The tab is on another program's page, or only just came back."""
        if self._tool is not None and self._tool.get("handoff") and self._tool.get("running"):
            return True
        return self._handoff_end is not None and time.monotonic() - self._handoff_end < HANDOFF_GRACE

    def snapshot(self, since: int) -> dict:
        """What is newer than *since*, copied: the flow thread keeps changing
        a tool's event while the answer is turned into JSON."""
        with self._cond:
            return copy.deepcopy({
                "rev": self._rev,
                "events": [e for e in self.events if e["rev"] > since],
                "question": self.question,
                "toolRunning": self.tool_running,
                "finished": self.finished,
            })

    def wait_for_change(self, since: int, timeout: float) -> None:
        """Return once something newer than *since* exists, or after *timeout*:
        the page's request then answers at once with what is new."""
        with self._cond:
            if self._rev <= since:
                self._cond.wait(timeout)


def _answer_text(kind: str, answer: object, details: dict) -> str:
    if kind == "choose":
        for key, description in details.get("options", []):
            if key == answer:
                return description.split("\n")[0]
        return str(answer)
    if kind == "confirm":
        return "Yes" if answer else "No"
    if kind == "wait":
        return "Done"
    if kind == "tag_editor":
        return "Change the cover" if answer[1] == "cover" else "Tags edited"
    return str(answer) if answer else "(empty)"


# --- the flow in its thread ------------------------------------------------------
class Run:
    """The flow (cli.run_session) in a thread, with the page as front end."""

    def __init__(self, session: app.Session, back: tuple[str, str] | None = None,
                 warnings: list[str] | None = None) -> None:
        self.session = session
        # Said in the terminal before the page existed (the config's); the
        # page repeats them, as nobody may look at that terminal.
        self.warnings = list(warnings or [])
        self.front = WebFront()
        # Another program's page that started cdimport (--return-url): the
        # finished run's button leads back there.
        self.back_url, self.back_name = back if back else ("", "")
        self.thread = threading.Thread(target=self._run, name="cdimport-flow", daemon=True)
        self._started = threading.Lock()
        self._prevented = False

    def start(self) -> None:
        """Start the flow, once. The page does this on its first visit, so
        nothing runs ahead of what it can show (a slow browser, or an address
        opened by hand later)."""
        if not self._started.acquire(blocking=False):
            return
        ui.use(self.front)
        self.thread.start()

    def prevent_start(self) -> bool:
        """Keep the flow from ever starting; False when it already has."""
        if not self._started.acquire(blocking=False):
            return False
        self._prevented = True
        return True

    @property
    def started(self) -> bool:
        return self._started.locked() and not self._prevented

    def _run(self) -> None:
        from .cli import run_session

        code = 1
        for message in self.warnings:
            ui.warn(message)
        try:
            code = run_session(self.session)
        except Exception as exc:  # shown on the page, not lost in the thread
            log.exception("the run failed")
            self.front.message("error", f"cdimport stopped on an error: {exc}")
        finally:
            self.front.finish(code)

    def stop(self, interrupt_tool: bool = True) -> None:
        """End the run: at an open question at once, a running tool first
        gets Ctrl+C (unless it already has: *interrupt_tool* False). The flow
        ends at its next question."""
        if not self.front.stop() and interrupt_tool:
            proc.interrupt_tool()


# --- HTTP --------------------------------------------------------------------
class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, run: Run, token: str, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.run = run
        self.token = token
        self.stop = threading.Event()
        self.last_contact: float | None = None
        self.closing_at: float | None = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def contact(self) -> None:
        self.last_contact = time.monotonic()
        self.closing_at = None

    def page_gone(self) -> bool:
        now = time.monotonic()
        if self.closing_at is not None and now - self.closing_at > CLOSE_GRACE:
            return True
        return self.last_contact is not None and now - self.last_contact > HEARTBEAT_TIMEOUT


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "cdimport"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name
        log.debug("web: " + format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _host_ok(self) -> bool:
        # A page elsewhere could reach 127.0.0.1 through a name it controls
        # (DNS rebinding); the Host header then carries that name.
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _cookie_token(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == cookie_name(self.server.port):
                return value
        return ""

    def _authorized(self) -> bool:
        return hmac.compare_digest(self._cookie_token().encode(), self.server.token.encode())

    def _body(self, json_required: bool) -> dict:
        if self.headers.get("Origin", "") != f"http://{self.headers.get('Host', '')}":
            raise ApiError(403, "Requests must come from cdimport's own page.")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length) if length else b""
        if not json_required:
            return {}
        # A form on another site cannot send this type without asking first.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ApiError(415, "Expected JSON.")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ApiError(400, f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object.")
        return data

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                raise ApiError(400, "Unexpected host.")
            url = urlsplit(self.path)
            if method == "GET" and url.path == "/":
                self._index(url.query)
                return
            if not self._authorized():
                raise ApiError(403, "Open cdimport through the address it printed in the terminal.")
            if method == "GET" and url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
                return
            if not url.path.startswith("/api/"):
                raise ApiError(404, "Not found.")
            if method == "GET" and url.path.startswith("/api/image/"):
                data, mime = self.server.run.front.image(url.path[len("/api/image/"):])
                self._send(200, data, mime)
                return
            body = self._body(json_required=url.path != "/api/closing") if method == "POST" else {}
            self.server.contact()
            result = self._api(method, url.path, parse_qs(url.query), body)
            if result is not None:  # None: _api sent the answer itself (a picture)
                self._send_json(200, result)
            # Only once the answer is out: stopping first could end the
            # program while the answer is still being written.
            if url.path == "/api/closing":
                self.server.closing_at = time.monotonic()
            elif url.path == "/api/close":
                self.wfile.flush()
                self.server.stop.set()
        except (BrokenPipeError, ConnectionResetError):
            return  # the page went away mid-answer (a closed tab, a reload)
        except ApiError as exc:
            if self.path.startswith("/api/"):
                self._send_json(exc.status, {"error": exc.message})
            else:
                self._send(exc.status, exc.message.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as exc:  # a bug must not kill the server thread silently
            log.exception("web request failed: %s %s", method, self.path)
            self._send_json(500, {"error": f"Internal error: {exc}"})

    def _index(self, query: str) -> None:
        given = (parse_qs(query).get("token") or [""])[0]
        if given and hmac.compare_digest(given.encode(), self.server.token.encode()):
            self._send(303, b"", "text/plain", {
                "Location": "/",
                "Set-Cookie": f"{cookie_name(self.server.port)}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
            })
            return
        if not self._authorized():
            raise ApiError(403, "Open cdimport through the address it printed in the terminal.")
        self._static("index.html")

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "Not found.")
        content_type = _CONTENT_TYPES.get(target.suffix)
        if content_type is None:
            raise ApiError(404, "Not found.")
        headers = {"Content-Security-Policy": _CSP} if target.suffix == ".html" else None
        self._send(200, target.read_bytes(), content_type, headers)

    def _api(self, method: str, path: str, query: dict, body: dict):
        run = self.server.run
        front = run.front
        if method == "GET" and path == "/api/state":
            run.start()
            since = (query.get("since") or ["0"])[0]
            since_rev = int(since) if since.isdigit() else 0
            # Long poll: wait briefly for something new, so progress shows
            # without the page asking ten times a second.
            front.wait_for_change(since_rev, timeout=5.0 if since_rev else 0)
            return {**front.snapshot(since_rev), "returnUrl": run.back_url,
                    "returnName": run.back_name or "the previous page"}
        if method == "POST" and path == "/api/answer":
            qid = body.get("id")
            if not isinstance(qid, int):
                raise ApiError(400, "Which question?")
            front.answer(qid, body.get("value"))
            return {"ok": True}
        if method == "GET" and path == "/api/tags":
            return tageditor.read_album(front.tag_folder())
        if method == "GET" and path == "/api/tags/cover":
            try:
                data, mime = tageditor.cover(front.tag_folder(), (query.get("file") or [""])[0])
            except tageditor.TagEditError as exc:
                raise ApiError(404, str(exc)) from exc
            self._send(200, data, mime)
            return None
        if method == "POST" and path == "/api/tags/plan":
            return tageditor.plan(front.tag_folder(), body)
        if method == "POST" and path == "/api/tags/save":
            try:
                result = tageditor.save(front.tag_folder(), body)
            except tageditor.TagEditError as exc:
                raise ApiError(400, str(exc)) from exc
            front.moved_tags(Path(result["folder"]))
            return result
        if method == "POST" and path == "/api/stop-tool":
            return {"stopped": proc.interrupt_tool()}
        if method == "POST" and path == "/api/quit":
            if front.finished is not None:
                return {"ok": True}
            if front.tool_running:
                raise ApiError(409, "A tool is running. Stop it first; cdimport then asks what to do.")
            run.stop()
            return {"ok": True}
        if method == "POST" and path in ("/api/ping", "/api/closing", "/api/close"):
            return {"ok": True}
        raise ApiError(404, "Not found.")


def _say(message: str) -> None:
    """A line for the terminal cdimport was started in, which the page's
    front end does not reach."""
    print(message, flush=True)


# The last line serve prints when the run ends, read by lyrembed's "Import a
# CD" to open the imported album: keep the wording.
FINISHED_LINE = "cdimport finished: {folder}"


def serve(session: app.Session, open_browser: bool = True, port: int = 0,
          back: tuple[str, str] | None = None, warnings: list[str] | None = None) -> int:
    """Run the flow with the page as its front end; returns its exit code."""
    token = secrets.token_urlsafe(24)
    run = Run(session, back, warnings)
    server = _Server(run, token, port)
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    # Where lyrembed's page sends the tab back; the cookie is already set.
    session.page_url = f"http://127.0.0.1:{server.port}/"
    thread = threading.Thread(target=server.serve_forever, name="cdimport-web", daemon=True)
    thread.start()
    _say(f"cdimport is running at {url}")
    opened = open_browser and webbrowser.open(url)
    if back:
        _say(f"When the run is done, its page leads back to {back[1] or 'the previous page'}.")
    else:
        if not opened:
            _say("Open that address in your browser.")
        _say("Close the page when done, or press Ctrl+C here.")
    sys.stdout.flush()
    try:
        while not server.stop.wait(1.0):
            if server.page_gone() and not run.front.away:
                # Closed before its first request started the flow: nothing
                # runs that could finish.
                if run.front.finished is not None or run.prevent_start():
                    break
                if not run.front.tool_running:
                    # Nobody can answer any more; a running rip is left to finish first.
                    run.stop()
    except KeyboardInterrupt:
        if run.started:
            # A running tool already got the terminal's Ctrl+C; the flow
            # ends at its next question once the tool has finished.
            run.stop(interrupt_tool=not proc.terminal_interrupts_tools())
            run.thread.join(timeout=120)
    finally:
        server.shutdown()
        server.server_close()
        ui.use(None)
    if run.started:
        run.thread.join(timeout=120)
    album = session.last_album
    if album is not None and album.folder.is_dir():
        _say(FINISHED_LINE.format(folder=album.folder))
    code = run.front.finished
    return 130 if code is None else code
