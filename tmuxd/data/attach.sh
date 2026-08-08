#!/bin/sh
# ttyd -a runs this with $1 = the session id from ?arg=<id>.
#
# -a means anyone can put any string in that query parameter, so the pty
# creation point has to refuse ids that were never created through the
# library. This script never creates a session -- attach-session only.
#
# The "=" prefix turns off tmux's prefix matching. Without it, `-t work`
# resolves to `workbench` and you land in the wrong terminal.
set -eu

TMUX="${_TMUXD_TMUX:-tmux}"
SOCK="${_TMUXD_SOCK:-tmuxd}"

[ "$#" -ge 1 ] && [ -n "$1" ] || {
    echo "tmuxd: no session id given" >&2
    exit 1
}

if ! "$TMUX" -L "$SOCK" has-session -t "=$1" 2>/dev/null; then
    echo "tmuxd: unknown session '$1' -- sessions are created through the library" >&2
    exit 1
fi

exec "$TMUX" -L "$SOCK" attach-session -t "=$1"
