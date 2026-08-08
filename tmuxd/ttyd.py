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
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

from . import toolchain
from .errors import PortInUse, TtydFailed, TtydMissing

PR_SET_PDEATHSIG = 1

BUNDLED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ttyd")

# tmuxd drives -p, -i, -a, -W, -c and ttyd's own ?arg= semantics. An older ttyd
# fails when somebody opens a browser rather than at construction, which is the
# worst possible moment to find out.
MIN_VERSION = (1, 6)

# Upstream names its release assets by architecture; the bundled files keep
# those names, so adding one is dropping a file into data/ttyd (works/06 §3.3).
_ARCH = {
    "x86_64": "ttyd.x86_64",
    "amd64": "ttyd.x86_64",
    "aarch64": "ttyd.aarch64",
    "arm64": "ttyd.aarch64",
    "armv6l": "ttyd.arm",
    "armv7l": "ttyd.arm",
    "arm": "ttyd.arm",
}


def bundled_names():
    return sorted({name.split(".", 1)[1] for name in _ARCH.values()})


def bundled_binary():
    """The vendored build for this machine, or None.

    Linux only: upstream's static builds are musl ELFs, and macOS wants Mach-O
    (works/06 §3.3). Pretending otherwise would hand a macOS user a file that
    cannot execute.
    """
    if not sys.platform.startswith("linux"):
        return None
    name = _ARCH.get(platform.machine().lower())
    if not name:
        return None
    path = os.path.join(BUNDLED_DIR, name)
    return path if os.path.exists(path) else None


def parse_version(text):
    match = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def is_usable(path):
    """Runs, and new enough to have the flags we drive."""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    version = parse_version(proc.stdout or proc.stderr)
    return version is not None and version >= MIN_VERSION


def find_binary(explicit=None, *, state_dir=None, on_fallback=None):
    """Explicit, then ``~/.tmuxd.json``, then PATH, then the bundled build.

    PATH wins over the bundled copy on purpose: a distro-installed ttyd is one
    `apt upgrade` can fix, and ours is one only a release of tmuxd can. The
    bundled build serves the case where the system has none at all.

    ``~/.tmuxd.json`` sits above PATH because it is a **statement**, not a
    cache: with no ``--ttyd-bin`` flag on ``tmuxd install``, editing that file
    is how a person pins a binary. So a broken entry there raises, exactly as
    a broken ``ttyd_bin=`` does -- routing around it would run something other
    than what the file names while the file goes on claiming otherwise
    (works/07 §6.1).
    """
    explicit = explicit or os.environ.get("TMUXD_TTYD_BIN")
    if explicit:
        if not is_usable(explicit):
            raise TtydMissing(
                "ttyd_bin=%s cannot run, or is older than %d.%d. An explicitly "
                "named binary is never swapped out -- drop ttyd_bin to use the "
                "bundled one." % (explicit, *MIN_VERSION), path=explicit)
        return explicit

    recorded = toolchain.read().get("ttyd")
    if recorded:
        if not is_usable(recorded):
            raise TtydMissing(
                "%s (named by %s) cannot run, or is older than %d.%d.\n"
                "  fix that line, delete it, or run `tmuxd install` again.\n"
                "  it is not silently replaced -- you would be running "
                "something other than what the file says."
                % (recorded, toolchain.path(), *MIN_VERSION), path=recorded)
        return recorded

    on_path = shutil.which("ttyd")
    if on_path and is_usable(on_path):
        return on_path

    fallback = bundled_binary()
    if fallback:
        if on_path and on_fallback:
            # Not a failure: an old ttyd on PATH quietly steps aside.
            on_fallback(on_path)
        return _runnable_copy(fallback, state_dir)

    raise TtydMissing(
        "ttyd not found%s, and no bundled build for %s/%s.\n"
        "  bundled: %s\n"
        "  install: tmuxd install  (downloads a verified build from upstream)\n"
        "           brew install ttyd  |  "
        "https://github.com/tsl0922/ttyd/releases\n"
        "  ttyd is required -- tmuxd is tmux + ttyd, and will not start without it."
        % (" (the one on PATH is too old)" if on_path else "",
           sys.platform, platform.machine(), ", ".join(bundled_names())),
        platform=sys.platform, machine=platform.machine())


def _runnable_copy(source, state_dir):
    """Copy into the state dir and chmod it.

    package data loses its executable bit through wheel build and install, and
    site-packages may be read-only (containers, system Python). Same trick as
    attach.sh (works/06 §3.4).
    """
    if not state_dir:
        return source
    target_dir = os.path.join(state_dir, "bin")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "ttyd")
    if not os.path.exists(target) or os.path.getmtime(source) > os.path.getmtime(target):
        shutil.copyfile(source, target)
    os.chmod(target, 0o700)
    return target


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
