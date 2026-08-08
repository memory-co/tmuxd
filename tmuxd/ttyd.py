"""The ttyd child process.

ttyd is the only way a human gets in, and it is a child of *your* process:
when your script exits, the web entrance goes with it, while the tmux
sessions it started keep running (works/01-library.md §3).

Binding that lifetime cannot live in a ``finally`` -- under SIGKILL no Python
code runs at all -- so it goes to the kernel via ``PR_SET_PDEATHSIG``.

ttyd holds no session state; it only execs ``attach.sh``. That is what makes
adoption safe: if a tmuxd-started ttyd is already listening on the port with
the same socket, a new ``Tmuxd`` reuses it rather than fighting over the port
or kicking connected browsers off (§3.1).
"""

import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

from .errors import PortInUse, TtydFailed, TtydMissing

PR_SET_PDEATHSIG = 1


def find_binary(explicit=None):
    path = explicit or os.environ.get("TMUXD_TTYD_BIN") or shutil.which("ttyd")
    if not path:
        raise TtydMissing("ttyd not found in PATH (set ttyd_bin= or TMUXD_TTYD_BIN)")
    return path


def _pdeathsig():
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except OSError:
        pass


def port_open(host, port, timeout=0.25):
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_ttyd(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return b"ttyd" in fh.read()
    except OSError:
        # Not Linux, or the process vanished. Fall back to "the pidfile said so".
        return True


class Ttyd:
    """A running ttyd, either ours or one we adopted."""

    def __init__(self, pid, port, bind, owned, popen=None, version=None):
        self.pid = pid
        self.port = port
        self.bind = bind
        self.owned = owned
        self.version = version
        self._popen = popen

    def alive(self):
        return pid_alive(self.pid)

    def stop(self, timeout=5.0):
        """Stop it only if it is ours. Adopted ttyds belong to someone else."""
        if not self.owned or not self.alive():
            return False
        if self._popen is not None:
            self._popen.terminate()
            try:
                self._popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._popen.kill()
                self._popen.wait(timeout=timeout)
        else:
            os.kill(self.pid, signal.SIGTERM)
        return True


def version_of(binary):
    proc = subprocess.run([binary, "--version"], capture_output=True, text=True)
    text = (proc.stdout or proc.stderr).strip()
    return text.replace("ttyd version ", "") or "unknown"


def ensure(
    *,
    binary,
    port,
    bind,
    token,
    attach_script,
    tmux_socket,
    tmux_bin,
    state_path,
    writable=True,
    startup_timeout=5.0,
):
    """Reuse a compatible ttyd on this port, or start one.

    Returns a :class:`Ttyd`. Raises :class:`PortInUse` when the port is taken
    by something that is not ours -- guessing here would mean either hijacking
    a stranger's service or silently talking to the wrong terminal.
    """
    recorded = _read(state_path)
    if recorded and recorded.get("port") == port:
        pid = recorded.get("pid")
        if (
            isinstance(pid, int)
            and pid_alive(pid)
            and _is_ttyd(pid)
            and recorded.get("tmux_socket") == tmux_socket
            and port_open(bind, port)
        ):
            return Ttyd(pid, port, bind, owned=False, version=recorded.get("version"))

    if port_open(bind, port):
        raise PortInUse(
            "port %d is already serving something that is not this tmuxd" % port,
            port=port,
        )

    argv = [binary, "-p", str(port), "-i", bind, "-a"]
    if writable:
        argv.append("-W")
    if token:
        argv += ["-c", "tmuxd:%s" % token]
    argv.append(str(attach_script))

    env = dict(os.environ)
    env["_TMUXD_SOCK"] = tmux_socket
    env["_TMUXD_TMUX"] = tmux_bin

    proc = subprocess.Popen(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=_pdeathsig,
        start_new_session=False,
    )

    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace").strip()
            raise TtydFailed(
                "ttyd exited immediately (%s)" % (out.splitlines()[-1] if out else proc.returncode),
                port=port,
            )
        if port_open(bind, port):
            break
        time.sleep(0.05)
    else:
        proc.terminate()
        raise TtydFailed("ttyd did not start listening on port %d" % port, port=port)

    handle = Ttyd(proc.pid, port, bind, owned=True, popen=proc, version=version_of(binary))
    _write(
        state_path,
        {
            "pid": proc.pid,
            "port": port,
            "bind": bind,
            "tmux_socket": tmux_socket,
            "version": handle.version,
            "started_by": os.getpid(),
        },
    )
    return handle


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, payload):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
