import base64
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from conftest import free_port, needs_tmux, needs_ttyd, _kill_pool
from tmuxd import PortInUse, Tmuxd

pytestmark = [needs_tmux, needs_ttyd]


def get(url, user=None, password=None, timeout=3):
    req = urllib.request.Request(url)
    if user is not None:
        raw = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        req.add_header("Authorization", "Basic " + raw)
    return urllib.request.urlopen(req, timeout=timeout)


# -- the entrance ---------------------------------------------------------


def test_ttyd_is_up_and_serves_the_terminal_page(served):
    resp = get("http://127.0.0.1:%d/" % served.port, "tmuxd", "t0ken")
    assert resp.status == 200
    assert b"<!doctype html>" in resp.read()[:200].lower()


def test_the_token_is_enforced_by_ttyd(served):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get("http://127.0.0.1:%d/" % served.port)
    assert exc.value.code == 401


def test_url_is_ttyds_own_arg_address(served):
    s = served.session(id="id5")
    assert s.url == "http://127.0.0.1:%d/?arg=id5" % served.port


def test_url_percent_encodes_the_id(served):
    s = served.session(id="a b/c")
    assert s.url.endswith("/?arg=a%20b%2Fc")


# -- attach.sh: the pty creation guard ------------------------------------


def _attach(t, sid):
    env = dict(os.environ, _TMUXD_SOCK=t.tmux_socket, _TMUXD_TMUX=t.tmux_bin)
    return subprocess.run(
        [os.path.join(t.state_dir, "attach.sh"), sid],
        capture_output=True, text=True, env=env, timeout=10,
    )


def test_attach_refuses_an_id_that_was_never_created(served):
    """?arg= is caller-controlled, so the guard has to hold at the pty."""
    out = _attach(served, "rogue")
    assert out.returncode == 1
    assert "unknown session" in out.stderr


def test_attach_refuses_an_empty_id(served):
    assert _attach(served, "").returncode == 1


def test_attach_never_creates(served):
    _attach(served, "conjured")
    assert served.has("conjured") is False


def test_attach_does_not_prefix_match(served):
    served.session(id="workbench", cmd="cat")
    out = _attach(served, "work")
    assert out.returncode == 1
    assert "unknown session" in out.stderr


# -- process ownership ----------------------------------------------------


def test_ttyd_dies_with_the_process_that_started_it(tmp_path):
    """PR_SET_PDEATHSIG, not a finally block -- SIGKILL runs no Python."""
    port = free_port()
    name = "pdeath-%d" % os.getpid()
    code = (
        "import time, tmuxd;"
        "t = tmuxd.Tmuxd(port=%d, socket=%r, state_dir=%r);"
        "print(t._ttyd.pid, flush=True);"
        "time.sleep(60)" % (port, name, str(tmp_path))
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        ttyd_pid = int(child.stdout.readline().strip())
        assert _port_open(port)

        child.kill()          # the harshest exit there is
        child.wait(timeout=5)

        deadline = time.time() + 5
        while time.time() < deadline and _alive(ttyd_pid):
            time.sleep(0.05)
        assert not _alive(ttyd_pid), "ttyd outlived the process that owned it"
    finally:
        child.kill()
        _kill_pool(name)


def test_a_second_instance_adopts_the_running_ttyd(tmp_path):
    """A web backend restarting its workers must not fight over the port or
    kick connected browsers off."""
    port = free_port()
    name = "adopt-%d" % os.getpid()
    first = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    try:
        assert first._ttyd.owned is True
        second = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
        assert second._ttyd.owned is False
        assert second._ttyd.pid == first._ttyd.pid

        second.close()                       # not ours, so it stays up
        assert _port_open(port)
        assert first._ttyd.alive()
    finally:
        first.close()
        _kill_pool(name)
    assert not _port_open(port)               # the owner leaving does close it


def test_a_stranger_on_the_port_is_an_error_not_a_guess(tmp_path):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    name = "stranger-%d" % os.getpid()
    try:
        with pytest.raises(PortInUse):
            Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    finally:
        listener.close()
        _kill_pool(name)


def test_sessions_survive_ttyd_going_away(served):
    served.session(id="tough", cmd="cat")
    served._ttyd.stop()
    assert served.has("tough") is True


# -- info ------------------------------------------------------------------


def test_info_reports_the_door_without_opening_one(served, tmp_path):
    reader = Tmuxd(port=served.port, socket=served.socket_name,
                   state_dir=str(tmp_path), start_ttyd=False)
    try:
        report = reader.info()["ttyd"]
        assert report["listening"] is True
        assert report["owned"] is False
        assert reader._ttyd is None
    finally:
        reader.close()


def _port_open(port):
    from tmuxd.ttyd import port_open

    return port_open("127.0.0.1", port)


def _alive(pid):
    from tmuxd.ttyd import pid_alive

    return pid_alive(pid)
