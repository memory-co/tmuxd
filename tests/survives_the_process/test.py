"""survives_the_process — 门面短命、屋子长命. See README.md."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import (
    free_port,
    kill_pool,
    needs_tmux,
    needs_ttyd,
    pool_name,
    wait_until,
)
from tmuxd import Tmuxd
from tmuxd.ttyd import pid_alive, port_open

pytestmark = needs_tmux

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_the_session_outlives_the_object_that_made_it(instance, tmp_path):
    instance.session(id="survivor", cmd="cat")
    name = instance.socket_name

    instance.close()                      # 门面收摊
    revived = Tmuxd(port=free_port(), socket=name, state_dir=str(tmp_path))
    try:
        assert revived.has("survivor")
    finally:
        revived.close()


def test_and_it_still_remembers_where_it_started(instance, tmp_path):
    """tmux 只知道"有个叫 x 的会话活着";cwd 和 cmd 是状态文件记的那部分。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    instance.session(id="survivor", cwd=str(proj), cmd="cat")
    name = instance.socket_name

    revived = Tmuxd(port=free_port(), socket=name, state_dir=str(tmp_path))
    try:
        s = revived.get("survivor")
        assert (s.cwd, s.cmd, s.status) == (str(proj), "cat", "alive")
    finally:
        revived.close()


@needs_ttyd
def test_ttyd_dies_with_the_process_that_started_it(tmp_path):
    """必须用 SIGKILL 验。

    绑生死是 PR_SET_PDEATHSIG 干的,不是 finally —— SIGKILL 之下没有一行 Python
    会执行,所以拿 terminate() 来验等于什么都没验。
    """
    port = free_port()
    name = "pdeath-%d" % os.getpid()
    code = (
        "import time, tmuxd;"
        "t = tmuxd.Tmuxd(port=%d, socket=%r, state_dir=%r);"
        "print(t._ttyd.pid, flush=True);"
        "time.sleep(60)" % (port, name, str(tmp_path))
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, cwd=REPO
    )
    try:
        ttyd_pid = int(child.stdout.readline().strip())
        assert port_open("127.0.0.1", port)

        child.kill()                                   # 最狠的那种退出
        child.wait(timeout=5)

        assert wait_until(lambda: not pid_alive(ttyd_pid)), \
            "ttyd 活过了拥有它的那个进程"
    finally:
        child.kill()
        kill_pool(name)


@needs_ttyd
def test_but_the_sessions_do_not(tmp_path, request):
    """同一次退出里,会话必须不受影响。"""
    port = free_port()
    name = pool_name(request, prefix="keep")
    code = (
        "import time, tmuxd;"
        "t = tmuxd.Tmuxd(port=%d, socket=%r, state_dir=%r);"
        "t.session(id='held', cmd='cat');"
        "print(t._ttyd.pid, flush=True);"
        "time.sleep(60)" % (port, name, str(tmp_path))
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, cwd=REPO
    )
    after = None
    try:
        ttyd_pid = int(child.stdout.readline().strip())
        child.kill()
        child.wait(timeout=5)
        assert wait_until(lambda: not pid_alive(ttyd_pid))

        after = Tmuxd(port=free_port(), socket=name, state_dir=str(tmp_path))
        assert after.has("held"), "门面走了,屋里的人不该跟着走"
    finally:
        child.kill()
        if after is not None:
            after.close()
        kill_pool(name)


def test_close_takes_the_facade_not_the_room(instance):
    instance.session(id="keep", cmd="cat")
    instance.close()
    assert instance.has("keep")


@needs_ttyd
def test_the_context_manager_is_the_same_promise(tmp_path, request):
    name = pool_name(request, prefix="ctx")
    port = free_port()
    try:
        with Tmuxd(port=port, socket=name, state_dir=str(tmp_path)) as t:
            t.session(id="job-1", cmd="cat")
            ttyd_pid = t._ttyd.pid
        assert wait_until(lambda: not pid_alive(ttyd_pid))     # 门关了

        after = Tmuxd(port=free_port(), socket=name, state_dir=str(tmp_path))
        try:
            assert after.has("job-1")                          # 人还在
        finally:
            after.close()
    finally:
        kill_pool(name)


@needs_ttyd
def test_sessions_survive_ttyd_going_away(served):
    served.session(id="tough", cmd="cat")
    served._ttyd.stop()
    assert served.has("tough")


def test_only_kill_destroys(instance):
    s = instance.session(id="doomed", cmd="cat")
    assert instance.has("doomed")

    assert s.kill() == 0                  # 没人 attach,踢掉 0 个
    assert not instance.has("doomed")


def test_killing_an_already_dead_session_is_quiet(instance):
    s = instance.session(id="twice", cmd="cat")
    s.kill()
    assert s.kill() == 0                  # 不抛,也不假装杀了什么
