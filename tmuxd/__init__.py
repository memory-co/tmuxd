"""tmuxd -- terminals that outlive the connection and can be typed into.

    from tmuxd import Tmuxd

    t = Tmuxd(base_path="/tty")               # ttyd is up; tmux is not yet
    s = t.session(id="id5", cwd="~/proj", cmd="claude")
    s.send("run the tests", enter=True)
    print(s.url)                              # /tty/?arg=id5
    app.mount("/tty", t.asgi())               # tmuxd[asgi]: the window on your port

    Tmuxd(port=12345, token="changeme")       # or ttyd on its own port:
                                              # http://127.0.0.1:12345/?arg=id5

The library is the core. Embedding it needs no server at all; the CLI does,
and that is ``tmuxd serve`` plus the ``[server]`` extra (works/03-server.md).

Design notes live in ``docs/v1/works/``.
"""

__version__ = "2.1.0"

from .core import Tmuxd
from .errors import (
    BadId,
    NoSuchSession,
    PlatformError,
    PortInUse,
    SessionError,
    SessionExists,
    TmuxdError,
    TmuxGone,
    TmuxMissing,
    TtydFailed,
    TtydMissing,
    Unauthorized,
    Unreachable,
)
from .session import Session

__all__ = [
    "Tmuxd",
    "Session",
    "TmuxdError",
    "SessionError",
    "NoSuchSession",
    "SessionExists",
    "BadId",
    "PlatformError",
    "TmuxGone",
    "TmuxMissing",
    "TtydMissing",
    "TtydFailed",
    "PortInUse",
    "Unauthorized",
    "Unreachable",
    "__version__",
]


# `tmuxd.server` is deliberately not imported here: `import tmuxd` must never
# drag FastAPI in behind it. It lives in the `[server]` extra, for the CLI path
# only (works/03-server.md §6).
