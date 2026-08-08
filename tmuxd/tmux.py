"""The only place that builds a tmux command line.

Not tidiness: tmux's target syntax, format strings and escaping rules are
full of traps, and spread across modules they get written wrong. Two of
those traps are encoded here and nowhere else.

**Exact matching.** ``-t work`` prefix-matches, so it happily resolves to
``workbench`` and delivers your keystrokes to the wrong terminal. The ``=``
prefix turns that off -- but *only session* targets accept a bare ``=work``.
Pane targets (send-keys, capture-pane, display -p) need the trailing colon,
``=work:``, or tmux cannot parse the target at all::

    tmux send-keys -t '=work'   ->  can't find pane: =work
    tmux send-keys -t 'work:'   ->  silently types into workbench
    tmux send-keys -t '=work:'  ->  can't find session: work     (correct)

**No server yet.** Before the first session exists there is no tmux server,
and ``list-sessions`` exits 1 with "error connecting to ...". That is an
empty list, not a failure -- see :func:`list_sessions`.
"""

import os
import shutil
import subprocess

from .errors import TmuxGone, TmuxMissing

# tmux prints one of these when the socket has no server behind it.
_NO_SERVER = ("no server running on", "error connecting to", "no such file or directory")

MIN_VERSION = (3, 0)


def find_binary(explicit=None):
    path = explicit or os.environ.get("TMUXD_TMUX_BIN") or shutil.which("tmux")
    if not path:
        raise TmuxMissing("tmux not found in PATH (set tmux_bin= or TMUXD_TMUX_BIN)")
    if not os.path.isabs(path):
        path = shutil.which(path) or path
    return path


def _parse_version(text):
    # "tmux 3.3a" / "tmux next-3.4" / "tmux 2.9a"
    tok = text.strip().split()[-1]
    tok = tok.split("-")[-1]
    major = digits = ""
    for ch in tok:
        if ch.isdigit():
            digits += ch
        elif ch == "." and not major:
            major, digits = digits, ""
        else:
            break
    try:
        return (int(major or digits or 0), int(digits or 0) if major else 0)
    except ValueError:
        return (0, 0)


def check_version(binary):
    out = subprocess.run([binary, "-V"], capture_output=True, text=True)
    raw = (out.stdout or out.stderr).strip()
    version = _parse_version(raw)
    if version < MIN_VERSION:
        raise TmuxMissing(
            "tmux %s is too old, tmuxd needs >= %d.%d (window-size arrived in 2.9)"
            % (raw, *MIN_VERSION),
            found=raw,
        )
    return raw.replace("tmux ", "")


def target_session(sid):
    """Exact-match target for session-level commands."""
    return "=" + sid


def target_pane(sid):
    """Exact-match target for pane-level commands. The colon is required."""
    return "=" + sid + ":"


class Tmux:
    """A handle on one tmux server, addressed by socket name."""

    def __init__(self, binary, socket, config=None):
        self.binary = binary
        self.socket = socket
        self.config = config

    def _argv(self, args):
        argv = [self.binary, "-L", self.socket]
        if self.config:
            argv += ["-f", str(self.config)]
        return argv + list(args)

    def run(self, *args, check=True):
        proc = subprocess.run(self._argv(args), capture_output=True, text=True)
        if proc.returncode and check:
            err = (proc.stderr or proc.stdout).strip()
            if self._is_no_server(err):
                raise TmuxGone("tmux server is not running", socket=self.socket)
            raise TmuxGone(err or "tmux failed", socket=self.socket, argv=list(args))
        return proc

    @staticmethod
    def _is_no_server(text):
        low = text.lower()
        return any(marker in low for marker in _NO_SERVER)

    # -- reads ----------------------------------------------------------

    def server_running(self):
        proc = subprocess.run(
            self._argv(["has-session", "-t", "=___tmuxd_probe___"]),
            capture_output=True,
            text=True,
        )
        return not self._is_no_server((proc.stderr or "") + (proc.stdout or ""))

    def list_sessions(self, fmt):
        """Sessions, or [] when the server has not been started yet.

        The empty case is the one that bites: a freshly constructed Tmuxd has
        no tmux server behind it, and tmux reports that as exit 1.
        """
        proc = self.run("list-sessions", "-F", fmt, check=False)
        if proc.returncode:
            err = (proc.stderr or proc.stdout).strip()
            if self._is_no_server(err):
                return []
            raise TmuxGone(err or "list-sessions failed", socket=self.socket)
        return [line for line in proc.stdout.splitlines() if line]

    def has_session(self, sid):
        proc = self.run("has-session", "-t", target_session(sid), check=False)
        return proc.returncode == 0

    def display(self, sid, fmt):
        proc = self.run("display-message", "-p", "-t", target_pane(sid), fmt, check=False)
        if proc.returncode:
            return None
        return proc.stdout.rstrip("\n")

    def count_clients(self, sid):
        proc = self.run("list-clients", "-t", target_session(sid), check=False)
        if proc.returncode:
            return 0
        return len([line for line in proc.stdout.splitlines() if line])

    # -- writes ---------------------------------------------------------

    def new_session(self, sid, cwd=None, cmd=None, env=None, width=None, height=None):
        args = ["new-session", "-d", "-s", sid]
        if cwd:
            args += ["-c", str(cwd)]
        if width and height:
            args += ["-x", str(width), "-y", str(height)]
        for key, value in (env or {}).items():
            args += ["-e", "%s=%s" % (key, value)]
        if cmd:
            args.append(cmd)
        self.run(*args)

    def kill_session(self, sid):
        self.run("kill-session", "-t", target_session(sid))

    def rename_session(self, sid, new):
        self.run("rename-session", "-t", target_session(sid), new)

    def send_literal(self, sid, text):
        self.run("send-keys", "-t", target_pane(sid), "-l", "--", text)

    def send_keys(self, sid, keys):
        self.run("send-keys", "-t", target_pane(sid), "--", *keys)

    def kill_server(self):
        self.run("kill-server", check=False)
