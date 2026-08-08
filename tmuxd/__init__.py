"""tmuxd -- terminals that outlive the connection and can be typed into.

    from tmuxd import Tmuxd

    t = Tmuxd(port=12345, token="changeme")   # ttyd is up; tmux is not yet
    s = t.session(id="id5", cwd="~/proj", cmd="claude")
    s.send("run the tests", enter=True)
    print(s.url)                              # http://localhost:12345/?arg=id5

The library is the core. The CLI (``tmuxd.cli``) and the HTTP endpoint
(``tmuxd.http``, off by default) are shells around it.

Design notes live in ``docs/v1/works/``.
"""

__version__ = "1.0.0"

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


def __getattr__(name):
    # Kept lazy so `import tmuxd` never drags the HTTP shell in with it.
    if name == "RemoteTmuxd":
        from .remote import RemoteTmuxd

        return RemoteTmuxd
    raise AttributeError(name)
