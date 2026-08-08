"""the_entrance — 人从哪进来. See README.md."""
from __future__ import annotations

import base64
import os
import subprocess
import urllib.error
import urllib.request

import pytest

from tests.conftest import TOKEN, needs_tmux, needs_ttyd, pool_name
from tmuxd import Tmuxd

pytestmark = [needs_tmux, needs_ttyd]


def get(url, user=None, password=None, timeout=3):
    req = urllib.request.Request(url)
    if user is not None:
        raw = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        req.add_header("Authorization", "Basic " + raw)
    return urllib.request.urlopen(req, timeout=timeout)


def _attach(t, sid):
    """按 ttyd 调用它的方式跑 attach.sh —— 同样的 argv 和环境变量。"""
    env = dict(os.environ, _TMUXD_SOCK=t.tmux_socket, _TMUXD_TMUX=t.tmux_bin)
    return subprocess.run(
        [os.path.join(t.state_dir, "attach.sh"), sid],
        capture_output=True, text=True, env=env, timeout=10,
    )


# -- 门开着,而且是真的终端页面 --------------------------------------------


def test_ttyd_serves_the_terminal_page(served):
    resp = get("http://127.0.0.1:%d/" % served.port, "tmuxd", TOKEN)
    assert resp.status == 200
    assert b"<!doctype html>" in resp.read()[:200].lower()


def test_the_url_is_ttyds_own_arg_address(served):
    s = served.session(id="id5")
    assert s.url == "http://127.0.0.1:%d/?arg=id5" % served.port


def test_the_url_opens(served):
    s = served.session(id="id5")
    assert get(s.url, "tmuxd", TOKEN).status == 200


def test_the_url_percent_encodes_the_id(served):
    assert served.session(id="a b/c").url.endswith("/?arg=a%20b%2Fc")


def test_the_url_needs_no_live_python(tmp_path, request):
    """地址只是个字符串,算它不需要门开着 —— CLI 的读命令就靠这条。"""
    name = pool_name(request, prefix="url")
    t = Tmuxd(port=12345, socket=name, state_dir=str(tmp_path), start_ttyd=False)
    try:
        assert t._ttyd is None
        assert t.url_for("whatever") == "http://127.0.0.1:12345/?arg=whatever"
    finally:
        t.close()


# -- 谁进得来 ---------------------------------------------------------------


def test_the_token_is_enforced_by_ttyd(served):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get("http://127.0.0.1:%d/" % served.port)
    assert exc.value.code == 401


def test_binding_outward_without_a_token_is_refused(tmp_path):
    """把一台机器的 shell 放到网上,不给"我待会再加"的机会。"""
    with pytest.raises(ValueError) as exc:
        Tmuxd(port=1, bind="0.0.0.0", state_dir=str(tmp_path))
    assert "token" in str(exc.value)


# -- pty 创建点:会话只能由库创建 -------------------------------------------


def test_attach_refuses_an_id_that_was_never_created(served):
    """?arg= 是调用方随便填的,所以这道闸必须在 pty 那里再挡一次。"""
    out = _attach(served, "rogue")
    assert out.returncode == 1
    assert "unknown session" in out.stderr


def test_attach_refuses_an_empty_id(served):
    assert _attach(served, "").returncode == 1


def test_attach_never_creates(served):
    _attach(served, "conjured")
    assert served.has("conjured") is False


def test_attach_lets_a_real_session_through(served):
    """挡的是没建过的,不是所有的 —— 建过的必须能进。"""
    served.session(id="real", cmd="cat")
    probe = served._tmux.run("has-session", "-t", "=real", check=False)
    assert probe.returncode == 0
