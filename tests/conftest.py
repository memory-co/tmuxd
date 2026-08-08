import os
import shutil
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmuxd import Tmuxd  # noqa: E402
from tmuxd.core import DEFAULT_SOCKET  # noqa: E402

HAVE_TMUX = shutil.which("tmux") is not None
HAVE_TTYD = shutil.which("ttyd") is not None

needs_tmux = pytest.mark.skipif(not HAVE_TMUX, reason="tmux not installed")
needs_ttyd = pytest.mark.skipif(not HAVE_TTYD, reason="ttyd not installed")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_pool(socket_name):
    tmux_socket = socket_name if socket_name == DEFAULT_SOCKET else "%s-%s" % (
        DEFAULT_SOCKET, socket_name)
    subprocess.run(
        [shutil.which("tmux") or "tmux", "-L", tmux_socket, "kill-server"],
        capture_output=True,
    )


@pytest.fixture
def instance(tmp_path, request):
    """A Tmuxd on its own tmux socket and state dir, with no ttyd."""
    name = "test-%s-%d" % (request.node.name.replace("_", "-")[:24], os.getpid())
    t = Tmuxd(port=None, socket=name, state_dir=str(tmp_path), workspace=str(tmp_path))
    yield t
    t.close()
    _kill_pool(name)


@pytest.fixture
def served(tmp_path, request):
    """A Tmuxd that really starts ttyd on a free port."""
    if not HAVE_TTYD:
        pytest.skip("ttyd not installed")
    name = "serve-%s-%d" % (request.node.name.replace("_", "-")[:20], os.getpid())
    t = Tmuxd(
        port=free_port(),
        socket=name,
        token="t0ken",
        state_dir=str(tmp_path),
        workspace=str(tmp_path),
    )
    yield t
    t.close()
    _kill_pool(name)
