"""各场景共用的 fixture 与 helper。

- ``instance`` (fixture) —— 一个 ``Tmuxd``,自己的 tmux socket 和状态目录,**不起 ttyd**
- ``served`` (fixture) —— 同上,但真的在一个空闲端口上起 ttyd
- ``screen(t, sid)`` —— 读屏幕。**只有测试能这么干**,库本身刻意不提供(works/03-server.md §7)
- ``wait_for(t, sid, needle)`` —— 等到屏幕上出现某段文字
- ``free_port()`` / ``kill_pool(name)`` —— 挑端口 / 收尾时把整个池干掉

每个场景拿到的都是**独立的 tmux socket**(名字带用例名和 pid),所以并行跑、
或者跑测试的人自己开着 tmux,都互不干扰 —— 这本来就是 tmuxd 对真实用户的承诺,
测试自己先守住。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess

import pytest

from tmuxd import Tmuxd
from tmuxd.core import DEFAULT_SOCKET

HAVE_TMUX = shutil.which("tmux") is not None
HAVE_TTYD = shutil.which("ttyd") is not None

needs_tmux = pytest.mark.skipif(not HAVE_TMUX, reason="tmux not installed")
needs_ttyd = pytest.mark.skipif(not HAVE_TTYD, reason="ttyd not installed")

TOKEN = "test-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pool_name(request, prefix="t") -> str:
    """每个用例一个池名,好让失败的用例不污染下一个。"""
    node = request.node.name.replace("_", "-").replace("[", "-").replace("]", "")
    return "%s-%s-%d" % (prefix, node[:28], os.getpid())


def kill_pool(socket_name: str) -> None:
    """把这个实例背后的 tmux server 整个干掉,收尾用。"""
    tmux_socket = socket_name if socket_name == DEFAULT_SOCKET else "%s-%s" % (
        DEFAULT_SOCKET, socket_name)
    subprocess.run(
        [shutil.which("tmux") or "tmux", "-L", tmux_socket, "kill-server"],
        capture_output=True,
    )


def screen(t: Tmuxd, sid: str) -> str:
    """屏幕上现在是什么。

    库**没有**这个能力,而且是故意的:读终端要么归人(打开那个 URL),要么归 ssh。
    测试要验证"字符确实进去了",只好自己下到 tmux 那一层 —— 这也正好说明了
    为什么这条线画在这里:测试是唯一真正需要读屏幕的调用方。
    """
    out = t._tmux.run("capture-pane", "-t", "=%s:" % sid, "-p", "-J", check=False)
    return out.stdout


def wait_for(t: Tmuxd, sid: str, needle: str, timeout: float = 5.0) -> bool:
    """等屏幕上出现 needle。终端是异步的,断言前必须等。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in screen(t, sid):
            return True
        time.sleep(0.05)
    return False


def wait_until(predicate, timeout: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def instance(tmp_path, request):
    """一个不起 ttyd 的实例 —— 大多数场景不需要网页那半。"""
    name = pool_name(request)
    t = Tmuxd(port=None, socket=name, state_dir=str(tmp_path), workspace=str(tmp_path))
    yield t
    t.close()
    kill_pool(name)


@pytest.fixture
def served(tmp_path, request):
    """真的起 ttyd 的实例,给需要走网页入口的场景。"""
    if not HAVE_TTYD:
        pytest.skip("ttyd not installed")
    name = pool_name(request, prefix="s")
    t = Tmuxd(
        port=free_port(),
        socket=name,
        token=TOKEN,
        state_dir=str(tmp_path),
        workspace=str(tmp_path),
    )
    yield t
    t.close()
    kill_pool(name)
