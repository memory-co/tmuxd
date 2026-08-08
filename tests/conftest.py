"""各场景共用的 fixture 与 helper。

- ``instance`` (fixture) —— 一个 ``Tmuxd``:自己的 tmux socket、状态目录,
  以及一个真的 ttyd(``tmuxd = tmux + ttyd``,没有"只要一半"的模式)
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


@pytest.fixture(autouse=True)
def isolated_toolchain_file(tmp_path_factory, monkeypatch):
    """没有 ``~/.tmuxd.json``,除非用例自己写一个。

    这个文件的作用就是**改变二进制的查找结果**,所以跑测试的人机器上那份
    要是漏进来,查找顺序的用例会莫名其妙地过或者不过。指到一个不存在的路径
    上 —— 也正好顺手测了"没有这个文件时行为不变"这个默认情形。
    """
    monkeypatch.setenv(
        "TMUXD_JSON", str(tmp_path_factory.mktemp("toolchain") / "absent.json"))


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


def _make(tmp_path, request, prefix="t"):
    if not HAVE_TTYD:
        pytest.skip("ttyd not installed")
    name = pool_name(request, prefix=prefix)
    t = Tmuxd(port=free_port(), socket=name, token=TOKEN,
              state_dir=str(tmp_path), workspace=str(tmp_path))
    return name, t


@pytest.fixture
def instance(tmp_path, request):
    """一个完整的实例。

    没有"不起 ttyd"的选项 —— tmuxd = tmux + ttyd,缺一个都不成立
    (works/01-library.md §2)。所以每个场景拿到的都是真家伙。
    """
    name, t = _make(tmp_path, request)
    yield t
    t.close()
    kill_pool(name)


@pytest.fixture
def served(tmp_path, request):
    """``instance`` 的别名 —— 现在两者没有区别了,留着少改一堆用例。"""
    name, t = _make(tmp_path, request, prefix="s")
    yield t
    t.close()
    kill_pool(name)
