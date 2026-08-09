"""``Tmuxd`` -- the object everything else is a shell around.

    t = Tmuxd(port=12345, token="changeme")   # ttyd is up; tmux is not yet
    s = t.session(id="id5", cwd="~/proj", cmd="claude")
    s.send("run the tests", enter=True)
    print(s.url)

The CLI and the HTTP endpoint call exactly this. Nothing lives above it.
"""

import os
import shutil
import socket as _socket
import time
import warnings
from urllib.parse import quote

from . import state as _state
from . import tmux as _tmux
from . import toolchain as _toolchain
from . import ttyd as _ttyd
from .errors import BadId, NoSuchSession, SessionExists
from .session import Session

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_SOCKET = "tmuxd"
DEFAULT_HISTORY = 10000
DEFAULT_GC_TTL = 7 * 24 * 3600

# Characters tmux cannot carry in a session name, plus a few we refuse on our
# own account: a leading dash would be read as an option, control characters
# make a mess of every listing that prints them, and a slash cannot survive a
# URL path segment -- ASGI servers decode %2F back to "/" before routing, so
# an id containing one would be unaddressable over the control API. Ids are
# keys the caller computes; a slash buys nothing and costs correctness in the
# path, in ?arg= and in file names.
_FORBIDDEN = set("./:\n\r\t\0")


def free_port(bind="127.0.0.1"):
    """A port nobody is on, right now.

    Racy in principle -- someone could take it between here and ttyd binding.
    That race is loud (ttyd fails to start and says so) and vanishingly rare,
    while the collision a fixed default guarantees is neither.
    """
    host = "127.0.0.1" if bind in ("0.0.0.0", "::", "") else bind
    with _socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def _env(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, "") else value


