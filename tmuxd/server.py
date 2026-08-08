"""The control API -- what the CLI talks to.

Two paths use tmuxd, and they need opposite things (works/03-server.md §1).
Embedding the library needs no server at all: your process is already alive,
it holds the instance, and if you want to expose any of it you already have a
web app -- so this module gives you a :func:`router` to mount there.

The CLI needs a server, and that is not optional: ``tmuxd ls`` is a process
that lives for tens of milliseconds and can hold neither ttyd nor session
state, so it has to ask something that can. :func:`serve` is that something.

Two ports, two audiences: ttyd's is the only address a *person* ever sees
(``s.url`` goes straight to a colleague), and this one is JSON in, JSON out.

Imported lazily and only ever from ``tmuxd[server]`` -- ``import tmuxd``
never pulls FastAPI in behind it.
"""

import time

try:
    from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse
except ModuleNotFoundError as exc:  # pragma: no cover - depends on the extra
    raise ModuleNotFoundError(
        'tmuxd serve needs the server extra: pip install "tmuxd[server]"\n'
        "(the Python SDK does not need it -- see docs/v1/sdk)"
    ) from exc

from . import __version__
from .errors import PlatformError, SessionError, TmuxdError, Unauthorized

IDEMPOTENCY_WINDOW = 600.0

STATUS = {
    "no_such_session": 404,
    "session_exists": 409,
    "bad_id": 400,
    "port_in_use": 409,
    "unauthorized": 401,
    "tmux_gone": 503,
    "tmux_missing": 503,
    "ttyd_missing": 503,
    "ttyd_failed": 503,
}


def status_for(exc):
    return STATUS.get(exc.code, 500 if isinstance(exc, PlatformError) else 400)


class _Replays:
    """Idempotency-Key memory.

    A retried POST must not type a command twice -- the accident is real, and
    worse when the command was `terraform apply`.
    """

    def __init__(self, window=IDEMPOTENCY_WINDOW):
        self.window = window
        self._seen = {}

    def get(self, key):
        if not key:
            return None
        self._sweep()
        hit = self._seen.get(key)
        return None if hit is None else hit[1]

    def put(self, key, value):
        if key:
            self._sweep()
            self._seen[key] = (time.monotonic(), value)

    def _sweep(self):
        cutoff = time.monotonic() - self.window
        for key in [k for k, (at, _) in self._seen.items() if at < cutoff]:
            del self._seen[key]


def router(tmuxd, *, token=None, control_port=None):
    """An ``APIRouter`` over one :class:`~tmuxd.core.Tmuxd`.

    Mount it in the app you already run::

        app.include_router(router(t, token="..."), prefix="/tmuxd")

    Auth, logging, CORS and rate limiting then stay yours -- a library has no
    business starting a second HTTP server inside your process.
    """
    api = APIRouter()
    replays = _Replays()

    def authorize(authorization=Header(default=None)):
        if token and authorization != "Bearer %s" % token:
            raise Unauthorized("bad or missing bearer token")

    guard = [Depends(authorize)]

    # -- health is the only unauthenticated route ------------------------

    @api.get("/api/health")
    def health():
        return {"ok": True}

    @api.get("/api/info", dependencies=guard)
    def info():
        out = tmuxd.info()
        out["control"] = {"port": control_port}
        return out

    # -- sessions --------------------------------------------------------

    @api.get("/api/sessions", dependencies=guard)
    def list_sessions():
        return {"sessions": [s.to_dict() for s in tmuxd.sessions()]}

    @api.post("/api/sessions", status_code=201, dependencies=guard)
    def create_session(body: dict = Body(default={}),
                       idempotency_key=Header(default=None, alias="Idempotency-Key")):
        cached = replays.get(idempotency_key)
        if cached is not None:
            return cached
        session = tmuxd.session(
            id=body.get("id"), cwd=body.get("cwd"),
            cmd=body.get("cmd"), env=body.get("env"),
        )
        out = session.to_dict()
        replays.put(idempotency_key, out)
        return out

    # Plain path params, not {sid:path}: the greedy form swallows slashes, so
    # /api/sessions/a/rename would resolve to a session called "a/rename"
    # instead of 404-ing. Ids that do contain a slash arrive percent-encoded
    # and are decoded after matching, which is what we want.
    @api.get("/api/sessions/{sid}", dependencies=guard)
    def get_session(sid: str):
        return tmuxd.get(sid).to_dict()

    @api.delete("/api/sessions/{sid}", dependencies=guard)
    def kill_session(sid: str):
        clients = tmuxd.get(sid).kill()
        return {"id": sid, "killed": True, "clients": clients}

    # -- the only write action -------------------------------------------

    @api.post("/api/sessions/{sid}/keys", dependencies=guard)
    def send_keys(sid: str, body: dict = Body(default={}),
                  idempotency_key=Header(default=None, alias="Idempotency-Key")):
        cached = replays.get(idempotency_key)
        if cached is not None:
            return cached
        session = tmuxd.get(sid)
        text = body.get("text")
        if text is not None:
            session.send(text, enter=bool(body.get("enter")))
        elif body.get("enter"):
            session.send_key("Enter")
        if body.get("keys"):
            session.send_key(*body["keys"])
        out = {"ok": True}
        replays.put(idempotency_key, out)
        return out

    return api


def create_app(tmuxd, *, token=None, control_port=None):
    """A standalone app wrapping :func:`router`, used by :func:`serve`."""
    app = FastAPI(
        title="tmuxd",
        version=__version__,
        summary="control API -- ttyd is on the other port, and that one is for people",
    )
    app.include_router(router(tmuxd, token=token, control_port=control_port))

    @app.exception_handler(TmuxdError)
    def _tmuxd_error(request: Request, exc: TmuxdError):
        # The wire codes are errors.py serialised, not a second vocabulary.
        return JSONResponse(status_code=status_for(exc), content=exc.to_dict())

    @app.exception_handler(ValueError)
    def _value_error(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400, content={"error": "bad_request", "message": str(exc)}
        )

    return app


def serve(tmuxd, *, control_port, bind="127.0.0.1", token=None, log_level="warning"):
    """Run the control API in the foreground. Blocks until interrupted."""
    import uvicorn

    app = create_app(tmuxd, token=token, control_port=control_port)
    config = uvicorn.Config(app, host=bind, port=control_port, log_level=log_level)
    uvicorn.Server(config).run()


__all__ = ["router", "create_app", "serve", "status_for"]
