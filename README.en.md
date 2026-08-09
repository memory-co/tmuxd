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

### From the command line — needs a server

```bash
pip install "tmuxd[server]"     # + fastapi + uvicorn
tmuxd start                     # ttyd on :7681, control API on :7682
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
- **No permission tiers.** Everything is read-write. Holding the token means
  holding a shell on that machine, so a read-only switch here would be a
  boundary that is not really there. Lock upstairs, where identity exists.

## Two ways in

|  | Library | CLI |
| --- | --- | --- |
| Holds the instance | your process | `tmuxd serve` |
| Needs a server | **no** | **yes** |
| Install | `pip install tmuxd` | `pip install "tmuxd[server]"` |
| Ports | ttyd only | ttyd + control API |
| Exposing it | mount `tmuxd.server.router()` in the app you already run | control API on `:7682` |

Two ports, two audiences. **`:7681` is ttyd and it is for people** — `s.url` goes
straight to a colleague. **`:7682` is the control API and it is for programs** —
JSON in, JSON out, seven endpoints. Driving another machine is `ssh box tmuxd …`,
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
pytest                              # ~198 tests, ~50s
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
