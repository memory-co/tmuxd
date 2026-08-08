"""Exceptions.

Two bases, and the split is the useful part (works/03-http.md §6):

    SessionError   the caller can fix this by changing what it asked for
    PlatformError  the machine is in trouble; alert, do not retry

Every exception carries a ``code`` that the HTTP shell serialises verbatim,
so the wire format is a projection of these classes rather than a second
vocabulary.
"""


class TmuxdError(Exception):
    code = "error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self):
        d = {"error": self.code, "message": self.message}
        if self.details:
            d["details"] = self.details
        return d


class SessionError(TmuxdError):
    """The caller asked for something that does not line up. Fixable."""


class NoSuchSession(SessionError):
    code = "no_such_session"


class SessionExists(SessionError):
    code = "session_exists"


class BadId(SessionError):
    code = "bad_id"


class PlatformError(TmuxdError):
    """The environment is wrong. Alerting beats retrying."""


class TmuxGone(PlatformError):
    code = "tmux_gone"


class TmuxMissing(PlatformError):
    code = "tmux_missing"


class TtydMissing(PlatformError):
    code = "ttyd_missing"


class TtydFailed(PlatformError):
    code = "ttyd_failed"


class PortInUse(PlatformError):
    code = "port_in_use"


class Unauthorized(PlatformError):
    code = "unauthorized"


class Unreachable(PlatformError):
    code = "unreachable"


CODES = {}


def _register(cls, inherited=None):
    # Abstract bases inherit their parent's code; only concrete classes that
    # declare their own belong in the table.
    if cls.code != inherited:
        CODES[cls.code] = cls
    for sub in cls.__subclasses__():
        _register(sub, cls.code)


_register(TmuxdError)


def from_code(code, message, details=None):
    """Rebuild an exception from a wire error body (used by RemoteTmuxd)."""
    cls = CODES.get(code, TmuxdError)
    return cls(message, **(details or {}))