class Tmuxd:
    def __init__(
        self,
        port=None,
        *,
        bind=None,
        token=None,
        socket=None,
        workspace=None,
        shell=None,
        history_limit=None,
        tmux_bin=None,
        ttyd_bin=None,
        state_dir=None,
        gc_ttl=None,
        url_host=None,
    ):
        self.socket_name = socket or _env("TMUXD_SOCKET", DEFAULT_SOCKET)
        if self.socket_name == "default":
            raise ValueError(
                "socket='default' would put tmuxd's sessions in the tmux server you "
                "use yourself. It always runs its own pool -- pick another name."
            )
        self.tmux_socket = self.socket_name if self.socket_name == DEFAULT_SOCKET \
            else "%s-%s" % (DEFAULT_SOCKET, self.socket_name)

        # tmuxd = tmux + ttyd. There is no "just the multiplexer" mode: a shell
        # that outlives its connection is tmux's half, and a person getting in
        # from a browser is ttyd's, and without the second one this is a tmux
        # wrapper rather than tmuxd (works/01-library.md §2).
        self.bind = bind or _env("TMUXD_BIND", "127.0.0.1")
        # No fixed default port. 7681 is *ttyd's* default, so it is exactly the
        # port a user's own ttyd is most likely already sitting on -- picking it
        # is picking a fight. Asked for a port, we use it; not asked, we take a
        # free one and write it down (works/01 §5).
        self.port = int(port if port is not None
                        else _env("TMUXD_PORT") or free_port(self.bind))
        self.token = token if token is not None else _env("TMUXD_TOKEN")
        self.url_host = url_host or _env("TMUXD_URL_HOST")
        self.workspace = os.path.abspath(
            os.path.expanduser(workspace or _env("TMUXD_WORKSPACE", os.getcwd()))
        )
        self.shell = shell or _env("TMUXD_SHELL")
        self.history_limit = int(
            history_limit or _env("TMUXD_HISTORY_LIMIT", DEFAULT_HISTORY)
        )
        self.gc_ttl = float(gc_ttl or _env("TMUXD_GC_TTL", DEFAULT_GC_TTL))

        if self.bind not in ("127.0.0.1", "localhost", "::1") and not self.token:
            raise ValueError(
                "binding %s without a token would put a shell on this machine on the "
                "network. Set token=..." % self.bind
            )

        root = os.path.expanduser(state_dir or _env("TMUXD_STATE_DIR", "~/.tmuxd"))
        self.state_dir = os.path.join(root, self.socket_name)
        os.makedirs(self.state_dir, exist_ok=True)
        self._store = _state.Store(self.state_dir)

        # ~/.tmuxd.json is read here, by default, and only for these two paths.
        # It holds where the binaries are -- a fact about the machine, the same
        # answer whoever asks. Ports and tokens are behaviour and never come
        # from it (works/07-install.md §5).
        recorded = _toolchain.read()

        # Resolving the binary and reading `tmux -V` starts no server; the tmux
        # server itself stays lazy until the first session (works/01 §4.1).
        self.tmux_bin = _tmux.find_binary(tmux_bin)
        self.tmux_version = _tmux.check_version(self.tmux_bin)
        self._conf = self._render_conf()
        self._tmux = _tmux.Tmux(self.tmux_bin, self.tmux_socket, self._conf)

        self.ttyd_bin = _ttyd.find_binary(
            ttyd_bin, state_dir=self.state_dir,
            on_fallback=lambda old: warnings.warn(
                "ttyd on PATH (%s) is older than %d.%d; using the bundled build "
                "instead" % (old, *_ttyd.MIN_VERSION), RuntimeWarning, stacklevel=3),
        )
        # Which level answered, for info(). Not a guess: config is the only
        # one that can claim a path we already have in hand.
        self.ttyd_source = ("config" if self.ttyd_bin == recorded.get("ttyd")
                            else "bundled" if self.ttyd_is_bundled else "path")
        self._ttyd = _ttyd.ensure(
            binary=self.ttyd_bin,
            port=self.port,
            bind=self.bind,
            token=self.token,
            attach_script=self._attach_script(),
            tmux_socket=self.tmux_socket,
            tmux_bin=self.tmux_bin,
            state_path=os.path.join(self.state_dir, "ttyd-%d.json" % self.port),
        )

    # -- setup ----------------------------------------------------------

    def _render_conf(self):
        with open(os.path.join(DATA_DIR, "tmux.conf"), encoding="utf-8") as fh:
            body = fh.read()
        body = body.replace("@HISTORY_LIMIT@", str(self.history_limit))
        if self.shell:
            body += "\nset -g default-shell %s\n" % self.shell
        path = os.path.join(self.state_dir, "tmux.conf")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
        return path

    def _attach_script(self):
        src = os.path.join(DATA_DIR, "attach.sh")
        dst = os.path.join(self.state_dir, "attach.sh")
        if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
            shutil.copyfile(src, dst)
        os.chmod(dst, 0o700)
        return dst

    # -- ids -------------------------------------------------------------

    @staticmethod
    def _validate_id(sid):
        if not isinstance(sid, str) or not sid:
            raise BadId("session id must be a non-empty string", id=sid)
        if sid.startswith("-"):
            raise BadId("session id must not start with '-'", id=sid)
        bad = sorted(_FORBIDDEN & set(sid))
        if bad:
            raise BadId(
                "session id must not contain %s" % " ".join(repr(c) for c in bad), id=sid
            )
        if len(sid) > 200:
            raise BadId("session id is too long (max 200)", id=sid)

    def _generate_id(self):
        taken = set(self._live_ids()) | set(self._store.all())
        n = 0
        while str(n) in taken:
            n += 1
        return str(n)

    # -- sessions ---------------------------------------------------------

    def session(self, id=None, cwd=None, cmd=None, env=None):
        """Get it, or create it. The semantics of ``tmux new-session -A``.

        An existing session is **not** rebuilt because this call passed a
        different ``cwd`` or ``cmd`` -- the id decides. To swap the command,
        kill it and make a new one, explicitly.
        """
        sid = id if id is not None else self._generate_id()
        self._validate_id(sid)

        if self._tmux.has_session(sid):
            record = self._store.read(sid) or self._external_record(sid)
            record = self._store.touch_attached(sid) or record
            return Session(self, record)

        cwd = os.path.abspath(os.path.expanduser(cwd)) if cwd else self.workspace
        stamp = _state.now()
        self._tmux.new_session(sid, cwd=cwd, cmd=cmd, env=env)
        record = {
            "id": sid,
            "cwd": cwd,
            "cmd": cmd,
            "created_at": stamp,
            "last_attached": None,
        }
        self._store.write(record)
        return Session(self, record)

    def create(self, id=None, cwd=None, cmd=None, env=None):
        """Like :meth:`session`, but refuses to attach to an existing id."""
        sid = id if id is not None else self._generate_id()
        self._validate_id(sid)
        if self._tmux.has_session(sid):
            raise SessionExists('id "%s" already has a session' % sid, id=sid)
        return self.session(sid, cwd=cwd, cmd=cmd, env=env)

    def get(self, id):
        """Attach to an existing session, or raise. Never creates."""
        self._validate_id(id)
        if not self._tmux.has_session(id):
            raise NoSuchSession('no session with id "%s"' % id, id=id)
        record = self._store.read(id) or self._external_record(id)
        return Session(self, record)

    def has(self, id):
        try:
            self._validate_id(id)
        except BadId:
            return False
        return self._tmux.has_session(id)

    def sessions(self):
        """Every session, reconciled against tmux, with dead records swept.

        Reconciliation happens here and nowhere else -- no background thread,
        no timer. An imported library should not wake up on its own schedule
        to write to disk (works/02-session.md §7).
        """
        live = set(self._live_ids())
        stored = self._store.all()
        out = []

        for sid, record in sorted(stored.items()):
            if sid in live:
                out.append(Session(self, record))
            elif self._store.age(sid) > self.gc_ttl:
                self._store.delete(sid)  # only ever removes a JSON file
            else:
                out.append(Session(self, record))

        for sid in sorted(live - set(stored)):
            out.append(Session(self, self._external_record(sid)))

        return out

    def _live_ids(self):
        return [line for line in self._tmux.list_sessions("#{session_name}") if line]

    @staticmethod
    def _external_record(sid):
        # Someone went around the library and ran `tmux -L tmuxd new-session`.
        # Listed and usable, never adopted into a state file: turning a visible
        # anomaly into an invisible lie is worse than the anomaly.
        return {"id": sid, "cwd": None, "cmd": None, "external": True,
                "created_at": None, "last_attached": None}

    # -- entrance ---------------------------------------------------------

    def url_for(self, sid):
        host = self.url_host or (
            "127.0.0.1" if self.bind in ("0.0.0.0", "::", "") else self.bind
        )
        return "http://%s:%d/?arg=%s" % (host, self.port, quote(sid, safe=""))

    # -- introspection ----------------------------------------------------

    @property
    def ttyd_is_bundled(self):
        """True when the vendored build answered rather than one on PATH."""
        return os.path.dirname(self.ttyd_bin) == os.path.join(self.state_dir, "bin")

    def info(self):
        sessions = self.sessions()
        alive = sum(1 for s in sessions if s.alive)
        external = sum(1 for s in sessions if s.external)
        return {
            "version": __import__("tmuxd").__version__,
            "socket": self.socket_name,
            "state_dir": self.state_dir,
            "ttyd": {
                "version": self._ttyd.version,
                "port": self._ttyd.port,
                "bind": self._ttyd.bind,
                "pid": self._ttyd.pid,
                "owned": self._ttyd.owned,
                "bin": self.ttyd_bin,
                # Which lookup level answered (works/06 §3, works/07 §6).
                "source": self.ttyd_source,
            },
            "tmux": {
                "bin": self.tmux_bin,
                "version": self.tmux_version,
                "socket": self.tmux_socket,
                "running": self._tmux.server_running(),
            },
            "sessions": {
                "total": len(sessions),
                "alive": alive,
                "exited": len(sessions) - alive,
                "external": external,
            },
        }

    # -- lifecycle ---------------------------------------------------------

    def kill_tmux_server(self):
        """Destroy every session in this pool. Explicit only, never automatic."""
        self._tmux.kill_server()

    def close(self):
        """Take down what we started. Sessions are not ours to end."""
        if self._ttyd is not None:
            self._ttyd.stop()
            self._ttyd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __repr__(self):
        return "<Tmuxd socket=%s port=%s>" % (self.socket_name, self.port)
