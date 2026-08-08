"""The optional HTTP shell.

Off unless you call :meth:`Tmuxd.serve_http`. Most callers are in the same
process as the library and should just call it; going through HTTP to reach
your own process is tax paid for nothing (works/03-http.md §1).

Eight endpoints, each one method on the library. Nothing here is invented:
the error bodies are :mod:`tmuxd.errors` serialised, and the session bodies
are ``Session.to_dict``.

Written on the standard library on purpose. With no streaming and no
websockets there is nothing a framework would do for us, and ``import
tmuxd`` should never drag a web stack in behind it.
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from .errors import TmuxdError, Unauthorized

_SESSION_RE = re.compile(r"^/api/sessions/([^/]+)$")
_ACTION_RE = re.compile(r"^/api/sessions/([^/]+)/(keys|rename)$")
IDEMPOTENCY_WINDOW = 600.0


class HttpShell:
    def __init__(self, tmuxd, port, bind="127.0.0.1", token=None):
        self.tmuxd = tmuxd
        self.port = port
        self.bind = bind
        self.token = token
        self._server = None
        self._thread = None
        self._replays = {}
        self._lock = threading.Lock()

    # -- server lifecycle ------------------------------------------------

    def start(self):
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.bind, self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
            name="tmuxd-http", daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve_forever(self):
        try:
            while self._thread and self._thread.is_alive():
                self._thread.join(0.5)
        except KeyboardInterrupt:
            pass

    # -- idempotency ------------------------------------------------------

    def replay(self, key):
        if not key:
            return None
        with self._lock:
            self._sweep()
            hit = self._replays.get(key)
        return None if hit is None else hit[1]

    def remember(self, key, result):
        if not key:
            return
        with self._lock:
            self._sweep()
            self._replays[key] = (time.time(), result)

    def _sweep(self):
        cutoff = time.time() - IDEMPOTENCY_WINDOW
        for key in [k for k, (at, _) in self._replays.items() if at < cutoff]:
            del self._replays[key]

    # -- routing ----------------------------------------------------------

    def dispatch(self, method, path, body, headers):
        if path == "/api/health":
            return 200, {"ok": True}

        self._authorize(headers)

        if path == "/api/info" and method == "GET":
            return 200, self.tmuxd.info()

        if path == "/api/sessions":
            if method == "GET":
                return 200, {"sessions": [s.to_dict() for s in self.tmuxd.sessions()]}
            if method == "POST":
                session = self.tmuxd.session(
                    id=body.get("id"),
                    cwd=body.get("cwd"),
                    cmd=body.get("cmd"),
                    env=body.get("env"),
                )
                return 201, session.to_dict()

        match = _SESSION_RE.match(path)
        if match:
            sid = unquote(match.group(1))
            if method == "GET":
                return 200, self.tmuxd.get(sid).to_dict()
            if method == "DELETE":
                clients = self.tmuxd.get(sid).kill()
                return 200, {"id": sid, "killed": True, "clients": clients}

        match = _ACTION_RE.match(path)
        if match and method == "POST":
            sid, action = unquote(match.group(1)), match.group(2)
            session = self.tmuxd.get(sid)
            if action == "keys":
                if "text" in body and body["text"] is not None:
                    session.send(body["text"], enter=bool(body.get("enter")))
                elif body.get("enter"):
                    session.send_key("Enter")
                if body.get("keys"):
                    session.send_key(*body["keys"])
                return 200, {"ok": True}
            new_id = body.get("id") or body.get("new_id")
            session.rename(new_id)
            return 200, session.to_dict()

        raise _NotFound("no route for %s %s" % (method, path))

    def _authorize(self, headers):
        if not self.token:
            return
        header = headers.get("Authorization", "")
        if header != "Bearer %s" % self.token:
            raise Unauthorized("bad or missing bearer token")


class _NotFound(TmuxdError):
    code = "not_found"


def _make_handler(shell):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tmuxd"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        # -- verbs -------------------------------------------------------

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_DELETE(self):
            self._handle("DELETE")

        # -- plumbing ----------------------------------------------------

        def _handle(self, method):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            key = self.headers.get("Idempotency-Key")

            if method == "POST":
                cached = shell.replay(key)
                if cached is not None:
                    return self._respond(*cached)

            try:
                body = self._read_body()
            except ValueError as exc:
                return self._respond(400, {"error": "bad_request", "message": str(exc)})

            try:
                status, payload = shell.dispatch(method, path, body, self.headers)
            except Unauthorized as exc:
                return self._respond(401, exc.to_dict())
            except _NotFound as exc:
                return self._respond(404, exc.to_dict())
            except TmuxdError as exc:
                return self._respond(_status_for(exc), exc.to_dict())
            except ValueError as exc:
                return self._respond(400, {"error": "bad_request", "message": str(exc)})

            if method == "POST":
                shell.remember(key, (status, payload))
            self._respond(status, payload)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("body is not valid JSON: %s" % exc)
            if not isinstance(parsed, dict):
                raise ValueError("body must be a JSON object")
            return parsed

        def _respond(self, status, payload):
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    return Handler


_STATUS = {
    "no_such_session": 404,
    "session_exists": 409,
    "bad_id": 400,
    "port_in_use": 409,
    "unauthorized": 401,
    "tmux_gone": 503,
    "tmux_missing": 503,
    "ttyd_missing": 503,
    "ttyd_failed": 503,
    "not_found": 404,
}


def _status_for(exc):
    return _STATUS.get(exc.code, 500)
