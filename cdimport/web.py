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
from urllib.parse import parse_qs, urlsplit

from . import app, proc, ui

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
COOKIE = "cdimport_token"
# As in lyrembed: the page pings every 15 s, and Chrome runs a hidden tab's
# timers once a minute, so only longer silence means it is gone.
HEARTBEAT_TIMEOUT = 90.0
CLOSE_GRACE = 10.0
MAX_BODY = 1024 * 1024
# Lines of a tool's output the page keeps; cyanrip's preview of a long disc
# fits, its progress lines overwrite each other.
TOOL_LINES = 400
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_LINE_BREAK = re.compile(r"\r\n|\n|\r")


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
        self._pending = ""  # output after the last line break: a line still being written
        self._redrawn = ""  # the newest state of a line a progress display redraws with \r
        self.finished: int | None = None

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
                    last = _ANSI.sub("", self._pending or self._redrawn).rstrip()
                    if last:
                        tool["lines"].append(last)
                    tool.update({"running": False, "code": details.get("code"), "partial": ""})
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
            parts = _LINE_BREAK.split(text)
            separators = _LINE_BREAK.findall(text)
            lines = tool["lines"]
            for part, sep in zip(parts, separators):
                if sep == "\r":
                    self._redrawn = part  # redrawn: only its newest state counts
                else:
                    lines.append(_ANSI.sub("", part).rstrip())
                    self._redrawn = ""
            self._pending = parts[-1]
            tool["partial"] = _ANSI.sub("", self._pending or self._redrawn).rstrip()
            del lines[:-TOOL_LINES]
            self._bump(tool)

    # questions
    def _ask(self, kind: str, prompt: str, **details: object) -> object:
        with self._cond:
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

    def cancel(self) -> bool:
        """Stop the flow at the open question; False when none is open."""
        with self._cond:
            if self.question is None:
                return False
            self._answers[self.question["id"]] = Cancelled()
            self._cond.notify_all()
            return True

    def finish(self, code: int) -> None:
        with self._cond:
            self.finished = code
            self._rev += 1
            self._cond.notify_all()

    def ask(self, prompt: str, default: str = "") -> str:
        return self._ask("ask", prompt, default=default)

    def confirm(self, prompt: str, default: bool = True) -> bool:
        return self._ask("confirm", prompt, default=default)

    def choose(self, prompt: str, options: list[tuple[str, str]], default: str | None = None) -> str:
        return self._ask("choose", prompt, options=[[k, d] for k, d in options], default=default)

    def ask_path(self, prompt: str, prefill: str = "") -> str:
        return self._ask("ask_path", prompt, prefill=prefill)

    def wait_for_enter(self, prompt: str) -> None:
        self._ask("wait", prompt)

    # the page's view
    @property
    def tool_running(self) -> bool:
        return self._tool is not None and self._tool.get("running", False)

    def snapshot(self, since: int) -> dict:
        with self._cond:
            return {
                "rev": self._rev,
                "events": [e for e in self.events if e["rev"] > since],
                "question": self.question,
                "toolRunning": self.tool_running,
                "finished": self.finished,
            }

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
    return str(answer) if answer else "(empty)"


# --- the flow in its thread ------------------------------------------------------
class Run:
    """The flow (cli.run_session) in a thread, with the page as front end."""

    def __init__(self, session: app.Session) -> None:
        self.session = session
        self.front = WebFront()
        self.thread = threading.Thread(target=self._run, name="cdimport-flow", daemon=True)
        self._started = threading.Lock()

    def start(self) -> None:
        """Start the flow, once. The page does this on its first visit, so
        nothing runs ahead of what it can show (a slow browser, or an address
        opened by hand later)."""
        if not self._started.acquire(blocking=False):
            return
        ui.use(self.front)
        self.thread.start()

    @property
    def started(self) -> bool:
        return self._started.locked()

    def _run(self) -> None:
        from .cli import run_session

        code = 1
        try:
            code = run_session(self.session)
        except Exception as exc:  # shown on the page, not lost in the thread
            log.exception("the run failed")
            self.front.message("error", f"cdimport stopped on an error: {exc}")
        finally:
            self.front.finish(code)

    def stop(self) -> None:
        """End the run: at an open question at once, a running tool first
        gets Ctrl+C."""
        if not self.front.cancel():
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
            if name == COOKIE:
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
            body = self._body(json_required=url.path != "/api/closing") if method == "POST" else {}
            self.server.contact()
            self._send_json(200, self._api(method, url.path, parse_qs(url.query), body))
            # Only once the answer is out: stopping first could end the
            # program while the answer is still being written.
            if url.path == "/api/closing":
                self.server.closing_at = time.monotonic()
            elif url.path == "/api/close":
                self.wfile.flush()
                self.server.stop.set()
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
                "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
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
            return front.snapshot(since_rev)
        if method == "POST" and path == "/api/answer":
            qid = body.get("id")
            if not isinstance(qid, int):
                raise ApiError(400, "Which question?")
            front.answer(qid, body.get("value"))
            return {"ok": True}
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


def serve(session: app.Session, open_browser: bool = True, port: int = 0) -> int:
    """Run the flow with the page as its front end; returns its exit code."""
    token = secrets.token_urlsafe(24)
    run = Run(session)
    server = _Server(run, token, port)
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    thread = threading.Thread(target=server.serve_forever, name="cdimport-web", daemon=True)
    thread.start()
    _say(f"cdimport is running at {url}")
    if not (open_browser and webbrowser.open(url)):
        _say("Open that address in your browser.")
    _say("Close the page when done, or press Ctrl+C here.")
    sys.stdout.flush()
    try:
        while not server.stop.wait(1.0):
            if server.page_gone():
                if run.front.finished is not None:
                    break
                if not run.front.tool_running:
                    # Nobody can answer any more; a running rip is left to finish first.
                    run.stop()
    except KeyboardInterrupt:
        if run.started:
            run.stop()
            run.thread.join(timeout=120)
    finally:
        server.shutdown()
        server.server_close()
        ui.use(None)
    if run.started:
        run.thread.join(timeout=120)
    code = run.front.finished
    return 130 if code is None else code
