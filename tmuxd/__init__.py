"""tmuxd -- terminals that outlive the connection and can be typed into.

    from tmuxd import Tmuxd

    t = Tmuxd(port=12345, token="changeme")   # ttyd is up; tmux is not yet
    s = t.session(id="id5", cwd="~/proj", cmd="claude")
    s.send("run the tests", enter=True)
    print(s.url)                              # http://localhost:12345/?arg=id5

The library is the core. Embedding it needs no server at all; the CLI does,
and that is ``tmuxd serve`` plus the ``[server]`` extra (works/03-server.md).

Design notes live in ``docs/v1/works/``.
"""

__version__ = "2.0.0"

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
