# tmuxd

[![PyPI](https://img.shields.io/pypi/v/tmuxd)](https://pypi.org/project/tmuxd/)
[![Python](https://img.shields.io/pypi/pyversions/tmuxd)](https://pypi.org/project/tmuxd/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/memory-co/tmuxd/blob/main/LICENSE)

**tmux + ttyd as a Python library: terminals that outlive the connection, that a
program can type into and a person can open in a browser.**

[简体中文](https://github.com/memory-co/tmuxd/blob/main/README.md) · **English** · [Changelog](https://github.com/memory-co/tmuxd/blob/main/CHANGELOG.md)

---

Everyone has written `ttyd tmux new -A -s work` at some point. tmux keeps the
session alive, ttyd makes it visible in a browser. It works — but the result has
no handle on it. Who opened that session, in which directory, is it still alive?
Want to feed it a command from the outside? SSH in and type `tmux send-keys`.

tmuxd is that command turned into something you `import`.

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # ttyd is up; tmux is not yet
s = t.session(id="id5", cwd="~/proj", cmd="claude")
s.send("run the tests", enter=True)
print(s.url)                              # http://127.0.0.1:12345/?arg=id5
```

Send that URL to anyone and their browser is *in* that terminal — watching, and
able to take over the keyboard. **A program hands out the work; a person watches
it run.**

Inside a web backend that window needs **no port of its own and no second
password**: ttyd listens on a unix socket, `t.asgi()` mounts it under your own
routes, and your login decides who gets into which window — see
[Where the window opens](https://github.com/memory-co/tmuxd/blob/main/README.en.md#where-the-window-opens-two-doors).

## Quick start

Needs `tmux` (≥ 3.0) and `ttyd` on the machine — see [Requirements](https://github.com/memory-co/tmuxd/blob/main/README.en.md#requirements).

### As a library — no server needed

```bash
pip install tmuxd          # zero runtime dependencies
```

```python
from tmuxd import Tmuxd

with Tmuxd(port=12345, token="changeme") as t:
    s = t.session(id="deploy", cwd="/srv/app", cmd="./deploy.sh")
    print("watch it here:", s.url)
# ttyd goes with your process; the deploy is still running
```

Your process holds the instance, so there is nothing else to run.

### Inside a web backend — the window on your port, behind your login

```bash
pip install "tmuxd[asgi]"       # + websockets; your ASGI server needs WebSocket support (e.g. uvicorn[standard])
```

```python
from fastapi import FastAPI
from tmuxd import Tmuxd

app = FastAPI()
t = Tmuxd(base_path="/tty")          # no port: ttyd listens on ~/.tmuxd/tmuxd/ttyd.sock (0600)

def gate(scope):
    """Asked on every request into the window: the page, /token, /ws."""
    return my_login_allows(scope)    # your own login state, e.g. from a cookie

app.mount("/tty", t.asgi(authorize=gate))

@app.post("/api/work")
def start_work():
    s = t.session(id="job-1", cmd="claude")
    return {"window": s.url}         # "/tty/?arg=job-1" -- same-origin, drop it in an iframe
```

One port, your app's. The window and your API share one door — see the next section.

### From the command line — needs a server

```bash
pip install "tmuxd[server]"     # + fastapi + uvicorn
tmuxd start                     # both ports picked free, recorded in ~/.tmuxd/daemon.json
```

```bash
tmuxd new  -s work -c ~/proj
tmuxd send -s work "npm test" --enter
tmuxd url  -s work -o           # open it in a browser
tmuxd ls
tmuxd stop                      # stops the server; sessions keep running
```

A CLI command lives for milliseconds and can hold neither ttyd nor session
state, so it asks a server that can. That is why the CLI and the server install
together — [why](https://github.com/memory-co/tmuxd/blob/main/docs/v1/works/03-server.md).

## Where the window opens: two doors

The ttyd page — the window a person looks through — can open in one of two
places, **decided by the constructor**:

|  | ttyd on its own port (TCP) | mounted in your app (unix socket) |
| --- | --- | --- |
| Written as | `Tmuxd(port=12345, token=…)` | `Tmuxd(base_path="/tty")` + `app.mount("/tty", t.asgi(authorize=gate))` |
| ttyd listens on | `127.0.0.1:12345` (or your `bind`) | `<state_dir>/<socket>/ttyd.sock`, mode 0600 |
| `s.url` | `http://127.0.0.1:12345/?arg=id5` | `/tty/?arg=id5` (relative, same-origin) |
| Who gets in | whoever has the token (basic auth, one for everyone) | your `gate(scope)` decides — per person, per window |
| Ports to open | one more, firewall included | none beyond your app's |
| Fits | scripts, one machine, the CLI (`tmuxd start` is always this) | a web backend that already has a login |

**The rule: `port=` (or `TMUXD_PORT`, or `listen="tcp"`) means TCP; nothing at all means the socket.**

> ⚠️ **This is a breaking change.** In 2.1.0, `Tmuxd()` without a port picked a
> random free TCP port. Now it opens the socket, `s.url` is relative, and nobody
> gets in until something mounts `t.asgi()`. For the old behaviour write
> `Tmuxd(listen="tcp")`. See the [changelog](https://github.com/memory-co/tmuxd/blob/main/CHANGELOG.md).

**Why the second door exists.** With its own port, the window sits outside
your door: one more port for the firewall, and one more password — a plaintext
token shared by everyone, unrelated to your login, so holding it skips the login
entirely and neither logging out nor changing a password touches it. Mounted in
your app, the window and the API share one port and one door.

**Writing the gate.** `authorize(scope)` gets the raw ASGI scope (path, query,
headers, cookies) and may be sync or async:

| Returns | Result |
| --- | --- |
| falsy | HTTP 403; the WebSocket handshake is closed (1008) |
| `True` | allowed |
| `[(name, value), …]` | allowed, with these headers added to the response — a cookie set after checking a ticket, say |

**No `authorize` means everyone gets in.** The socket's 0600 only keeps other
users on the machine from reaching ttyd directly; once `t.asgi()` is mounted,
**anyone who can reach your app's port can open `/tty/?arg=<any id>` and have a
shell** — looser than TCP mode's basic auth. Leave it out only when the host
listens on `127.0.0.1` for yourself alone, or when something in front
(Cloudflare Access, oauth2-proxy, a VPN) already authenticates every request.
Your app's own `/api` auth middleware does **not** cover this route: an iframe
and a WebSocket cannot send an `Authorization` header.

An iframe and a browser WebSocket cannot send an `Authorization` header, so the
usual shape is **ticket for cookie**: attach a short-lived one-time ticket to the
window URL, check it on the page request and set an HttpOnly cookie with
`Path=/tty`; let `/ws` in on that cookie **and check that `?arg=` is the session
on the ticket** — one ticket, one window.

Worth knowing:

- `base_path` must match the mount prefix (`"/tty"` with `"/tty"`);
- the relay moves bytes and never parses ttyd's protocol; **ttyd's lifetime still
  belongs to `Tmuxd`** (started at construction, stopped by `close()`), and a
  gone ttyd is a 502;
- keep the host at **one worker**; behind another proxy, raise the WebSocket idle timeout;
- the window is same-origin with your page, so ttyd's frontend JS can read your
  page's localStorage. For a stricter line, serve `/tty` from its own subdomain.

Design and trade-offs: [works/08 · one door](https://github.com/memory-co/tmuxd/blob/main/docs/v1/works/08-one-door.md).

## What makes it different

**It is designed by subtraction.** What was removed says more than what is left.

- **One session is one terminal.** No windows, no panes — the multiplexing half
  of tmux is not used. Want more terminals? Open more sessions.
- **Write only, no reading.** No `capture`, no `run`, no output stream, no
  recording, no event stream. Reading a terminal belongs to a *person* (open the
  URL — ttyd already does that better than any API could) or to **ssh** (clean
  stdout, a real exit code, binary safety). What stays is the one write action
  neither of them can do.
- **The facade is short-lived, the house is not.** ttyd is a child of your
  process; the tmux server is nobody's child. `kill -9` your program and the
  sessions carry on, with their working directory and command remembered.
- **It never touches your own tmux.** Only the binary is probed, and the pool
  always opens on a dedicated `tmux -L tmuxd`. Your `tmux ls` is unchanged.
- **A person and a program type into the same terminal.** Not a feature we
  built — tmux gives it away, which is why the whole design is arranged
  around it.
- **No permission tiers.** Everything is read-write. Getting into a window
  means holding a shell on that machine, so a read-only switch here would be a
  boundary that is not really there. Lock upstairs, where identity exists —
  mounted in your app, upstairs is your `gate(scope)`.

## Two ways in

|  | Library (mounted in your app) | Library (ttyd on its own port) | CLI |
| --- | --- | --- | --- |
| Holds the instance | your process | your process | `tmuxd serve` |
| Needs a server | **no** (uses yours) | **no** | **yes** |
| Install | `pip install "tmuxd[asgi]"` | `pip install tmuxd` | `pip install "tmuxd[server]"` |
| Ports | **none** (ttyd is on a socket) | ttyd | ttyd + control API |
| The window | `app.mount("/tty", t.asgi(…))` | ttyd's port | ttyd's port |
| Programs call it | the library directly; mount `tmuxd.server.router()` to offer HTTP | same | control API (random port) |

The CLI's two ports, two audiences. **One is ttyd and it is for people** — `s.url` goes
straight to a colleague. **The other is the control API and it is for programs** —
JSON in, JSON out, seven endpoints. **Both are free ports picked at startup**: 7681
is ttyd's own default, which makes it the likeliest port for your own ttyd to be
on, and claiming it would be picking a fight. They are recorded in
`~/.tmuxd/daemon.json`, which every `tmuxd` command reads, so you never type them. Driving another machine is `ssh box tmuxd …`,
not a port on the internet.

## Requirements

| | | |
| --- | --- | --- |
| **tmux** | ≥ 3.0 | `apt install tmux` · `brew install tmux` · `dnf install tmux` |
| **ttyd** | ≥ 1.6 | **bundled in the Linux wheels** · macOS: `brew install ttyd` |
| **Python** | ≥ 3.9 | |
| **OS** | Linux, macOS | |

**On Linux, `pip install` is enough.** The wheels carry an upstream ttyd build
for their architecture (x86_64, aarch64, armv7l — glibc and musl alike, since
upstream links statically). A ttyd already on `PATH` still wins: that one can be
fixed by `apt upgrade` and ours can only be fixed by a release of tmuxd.

**On macOS you install ttyd yourself** — `brew install ttyd`. Upstream has never
shipped a Darwin build (checked back to 1.7.3: ten musl ELFs and one win32.exe,
every time), and Homebrew's is dynamically linked against five of its own
packages, so re-shipping it would do badly what brew does well. macOS gets the
`py3-none-any` wheel, which installs everywhere and simply expects ttyd on PATH.

**Windows is not supported** — tmux has no Windows build, and tmuxd imports
`fcntl`.

**Optional extras:** `tmuxd[asgi]` adds `websockets` (for the ttyd side of
`t.asgi()`); `tmuxd[server]` adds `fastapi` + `uvicorn` (for the CLI and the
control API). `import tmuxd` itself always has zero dependencies.

**If your machine is not ready** — an architecture no wheel covers, no tmux, or
you want a newer ttyd than the one we vendored — there is an optional
[`tmuxd install`](https://github.com/memory-co/tmuxd/blob/main/docs/v1/cli/install.md). It fetches a checksum-verified ttyd
from upstream (falling back to the bundled one when the network is down) and
tells you the exact command for tmux on this machine, then records both paths in
`~/.tmuxd.json` so the library and the CLI find them next time. **A ready machine
never runs it**, and `Tmuxd()` does not check whether you have.

tmuxd never adopts the tmux you use yourself: it runs its own pool on a
dedicated socket, so `tmux ls` shows exactly what it showed before.

## Development

```bash
pip install -e ".[dev]"
pytest                              # ~234 tests, ~70s
pytest tests/exact_targeting -v     # a single scenario
```

Tests run against **real tmux, real ttyd and a real uvicorn** — this project's
whole value lives at the seam with those programs, and mocking them would test
nothing. Each test gets its own tmux socket, so running the suite never disturbs
a tmux you have open. They are organised [by scenario](https://github.com/memory-co/tmuxd/blob/main/tests/README.md), not by
module.

## License

Apache-2.0 — see [LICENSE](https://github.com/memory-co/tmuxd/blob/main/LICENSE).

tmuxd drives [ttyd](https://github.com/tsl0922/ttyd) (MIT) and
[tmux](https://github.com/tmux/tmux) (ISC) as external programs; it neither
vendors nor modifies them.
