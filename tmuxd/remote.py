"""Talking to a tmuxd on another machine.

Method names match :class:`~tmuxd.core.Tmuxd` so moving code from local to
remote changes one line -- the constructor. It is deliberately **not**
pretending to be the same object, though: process lifetime is the other
machine's business, so there is no ``close()`` that stops anything and no
``serve_http`` (works/03-http.md §8).
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import Unreachable, from_code


class RemoteSession:
    __slots__ = ("id", "cwd", "cmd", "created_at", "last_attached", "external", "_r", "_snapshot")

    def __init__(self, remote, payload):
        self._r = remote
        self._snapshot = payload
        self.id = payload["id"]
        self.cwd = payload.get("cwd")
        self.cmd = payload.get("cmd")
        self.created_at = payload.get("created_at")
        self.last_attached = payload.get("last_attached")
        self.external = bool(payload.get("external"))

    def _refresh(self):
        self._snapshot = self._r._call("GET", "/api/sessions/" + quote(self.id, safe=""))
        return self._snapshot

    @property
    def status(self):
        return self._refresh().get("status")

    @property
    def alive(self):
        return self.status == "alive"

    @property
    def clients(self):
        return self._refresh().get("clients", 0)

    @property
    def current_command(self):
        return self._refresh().get("current_command")

    @property
    def url(self):
        return self._snapshot.get("url")

    def send(self, text, enter=False):
        self._r._call(
            "POST",
            "/api/sessions/%s/keys" % quote(self.id, safe=""),
            {"text": text, "enter": bool(enter)},
        )
        return self

    def send_key(self, *keys):
        if keys:
            self._r._call(
                "POST",
                "/api/sessions/%s/keys" % quote(self.id, safe=""),
                {"keys": [str(k) for k in keys]},
            )
        return self

    def rename(self, new_id):
        self._snapshot = self._r._call(
            "POST", "/api/sessions/%s/rename" % quote(self.id, safe=""), {"id": new_id}
        )
        self.id = new_id
        return self

    def kill(self):
        out = self._r._call("DELETE", "/api/sessions/" + quote(self.id, safe=""))
        return out.get("clients", 0)

    def to_dict(self):
        return dict(self._snapshot)

    def __repr__(self):
        return "<RemoteSession %s>" % self.id


class RemoteTmuxd:
    def __init__(self, base_url, token=None, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def session(self, id=None, cwd=None, cmd=None, env=None):
        payload = self._call(
            "POST", "/api/sessions", {"id": id, "cwd": cwd, "cmd": cmd, "env": env}
        )
        return RemoteSession(self, payload)

    def create(self, id=None, cwd=None, cmd=None, env=None):
        return self.session(id=id, cwd=cwd, cmd=cmd, env=env)

    def get(self, id):
        return RemoteSession(self, self._call("GET", "/api/sessions/" + quote(id, safe="")))

    def has(self, id):
        from .errors import NoSuchSession

        try:
            self.get(id)
        except NoSuchSession:
            return False
        return True

    def sessions(self):
        payload = self._call("GET", "/api/sessions")
        return [RemoteSession(self, item) for item in payload.get("sessions", [])]

    def info(self):
        return self._call("GET", "/api/info")

    def url_for(self, sid):
        return self.get(sid).url

    # -- transport --------------------------------------------------------

    def _call(self, method, path, body=None):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token

        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise Unreachable("HTTP %d from %s" % (exc.code, self.base_url))
            raise from_code(
                payload.get("error", "error"),
                payload.get("message", "request failed"),
                payload.get("details"),
            )
        except URLError as exc:
            raise Unreachable("cannot reach %s: %s" % (self.base_url, exc.reason))

        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def __repr__(self):
        return "<RemoteTmuxd %s>" % self.base_url
