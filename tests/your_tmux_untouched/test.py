"""your_tmux_untouched — 专属会话池. See README.md."""
from __future__ import annotations

import os
import subprocess

import pytest

from tests.conftest import needs_tmux, wait_until
from tmuxd import Tmuxd

pytestmark = needs_tmux


# -- 池是专属的 -------------------------------------------------------------


def test_the_pool_has_its_own_socket(instance):
    assert instance.tmux_socket != "default"
    assert instance.tmux_socket.startswith("tmuxd-")


def test_sharing_your_own_tmux_is_refused(tmp_path):
    """那会把会话开进你正在用的那个 server —— 不给这个机会。"""
    with pytest.raises(ValueError) as exc:
        Tmuxd(port=None, socket="default", state_dir=str(tmp_path))
    assert "own" in str(exc.value)


def test_the_default_instance_still_is_not_yours(tmp_path):
    t = Tmuxd(port=None, state_dir=str(tmp_path))
    try:
        assert t.tmux_socket == "tmuxd"      # 不是 tmux 的 default socket
    finally:
        t.close()


# -- 构造完什么都还没起 -----------------------------------------------------


def test_no_tmux_server_until_the_first_session(instance):
    assert instance._tmux.server_running() is False
    assert instance.sessions() == []
    assert instance.has("anything") is False

    instance.session(id="first")
    assert instance._tmux.server_running() is True


def test_listing_before_any_server_is_empty_not_an_error(instance):
    """tmux 这时以 exit 1 报 "error connecting to ..."。

    把它当失败的话,一个刚构造的实例调 sessions() 就会炸 ——
    实现上最容易写错、也最容易被测试漏掉的一处。
    """
    probe = instance._tmux.run("list-sessions", check=False)
    assert probe.returncode != 0
    assert "error connecting" in (probe.stderr + probe.stdout).lower()

    assert instance.sessions() == []          # 库把它读成"空",不是"炸了"


# -- external:有人绕过了库 -------------------------------------------------


def test_external_sessions_are_listed_never_adopted(instance):
    instance.session(id="mine", cmd="cat")
    subprocess.run(
        [instance.tmux_bin, "-L", instance.tmux_socket, "new-session", "-d",
         "-s", "intruder"],
        check=True, capture_output=True,
    )

    by_id = {s.id: s for s in instance.sessions()}
    assert by_id["intruder"].external is True
    assert by_id["mine"].external is False

    # 不给它补状态文件 —— 那会把看得见的异常变成看不见的谎
    assert not os.path.exists(instance._store.path_for("intruder"))


def test_an_external_session_is_still_usable(instance):
    """标出来是为了说清楚来路,不是为了残废地对待它。"""
    subprocess.run(
        [instance.tmux_bin, "-L", instance.tmux_socket, "new-session", "-d",
         "-s", "intruder", "cat"],
        check=True, capture_output=True,
    )
    s = instance.get("intruder")
    assert s.alive
    s.send("still works", enter=True)
    assert s.kill() == 0


# -- 回收只删文件 -----------------------------------------------------------


def test_a_dead_session_keeps_its_record_for_a_while(instance):
    instance.session(id="short", cmd="true")
    assert wait_until(lambda: not instance.has("short"))

    listed = instance.sessions()
    assert [s.id for s in listed] == ["short"]
    assert listed[0].status == "exited"


def test_then_the_record_gets_swept(instance):
    instance.session(id="short", cmd="true")
    assert wait_until(lambda: not instance.has("short"))

    instance.gc_ttl = -1                      # 假装时间过去了
    assert instance.sessions() == []
    assert not os.path.exists(instance._store.path_for("short"))


def test_gc_never_kills_a_live_session(instance):
    """一个后台机制自作主张杀掉别人跑了三天的会话,是最不可原谅的那种 bug。"""
    instance.session(id="alive", cmd="cat")
    instance.gc_ttl = -1                      # 全都"过期"了

    assert [s.id for s in instance.sessions()] == ["alive"]
    assert instance.has("alive") is True


# -- 显式的毁灭 -------------------------------------------------------------


def test_kill_tmux_server_only_kills_its_own_pool(tmp_path, request):
    from tests.conftest import kill_pool, pool_name

    a_name = pool_name(request, prefix="a")
    b_name = pool_name(request, prefix="b")
    a = Tmuxd(port=None, socket=a_name, state_dir=str(tmp_path))
    b = Tmuxd(port=None, socket=b_name, state_dir=str(tmp_path))
    try:
        a.session(id="in-a", cmd="cat")
        b.session(id="in-b", cmd="cat")

        a.kill_tmux_server()

        assert a.sessions() == [] or all(not s.alive for s in a.sessions())
        assert b.has("in-b"), "另一个池不该受影响"
    finally:
        a.close()
        b.close()
        kill_pool(a_name)
        kill_pool(b_name)
