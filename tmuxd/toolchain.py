"""``~/.tmuxd.json`` -- two keys, and never a third.

    {"tmux": "/usr/bin/tmux", "ttyd": "/home/me/.tmuxd/bin/ttyd"}

Written by ``tmuxd install``, read by everything (works/07-install.md §4).

**Why a separate file from ~/.tmuxd.conf.** That one is the CLI's config and
it is written by a *person* -- comments, blank lines, an order they chose. A
program editing it needs a marked-block scheme and a rule about never
reordering anyone's lines, and every clause of that rule is a future bug.
This file is the machine's, so it is rewritten whole and ``rm`` undoes it.

**Why the library may read it at all.** Because of what is *not* in it. Where
tmux lives is a fact about the machine and the answer is the same whoever
asks. A port is behaviour: ``Tmuxd(port=12345)`` must never land somewhere
else because of a file in someone's home directory. Keeping this file to the
two binaries is what makes reading it by default safe -- so it never grows a
third key.
"""

import json
import os

KEYS = ("tmux", "ttyd")

DEFAULT_PATH = "~/.tmuxd.json"


def path():
    """``TMUXD_JSON`` overrides, for the same reason ``TMUXD_CONFIG`` does:
    a test run must not pick up the real file on the machine running it."""
    return os.path.expanduser(os.environ.get("TMUXD_JSON") or DEFAULT_PATH)


def read():
    """The recorded paths, or ``{}``.

    Anything unreadable, malformed or unexpected reads as absent. This file is
    a cache of a past lookup, not a source of truth -- a corrupt one must cost
    a fallback, never a crash.
    """
    try:
        with open(path(), encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {k: payload[k] for k in KEYS
            if isinstance(payload.get(k), str) and payload[k]}


def write(**values):
    """Merge in ``tmux=`` / ``ttyd=`` and rewrite the file.

    Passing ``None`` for a key drops it, which is how "we could not find one
    this time" is recorded -- keeping a stale path would be worse than
    admitting there is none.
    """
    unknown = set(values) - set(KEYS)
    if unknown:
        raise ValueError(
            "~/.tmuxd.json holds the two binaries and nothing else; refusing %s. "
            "Ports and tokens are behaviour and come from the caller "
            "(works/07-install.md §5)." % ", ".join(sorted(unknown)))

    payload = read()
    for key, value in values.items():
        if value:
            payload[key] = str(value)
        else:
            payload.pop(key, None)

    target = path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (target, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, target)
    return payload


def forget():
    """Delete the file. ``rm ~/.tmuxd.json`` by hand does the same thing."""
    try:
        os.unlink(path())
        return True
    except OSError:
        return False
