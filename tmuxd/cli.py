"""The command line -- the third shell around the library.

Locally it calls the library directly; ``-H`` is the only mode that speaks
HTTP. Eleven commands, eleven library calls (works/04-cli.md §9).

Exit codes matter more than the text: 0 fine, 2 usage, 3 no such session,
4 wrong state, 5 cannot reach a remote, 6 the tmux server is gone.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time

from . import __version__
from .errors import (
    NoSuchSession,
    PlatformError,
    SessionError,
    TmuxdError,
    TmuxGone,
    Unauthorized,
    Unreachable,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NO_SESSION = 3
EXIT_STATE = 4
EXIT_UNREACHABLE = 5
EXIT_TMUX_GONE = 6

CONFIG_PATH = os.environ.get("TMUXD_CONFIG") or os.path.expanduser("~/.tmuxd.conf")
REMOTE_ONLY_MESSAGE = (
    "not available against a remote tmuxd -- ttyd's lifetime belongs to the "
    "process over there"
)


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


# -- connecting -----------------------------------------------------------


def build(args, *, start_ttyd=False):
    """A local Tmuxd, or a RemoteTmuxd when -H was given."""
    if args.host:
        from .remote import RemoteTmuxd

        return RemoteTmuxd(args.host, token=args.token or os.environ.get("TMUXD_TOKEN"))

    from .core import Tmuxd

    conf = read_config()
    port = args.port or conf.get("port")
    return Tmuxd(
        port=int(port) if port else None,
        bind=args.bind or conf.get("bind") or None,
        token=args.token or conf.get("token") or None,
        socket=args.socket or conf.get("socket") or None,
        history_limit=conf.get("history-limit"),
        tmux_bin=conf.get("tmux-bin") or None,
        state_dir=args.state_dir or conf.get("state-dir") or None,
        start_ttyd=start_ttyd,
    )


def daemon_file(t):
    return os.path.join(t.state_dir, "daemon.json")


# -- commands -------------------------------------------------------------


def cmd_serve(args):
    from .core import Tmuxd

    conf = read_config()
    port = args.port or conf.get("port") or 7681
    t = Tmuxd(
        port=int(port),
        bind=args.bind or conf.get("bind") or None,
        token=args.token or conf.get("token") or None,
        socket=args.socket or conf.get("socket") or None,
        history_limit=conf.get("history-limit"),
        state_dir=args.state_dir or conf.get("state-dir") or None,
    )
    with open(daemon_file(t), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "port": t.port, "started_at": time.time()}, fh)

    if args.http_port:
        t.serve_http(args.http_port, token=t.token)
        print("http:  http://%s:%d/api" % (t.bind, args.http_port))

    _banner(t)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        t.close()
        try:
            os.unlink(daemon_file(t))
        except OSError:
            pass
    return EXIT_OK


def _banner(t):
    print("tmuxd %s" % __version__)
    print("ttyd:  http://%s:%d   %s" % (
        t.bind, t.port, ("token=%s…" % t.token[:8]) if t.token else "no token (open)"))
    info = t.info()["tmux"]
    print("tmux:  %s %s   socket=%s (dedicated)   %s" % (
        info["bin"], info["version"], info["socket"],
        "server running" if info["running"] else "server not started yet"))


def cmd_start(args):
    t = build(args, start_ttyd=False)
    _reject_remote(t)
    path = daemon_file(t)
    existing = _read_json(path)
    if existing and _alive(existing.get("pid")):
        print("already running (pid %d)" % existing["pid"])
        return EXIT_OK

    # Global flags come before the subcommand -- argparse will not take them
    # afterwards, and getting that wrong makes `start` fail in a way whose
    # only trace is the daemon log.
    argv = [sys.executable, "-m", "tmuxd"]
    for flag, value in (
        ("-L", args.socket), ("--port", args.port), ("--bind", args.bind),
        ("--token", args.token), ("--state-dir", args.state_dir),
    ):
        if value:
            argv += [flag, str(value)]
    argv.append("serve")
    if args.http_port:
        argv += ["--http-port", str(args.http_port)]

    log = open(os.path.join(t.state_dir, "daemon.log"), "ab")
    subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)

    deadline = time.time() + 10
    while time.time() < deadline:
        record = _read_json(path)
        if record and _alive(record.get("pid")):
            _banner(build(args, start_ttyd=False))
            return EXIT_OK
        time.sleep(0.1)

    sys.stderr.write("tmuxd did not come up. Tail of %s:\n" % log.name)
    sys.stderr.write(_tail(log.name))
    return EXIT_FAIL


def cmd_stop(args):
    t = build(args, start_ttyd=False)
    _reject_remote(t)
    record = _read_json(daemon_file(t))
    if not record or not _alive(record.get("pid")):
        print("not running")
        return EXIT_OK
    os.kill(record["pid"], 15)
    for _ in range(50):
        if not _alive(record["pid"]):
            break
        time.sleep(0.1)
    live = [s for s in t.sessions() if s.alive]
    print("ttyd stopped. %d session(s) still running (tmuxd start brings the door back)."
          % len(live))
    return EXIT_OK


def cmd_status(args):
    t = build(args, start_ttyd=False)
    _reject_remote(t)
    record = _read_json(daemon_file(t))
    pid = record.get("pid") if record else None
    running = bool(pid and _alive(pid))
    listening = False
    if t.port:
        from .ttyd import port_open

        listening = port_open(t.bind, t.port)
    if args.json:
        print(json.dumps({"daemon": running, "pid": pid, "port": t.port,
                          "listening": listening}, indent=2))
    else:
        print("daemon:    %s" % ("running (pid %d)" % pid if running else "not running"))
        print("ttyd port: %s" % ("listening on %d" % t.port if listening else "not listening"))
    return EXIT_OK if running or listening else EXIT_FAIL


def cmd_info(args):
    t = build(args, start_ttyd=False)
    info = t.info()
    if args.json:
        print(json.dumps(info, indent=2))
        return EXIT_OK
    print("tmuxd   %s" % info["version"])
    if info.get("ttyd"):
        print("ttyd    %(version)s  port=%(port)s  owned=%(owned)s" % info["ttyd"])
    print("tmux    %(version)s  %(bin)s  socket=%(socket)s  running=%(running)s" % info["tmux"])
    print("sessions %(total)d total  %(alive)d alive  %(exited)d exited  %(external)d external"
          % info["sessions"])
    return EXIT_OK


def cmd_new(args):
    t = build(args)
    # argparse.REMAINDER hands back the "--" separator too; it is punctuation,
    # not the first word of the command.
    words = list(args.command or [])
    if words and words[0] == "--":
        words = words[1:]
    cmd = " ".join(shlex.quote(part) for part in words) if words else None
    env = dict(pair.split("=", 1) for pair in args.env) if args.env else None
    session = t.session(id=args.session, cwd=args.cwd, cmd=cmd, env=env)
    if args.json:
        print(json.dumps(session.to_dict(), indent=2))
    else:
        print("%s  →  %s" % (session.id, session.url or "(no ttyd port configured)"))
    return EXIT_OK


def cmd_ls(args):
    t = build(args)
    sessions = t.sessions()
    if args.json:
        print(json.dumps([s.to_dict() for s in sessions], indent=2))
        return EXIT_OK
    if args.format:
        for s in sessions:
            print(_format(args.format, s))
        return EXIT_OK
    for s in sessions:
        status = s.status
        if status == "alive":
            print("%-20s alive   %d client%s  %-8s %s" % (
                s.id, s.clients, " " if s.clients == 1 else "s",
                s.current_command or "-", s.cwd or "-"))
        else:
            print("%-20s exited  %s" % (s.id, "swept in %d days" % (t.gc_ttl // 86400)))
    return EXIT_OK


def cmd_url(args):
    t = build(args)
    session = t.get(args.target)
    url = session.url
    if not url:
        sys.stderr.write("no ttyd port configured for this instance\n")
        return EXIT_FAIL
    print(url)
    if args.open:
        if not args.host:
            from .ttyd import port_open

            if not port_open(t.bind, t.port):
                sys.stderr.write(
                    "warning: nothing is listening on port %d -- start one with "
                    "`tmuxd start`\n" % t.port)
        opener = read_config().get("open-cmd")
        argv = (shlex.split(opener.replace("%u", url)) if opener
                else ["xdg-open" if sys.platform != "darwin" else "open", url])
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return EXIT_OK


def cmd_kill(args):
    t = build(args)
    clients = t.get(args.target).kill()
    print("killed %s%s" % (args.target,
                           " (%d client(s) thrown out)" % clients if clients else ""))
    return EXIT_OK


def cmd_rename(args):
    t = build(args)
    t.get(args.target).rename(args.new_id)
    print("%s → %s" % (args.target, args.new_id))
    return EXIT_OK


def cmd_has(args):
    t = build(args)
    return EXIT_OK if t.has(args.target) else EXIT_NO_SESSION


def cmd_send(args):
    t = build(args)
    t.get(args.target).send(args.text, enter=args.enter)
    print("✓ sent")
    return EXIT_OK


def cmd_keys(args):
    t = build(args)
    t.get(args.target).send_key(*args.keys)
    print("✓ sent")
    return EXIT_OK


def cmd_kill_server(args):
    t = build(args)
    _reject_remote(t)
    if not args.tmux:
        sys.stderr.write("pass --tmux to confirm: this destroys every session in "
                         "this pool\n")
        return EXIT_USAGE
    if sys.stdin.isatty() and not args.yes:
        answer = input("kill the tmux server for socket %s? [y/N] " % t.tmux_socket)
        if answer.strip().lower() not in ("y", "yes"):
            return EXIT_OK
    t.kill_tmux_server()
    print("tmux server killed (socket %s). Your own tmux is untouched." % t.tmux_socket)
    return EXIT_OK


# -- helpers --------------------------------------------------------------


def _format(spec, session):
    values = {
        "#{session_id}": session.id,
        "#{session_name}": session.id,
        "#{session_status}": session.status,
        "#{session_attached}": str(session.clients),
        "#{session_cwd}": session.cwd or "",
        "#{session_cmd}": session.cmd or "",
        "#{session_url}": session.url or "",
        "#{pane_current_command}": session.current_command or "",
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


def _reject_remote(t):
    if t.__class__.__name__ == "RemoteTmuxd":
        raise SystemExit(_fail(REMOTE_ONLY_MESSAGE, EXIT_USAGE))


def _fail(message, code):
    sys.stderr.write("✗ %s\n" % message)
    return code


# -- parser ---------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(prog="tmuxd", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version="tmuxd %s" % __version__)
    p.add_argument("-L", "--socket", help="instance name (also picks the tmux socket)")
    p.add_argument("-H", "--host", default=os.environ.get("TMUXD_HOST"),
                   help="drive a remote tmuxd over HTTP")
    p.add_argument("--token")
    p.add_argument("--port", type=int)
    p.add_argument("--bind")
    p.add_argument("--state-dir")
    p.add_argument("--json", action="store_true", help="raw JSON output")

    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run in the foreground, holding ttyd up")
    serve.add_argument("--http-port", type=int, help="also expose the HTTP shell")
    serve.set_defaults(func=cmd_serve)

    start = sub.add_parser("start", help="run in the background")
    start.add_argument("--http-port", type=int)
    start.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="stop ttyd; sessions keep running").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="check back on what is actually up").set_defaults(func=cmd_status)
    sub.add_parser("info", help="versions, ttyd, tmux, session counts").set_defaults(func=cmd_info)

    new = sub.add_parser("new", help="create or attach to a session")
    new.add_argument("-s", "--session", help="session id (generated when omitted)")
    new.add_argument("-c", "--cwd")
    new.add_argument("-e", "--env", action="append", metavar="K=V")
    new.add_argument("command", nargs=argparse.REMAINDER,
                     help="command to run (after --)")
    new.set_defaults(func=cmd_new)

    ls = sub.add_parser("ls", help="list sessions")
    ls.add_argument("-F", "--format", help="tmux-style format string")
    ls.set_defaults(func=cmd_ls)

    url = sub.add_parser("url", help="print the entrance URL")
    url.add_argument("-t", "--target", required=True)
    url.add_argument("-o", "--open", action="store_true", help="open a browser too")
    url.set_defaults(func=cmd_url)

    kill = sub.add_parser("kill", help="destroy a session")
    kill.add_argument("-t", "--target", required=True)
    kill.set_defaults(func=cmd_kill)

    rename = sub.add_parser("rename", help="change a session id")
    rename.add_argument("-t", "--target", required=True)
    rename.add_argument("new_id")
    rename.set_defaults(func=cmd_rename)

    has = sub.add_parser("has", help="exit 0 if the session exists")
    has.add_argument("-t", "--target", required=True)
    has.set_defaults(func=cmd_has)

    send = sub.add_parser("send", help="type literal text into a session")
    send.add_argument("-t", "--target", required=True)
    send.add_argument("text")
    send.add_argument("--enter", action="store_true")
    send.set_defaults(func=cmd_send)

    keys = sub.add_parser("keys", help="press tmux key names")
    keys.add_argument("-t", "--target", required=True)
    keys.add_argument("keys", nargs="+")
    keys.set_defaults(func=cmd_keys)

    ks = sub.add_parser("kill-server", help="destroy every session in this pool")
    ks.add_argument("--tmux", action="store_true", help="required confirmation")
    ks.add_argument("-y", "--yes", action="store_true")
    ks.set_defaults(func=cmd_kill_server)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        return exc.code
    except NoSuchSession as exc:
        return _fail(exc.message, EXIT_NO_SESSION)
    except SessionError as exc:
        return _fail("%s: %s" % (exc.code, exc.message), EXIT_STATE)
    except (Unreachable, Unauthorized) as exc:
        return _fail("%s: %s" % (exc.code, exc.message), EXIT_UNREACHABLE)
    except TmuxGone as exc:
        return _fail("%s: %s" % (exc.code, exc.message), EXIT_TMUX_GONE)
    except PlatformError as exc:
        return _fail("%s: %s" % (exc.code, exc.message), EXIT_FAIL)
    except TmuxdError as exc:
        return _fail("%s: %s" % (exc.code, exc.message), EXIT_FAIL)
    except ValueError as exc:
        return _fail(str(exc), EXIT_STATE)
    except KeyboardInterrupt:
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
