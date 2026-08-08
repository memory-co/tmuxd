"""The command line -- and the server it cannot work without.

`tmuxd ls` is a process that lives for tens of milliseconds. It can hold
neither ttyd (which would die with it) nor session state, so it does not
construct a :class:`~tmuxd.core.Tmuxd` at all -- it asks the one that can,
over the control port (works/03-server.md §1).

That client is stdlib ``urllib``: seven JSON endpoints need nothing more, and
the base install stays dependency-free. FastAPI and uvicorn live on the other
side of the wire, in ``tmuxd[server]``.

Exit codes matter more than the text: 0 fine, 2 usage, 3 no such session,
4 wrong state, 5 reserved, 6 the tmux server is gone.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import __version__
from .errors import from_code

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NO_SESSION = 3
EXIT_STATE = 4
# 5 was "cannot reach a remote tmuxd" before -H was removed. Left unused: a
# published exit code should not change meaning under someone's script.
EXIT_TMUX_GONE = 6

DEFAULT_PORT = 7681
DEFAULT_CONTROL_PORT = 7682
CONFIG_PATH = os.environ.get("TMUXD_CONFIG") or os.path.expanduser("~/.tmuxd.conf")

EXIT_FOR = {
    "no_such_session": EXIT_NO_SESSION,
    "session_exists": EXIT_STATE,
    "bad_id": EXIT_STATE,
    "port_in_use": EXIT_STATE,
    "unauthorized": EXIT_FAIL,
    "tmux_gone": EXIT_TMUX_GONE,
}


# -- config ---------------------------------------------------------------


def read_config(path=None):
    """``set -g key value`` lines, tmux style. Just another way to spell the
    constructor arguments -- not a second source of truth."""
    path = path or os.environ.get("TMUXD_CONFIG") or CONFIG_PATH
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = shlex.split(line)
        if len(parts) >= 4 and parts[0] == "set" and parts[1] == "-g":
            values[parts[2]] = parts[3]
        elif len(parts) >= 3 and parts[0] == "set":
            values[parts[1]] = parts[2]
    return values


class Settings:
    """Where an instance lives, resolved once per command."""

    def __init__(self, args):
        conf = read_config()
        self.socket = args.socket or os.environ.get("TMUXD_SOCKET") \
            or conf.get("socket") or "tmuxd"
        self.port = int(args.port or os.environ.get("TMUXD_PORT")
                        or conf.get("port") or DEFAULT_PORT)
        self.control_port = int(
            getattr(args, "control_port", None) or os.environ.get("TMUXD_CONTROL_PORT")
            or conf.get("control-port") or DEFAULT_CONTROL_PORT)
        self.bind = args.bind or os.environ.get("TMUXD_BIND") or conf.get("bind") \
            or "127.0.0.1"
        root = os.path.expanduser(args.state_dir or os.environ.get("TMUXD_STATE_DIR")
                                  or conf.get("state-dir") or "~/.tmuxd")
        self.state_dir = os.path.join(root, self.socket)
        self.conf = conf
        self._token = args.token or os.environ.get("TMUXD_TOKEN") or conf.get("token")

    @property
    def token(self):
        if self._token:
            return self._token
        try:
            with open(os.path.join(self.state_dir, "token"), encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            return None

    @property
    def base_url(self):
        host = "127.0.0.1" if self.bind in ("0.0.0.0", "::", "") else self.bind
        return "http://%s:%d" % (host, self.control_port)

    @property
    def daemon_file(self):
        return os.path.join(self.state_dir, "daemon.json")

    @property
    def tmux_socket(self):
        return self.socket if self.socket == "tmuxd" else "tmuxd-%s" % self.socket


# -- talking to the server -------------------------------------------------


class NoServer(Exception):
    pass


class Api:
    """stdlib HTTP client. Seven JSON endpoints need nothing more."""

    def __init__(self, settings, timeout=10.0):
        self.s = settings
        self.timeout = timeout

    def call(self, method, path, body=None, headers=None):
        data = None
        head = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            head["Content-Type"] = "application/json"
        if self.s.token:
            head["Authorization"] = "Bearer %s" % self.s.token
        head.update(headers or {})

        req = urllib.request.Request(self.s.base_url + path, data=data,
                                     headers=head, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            payload = _json_or_none(exc.read())
            if payload and "error" in payload:
                raise from_code(payload["error"], payload.get("message", ""),
                                payload.get("details"))
            raise NoServer("HTTP %d from %s" % (exc.code, self.s.base_url))
        except urllib.error.URLError:
            raise NoServer(
                "no server running (nothing listening on %s). Start one with "
                "`tmuxd start`." % self.s.base_url.replace("http://", ""))
        return _json_or_none(raw) or {}

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None, headers=None):
        return self.call("POST", path, body or {}, headers)

    def delete(self, path):
        return self.call("DELETE", path)


def _json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _quote(sid):
    from urllib.parse import quote

    return quote(sid, safe="")


# -- the server process ----------------------------------------------------


def cmd_serve(args):
    s = Settings(args)
    try:
        from .core import Tmuxd
        from .server import serve
    except ModuleNotFoundError as exc:
        return _fail(str(exc), EXIT_FAIL)

    t = Tmuxd(
        port=s.port, bind=s.bind, token=s.token, socket=s.socket,
        history_limit=s.conf.get("history-limit"),
        tmux_bin=s.conf.get("tmux-bin") or None,
        state_dir=os.path.dirname(s.state_dir),
    )
    if t.token:
        _write(os.path.join(t.state_dir, "token"), t.token, mode=0o600)
    with open(s.daemon_file, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "port": t.port,
                   "control_port": s.control_port, "started_at": time.time()}, fh)

    _banner(t, s.control_port)
    try:
        serve(t, control_port=s.control_port, bind=s.bind, token=t.token)
    except KeyboardInterrupt:
        pass
    finally:
        t.close()
        _unlink(s.daemon_file)
    return EXIT_OK


def _banner(t, control_port):
    # Block-buffered under a pipe or systemd, so an unflushed banner is a
    # banner nobody sees until the process exits.
    print("tmuxd %s" % __version__, flush=True)
    print("ttyd:     http://%s:%d        %s" % (
        t.bind, t.port, ("token=%s…" % t.token[:8]) if t.token else "no token (open)"))
    print("control:  http://%s:%d/api    (CLI 打这个)" % (t.bind, control_port))
    info = t.info()["tmux"]
    print("tmux:     %s %s   socket=%s (dedicated)   %s" % (
        info["bin"], info["version"], info["socket"],
        "server running" if info["running"] else "server not started yet"), flush=True)


def cmd_start(args):
    s = Settings(args)
    existing = _read_json(s.daemon_file)
    if existing and _alive(existing.get("pid")):
        print("already running (pid %d)" % existing["pid"])
        return EXIT_OK

    # Global flags come before the subcommand -- argparse will not take them
    # afterwards, and getting that wrong makes `start` fail in a way whose only
    # trace is the daemon log.
    argv = [sys.executable, "-m", "tmuxd"]
    for flag, value in (("-L", args.socket), ("--port", args.port),
                        ("--control-port", args.control_port), ("--bind", args.bind),
                        ("--token", args.token), ("--state-dir", args.state_dir)):
        if value:
            argv += [flag, str(value)]
    argv.append("serve")

    os.makedirs(s.state_dir, exist_ok=True)
    log_path = os.path.join(s.state_dir, "daemon.log")
    log = open(log_path, "ab")
    subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)

    api = Api(s)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            api.get("/api/health")
        except NoServer:
            time.sleep(0.1)
            continue
        return cmd_info(args)

    sys.stderr.write("tmuxd did not come up. Tail of %s:\n%s" % (log_path, _tail(log_path)))
    return EXIT_FAIL


def cmd_stop(args):
    s = Settings(args)
    record = _read_json(s.daemon_file)
    if not record or not _alive(record.get("pid")):
        print("not running")
        return EXIT_OK

    live = 0
    try:
        live = sum(1 for x in Api(s).get("/api/sessions")["sessions"]
                   if x.get("status") == "alive")
    except (NoServer, KeyError):
        pass

    os.kill(record["pid"], 15)
    for _ in range(80):
        if not _alive(record["pid"]):
            break
        time.sleep(0.1)
    print("server 已停。%d 个会话仍在运行(tmuxd start 回来即可)。" % live)
    return EXIT_OK


def cmd_status(args):
    s = Settings(args)
    record = _read_json(s.daemon_file)
    pid = record.get("pid") if record else None
    running = bool(pid and _alive(pid))

    control_up = ttyd_up = False
    try:
        Api(s, timeout=3).get("/api/health")
        control_up = True
    except NoServer:
        pass
    except Exception:
        control_up = True
    from .ttyd import port_open

    ttyd_up = port_open(s.bind, s.port)

    if args.json:
        print(json.dumps({"server": running, "pid": pid, "ttyd_port": s.port,
                          "control_port": s.control_port, "ttyd": ttyd_up,
                          "control": control_up}, indent=2))
    else:
        print("server:   %s" % ("running (pid %d)" % pid if running else "not running"))
        print("ttyd:     %s" % ("listening on %d" % s.port if ttyd_up else "not listening"))
        print("control:  %s" % ("listening on %d" % s.control_port if control_up
                                else "not listening"))
    return EXIT_OK if running or control_up else EXIT_FAIL


def cmd_info(args):
    s = Settings(args)
    info = Api(s).get("/api/info")
    if args.json:
        print(json.dumps(info, indent=2))
        return EXIT_OK
    print("tmuxd     %s" % info["version"])
    ttyd = info.get("ttyd") or {}
    print("ttyd      %s  port=%s  owned=%s" % (
        ttyd.get("version") or "?", ttyd.get("port"), ttyd.get("owned")))
    print("control   port=%s" % (info.get("control") or {}).get("port"))
    print("tmux      %(version)s  %(bin)s  socket=%(socket)s  running=%(running)s"
          % info["tmux"])
    print("sessions  %(total)d total  %(alive)d alive  %(exited)d exited  "
          "%(external)d external" % info["sessions"])
    return EXIT_OK


# -- sessions --------------------------------------------------------------


def cmd_new(args):
    s = Settings(args)
    # argparse.REMAINDER hands back the "--" separator too; it is punctuation,
    # not the first word of the command.
    words = list(args.command or [])
    if words and words[0] == "--":
        words = words[1:]
    body = {
        "id": args.id,
        "cwd": os.path.abspath(os.path.expanduser(args.cwd)) if args.cwd else None,
        "cmd": " ".join(shlex.quote(w) for w in words) if words else None,
        "env": dict(pair.split("=", 1) for pair in args.env) if args.env else None,
    }
    session = Api(s).post("/api/sessions", body)
    if args.json:
        print(json.dumps(session, indent=2))
    else:
        print("%s  →  %s" % (session["id"], session["url"]))
    return EXIT_OK


def cmd_ls(args):
    s = Settings(args)
    sessions = Api(s).get("/api/sessions")["sessions"]
    if args.json:
        print(json.dumps(sessions, indent=2))
        return EXIT_OK
    if args.format:
        for item in sessions:
            print(_format(args.format, item))
        return EXIT_OK
    for item in sessions:
        if item["status"] == "alive":
            n = item["clients"]
            print("%-20s alive   %d client%s  %-8s %s" % (
                item["id"], n, " " if n == 1 else "s",
                item.get("current_command") or "-", item.get("cwd") or "-"))
        else:
            print("%-20s exited" % item["id"])
    return EXIT_OK


def cmd_url(args):
    s = Settings(args)
    url = Api(s).get("/api/sessions/" + _quote(args.id))["url"]
    print(url)
    if args.open:
        opener = s.conf.get("open-cmd")
        argv = (shlex.split(opener.replace("%u", url)) if opener
                else ["open" if sys.platform == "darwin" else "xdg-open", url])
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return EXIT_OK


def cmd_kill(args):
    s = Settings(args)
    out = Api(s).delete("/api/sessions/" + _quote(args.id))
    clients = out.get("clients") or 0
    print("killed %s%s" % (args.id,
                           " (%d client(s) thrown out)" % clients if clients else ""))
    return EXIT_OK


def cmd_has(args):
    s = Settings(args)
    from .errors import NoSuchSession

    try:
        Api(s).get("/api/sessions/" + _quote(args.id))
    except NoSuchSession:
        return EXIT_NO_SESSION
    return EXIT_OK


def cmd_send(args):
    s = Settings(args)
    Api(s).post("/api/sessions/%s/keys" % _quote(args.id),
                {"text": args.text, "enter": bool(args.enter)})
    print("✓ sent")
    return EXIT_OK


def cmd_keys(args):
    s = Settings(args)
    Api(s).post("/api/sessions/%s/keys" % _quote(args.id), {"keys": list(args.keys)})
    print("✓ sent")
    return EXIT_OK


def cmd_install(args):
    """Local on purpose, and never required.

    It touches no server and constructs no ``Tmuxd`` -- it is the command you
    run *because* those do not work yet. A ready machine never runs it
    (works/07-install.md).
    """
    from . import install as I
    from . import toolchain
    from . import ttyd as T
    from .tmux import MIN_VERSION as TMUX_MIN

    marks = {"work": "· ", "warn": "⚠ ", "fail": "✗ ", "note": "  "}
    current = [""]

    def report(level, text):
        # One stream, flushed: this is a running report, and progress arriving
        # after the conclusion it explains is worse than no progress at all.
        # (Same trap as the `serve` banner -- stdout block-buffers under a pipe.)
        print("%-6s %s%s" % (current[0], marks.get(level, "  "), text), flush=True)

    # -- tmux: detect; install only when already root; otherwise say the words
    current[0] = "tmux"
    named = bool(args.tmux_bin)
    tmux_bin = args.tmux_bin or _tmux_lookup()
    if not tmux_bin:
        report("fail", "not found")
    elif not I.tmux_is_usable(tmux_bin):
        report("fail", "%s cannot run, or is older than %d.%d"
               % (tmux_bin, *TMUX_MIN))
        tmux_bin = None
    # A binary you named by hand is never quietly replaced with another one --
    # same rule as ttyd_bin= (works/06 §3).
    if not tmux_bin and not named:
        tmux_bin = I.install_tmux(report)
    if tmux_bin:
        print("tmux   ✓ %-12s %s" % (I.tmux_version(tmux_bin) or "?", tmux_bin),
              flush=True)

    # -- ttyd: network first, bundled second
    current[0] = "ttyd"
    if args.ttyd_bin:
        ttyd_bin, how = args.ttyd_bin, "explicit"
        if not T.is_usable(ttyd_bin):
            report("fail", "%s cannot run, or is older than %d.%d"
                   % (ttyd_bin, *T.MIN_VERSION))
            ttyd_bin = None
    else:
        ttyd_bin, how = I.install_ttyd(refresh=args.refresh, report=report)
    if ttyd_bin:
        print("ttyd   ✓ %-12s %s%s" % (
            T.version_of(ttyd_bin), ttyd_bin,
            "" if how in ("recorded", "path") else "  (%s)" % how), flush=True)

    # -- record it, so the next Tmuxd() finds it without looking
    if not tmux_bin and not ttyd_bin:
        return EXIT_FAIL
    before = toolchain.read()
    after = toolchain.write(tmux=tmux_bin, ttyd=ttyd_bin)
    print(("写入   %s" % toolchain.path()) if after != before else "无需改动。",
          flush=True)
    return EXIT_OK if (tmux_bin and ttyd_bin) else EXIT_FAIL


def _tmux_lookup():
    """What ``Tmuxd()`` would resolve: the recorded one if it still works, else PATH."""
    from . import install as I
    from . import toolchain

    recorded = toolchain.read().get("tmux")
    if recorded and I.tmux_is_usable(recorded):
        return recorded
    return shutil.which("tmux")


def cmd_kill_server(args):
    """Local on purpose: tearing the pool down has to work when the server is
    already down, which is exactly when you reach for it."""
    s = Settings(args)
    if not args.tmux:
        return _fail("pass --tmux to confirm: this destroys every session in "
                     "this pool", EXIT_USAGE)
    if sys.stdin.isatty() and not args.yes:
        if input("kill the tmux server for socket %s? [y/N] "
                 % s.tmux_socket).strip().lower() not in ("y", "yes"):
            return EXIT_OK
    from .tmux import find_binary

    subprocess.run([find_binary(s.conf.get("tmux-bin")), "-L", s.tmux_socket,
                    "kill-server"], capture_output=True)
    print("tmux server killed (socket %s). Your own tmux is untouched." % s.tmux_socket)
    return EXIT_OK


# -- helpers ---------------------------------------------------------------


def _format(spec, item):
    values = {
        "#{session_id}": item["id"],
        "#{session_name}": item["id"],
        "#{session_status}": item["status"],
        "#{session_attached}": str(item.get("clients") or 0),
        "#{session_cwd}": item.get("cwd") or "",
        "#{session_cmd}": item.get("cmd") or "",
        "#{session_url}": item.get("url") or "",
        "#{pane_current_command}": item.get("current_command") or "",
    }
    out = spec
    for key, value in values.items():
        out = out.replace(key, value)
    return out


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, text, mode=0o600):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def _tail(path, lines=15):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except OSError:
        return "(no log)\n"


def _fail(message, code):
    sys.stderr.write("✗ %s\n" % message)
    return code


# -- parser ----------------------------------------------------------------


def add_id(parser, *, required=True):
    """``-s`` / ``--id``, plus three hidden legacy aliases.

    The two halves answer different questions and both are needed. ``-s``
    selects: *which session*. ``--id`` says what you select it by -- the
    library takes ``id=``, the API sends ``{"id": ...}``, and webmuxd's
    session object calls it that too, so the long name is the one that
    survives being translated between layers (works/04-cli.md §3.1).

    1.0.0 spelled this ``-t`` / ``--target`` on most commands and ``-s`` on
    ``new``. 2.0 has one spelling and no hidden aliases: an alias that appears
    in neither --help nor the docs is a promise nobody can find, and this is
    a deliberately breaking release (CHANGELOG.md).

    Requiredness is checked in main() rather than by argparse, so the message
    can name the flag instead of printing a usage block.
    """
    parser.add_argument("-s", "--id", dest="id", metavar="ID",
                        help="session id" + ("" if required else " (generated when omitted)"))
    parser.set_defaults(_needs_id=required)
    return parser


def build_parser():
    p = argparse.ArgumentParser(prog="tmuxd", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version="tmuxd %s" % __version__)
    p.add_argument("-L", "--socket", help="instance name (also picks the tmux socket)")
    p.add_argument("--port", type=int, help="ttyd port (default 7681)")
    p.add_argument("--control-port", type=int, help="control API port (default 7682)")
    p.add_argument("--token")
    p.add_argument("--bind")
    p.add_argument("--state-dir")
    p.add_argument("--json", action="store_true", help="raw JSON output")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the server in the foreground").set_defaults(func=cmd_serve)
    sub.add_parser("start", help="run the server in the background").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop the server; sessions keep running").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="check back on what is actually up").set_defaults(func=cmd_status)
    sub.add_parser("info", help="versions, both ports, tmux, session counts").set_defaults(func=cmd_info)

    new = sub.add_parser("new", help="create or attach to a session")
    add_id(new, required=False)
    new.add_argument("-c", "--cwd")
    new.add_argument("-e", "--env", action="append", metavar="K=V")
    new.add_argument("command", nargs=argparse.REMAINDER, help="command to run (after --)")
    new.set_defaults(func=cmd_new)

    ls = sub.add_parser("ls", help="list sessions")
    ls.add_argument("-F", "--format", help="tmux-style format string")
    ls.set_defaults(func=cmd_ls)

    url = sub.add_parser("url", help="print the entrance URL")
    add_id(url)
    url.add_argument("-o", "--open", action="store_true", help="open a browser too")
    url.set_defaults(func=cmd_url)

    kill = sub.add_parser("kill", help="destroy a session")
    add_id(kill)
    kill.set_defaults(func=cmd_kill)

    has = sub.add_parser("has", help="exit 0 if the session exists")
    add_id(has)
    has.set_defaults(func=cmd_has)

    send = sub.add_parser("send", help="type literal text into a session")
    add_id(send)
    send.add_argument("text")
    send.add_argument("--enter", action="store_true")
    send.set_defaults(func=cmd_send)

    keys = sub.add_parser("keys", help="press tmux key names")
    add_id(keys)
    keys.add_argument("keys", nargs="+")
    keys.set_defaults(func=cmd_keys)

    # Not a step in any quick start: a ready machine never runs this, and
    # Tmuxd() does not check whether you have (works/07-install.md).
    inst = sub.add_parser("install", help="get tmux and ttyd in place (only if they are not)")
    inst.add_argument("--refresh", action="store_true",
                      help="fetch ttyd again even if a usable one is already here")
    inst.add_argument("--tmux-bin", metavar="PATH", help="record this tmux instead of looking")
    inst.add_argument("--ttyd-bin", metavar="PATH", help="record this ttyd instead of fetching")
    inst.set_defaults(func=cmd_install)

    ks = sub.add_parser("kill-server", help="destroy every session in this pool")
    ks.add_argument("--tmux", action="store_true", help="required confirmation")
    ks.add_argument("-y", "--yes", action="store_true")
    ks.set_defaults(func=cmd_kill_server)

    return p


def main(argv=None):
    # argparse raises SystemExit on a usage error. main() is documented to
    # *return* an exit code -- __main__ does sys.exit(main()) -- so catch it
    # here rather than letting an embedder get an exception out of a function
    # that promised an int.
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # --help / --version raise SystemExit(0); a usage error raises 2.
        # Pass the code through -- do not "helpfully" turn 0 into a failure.
        return EXIT_OK if exc.code is None else int(exc.code)
    if getattr(args, "_needs_id", False) and not args.id:
        return _fail("%s needs a session: -s ID (or --id ID)" % args.command,
                     EXIT_USAGE)
    try:
        return args.func(args)
    except NoServer as exc:
        return _fail(str(exc), EXIT_FAIL)
    except Exception as exc:  # library / wire errors carry a code
        code = getattr(exc, "code", None)
        if code is None:
            raise
        return _fail("%s: %s" % (code, getattr(exc, "message", exc)),
                     EXIT_FOR.get(code, EXIT_FAIL))
    except KeyboardInterrupt:
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
