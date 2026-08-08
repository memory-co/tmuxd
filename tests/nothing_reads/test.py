"""nothing_reads — 守住"不读"这条线. See README.md.

这里的用例断言的是**没有**。要加读能力,先改 works/03-http.md 的论证,
再让这里红掉 —— 顺序反过来就是在悄悄扩大这一层的职责。
"""
from __future__ import annotations

import pytest

from tests.conftest import free_port, needs_tmux
from tmuxd.remote import RemoteSession
from tmuxd.session import Session

pytestmark = needs_tmux

READING = ["capture", "run", "stream", "wait_for", "resize", "split", "record",
           "output", "read", "screen"]


# -- 库 ---------------------------------------------------------------------


@pytest.mark.parametrize("name", READING)
def test_session_has_no_read_method(name):
    assert not hasattr(Session, name), \
        "Session.%s 出现了 —— 先去改 works/03-http.md 的论证" % name


@pytest.mark.parametrize("name", READING)
def test_tmuxd_has_no_read_method(name):
    from tmuxd import Tmuxd

    assert not hasattr(Tmuxd, name)


def test_the_whole_session_surface_is_five_things_and_a_url():
    public = {n for n in dir(Session) if not n.startswith("_")}
    assert public == {
        "send", "send_key", "rename", "kill", "to_dict",   # 方法
        "url", "status", "alive", "clients", "current_command",  # 属性
        "id", "cwd", "cmd", "created_at", "last_attached", "external",  # 字段
    }


def test_remote_adds_no_reading_either(instance):
    """远程那头也不会多出读的能力 —— 两边接口必须一模一样。"""
    public = lambda c: {n for n in dir(c) if not n.startswith("_")}
    assert public(Session) == public(RemoteSession)


# -- HTTP 壳 ----------------------------------------------------------------


@pytest.fixture
def api(instance):
    shell = instance.serve_http(free_port(), token="tok")
    yield instance, shell
    shell.stop()


@pytest.mark.parametrize("path", [
    "/api/sessions/x/capture",
    "/api/sessions/x/run",
    "/api/sessions/x/stream",
    "/api/sessions/x/record",
    "/api/events",
])
def test_http_has_no_read_routes(api, path):
    from tmuxd.http import _NotFound

    _, shell = api
    headers = {"Authorization": "Bearer tok"}
    with pytest.raises(_NotFound):
        shell.dispatch("GET", path, {}, headers)


def test_http_exposes_exactly_eight_routes(api):
    """路由表短是有意的。多一条就该有人解释为什么。"""
    from tmuxd.http import _ACTION_RE, _SESSION_RE

    _, shell = api
    headers = {"Authorization": "Bearer tok"}

    ok = 0
    for method, path in [
        ("GET", "/api/health"), ("GET", "/api/info"),
        ("GET", "/api/sessions"), ("POST", "/api/sessions"),
    ]:
        body = {"id": "probe"} if method == "POST" else {}
        shell.dispatch(method, path, body, headers)
        ok += 1
    # 带 id 的四条:GET / DELETE / keys / rename
    assert _SESSION_RE.match("/api/sessions/probe")
    assert _ACTION_RE.match("/api/sessions/probe/keys")
    assert _ACTION_RE.match("/api/sessions/probe/rename")
    assert ok == 4


def test_the_handler_speaks_only_three_verbs(api):
    """没有 WS 就没有升级握手要处理 —— 结构上锁,不按文本 grep。

    (源码里出现 "websocket" 这个词是正当的:文档字符串正在解释为什么没有。)
    """
    from tmuxd.http import _make_handler

    _, shell = api
    handler = _make_handler(shell)
    verbs = {n for n in vars(handler) if n.startswith("do_")}
    assert verbs == {"do_GET", "do_POST", "do_DELETE"}
    assert not any(n.lower().startswith("do_upgrade") for n in dir(handler))


# -- CLI --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["capture", "run", "wait", "stream", "watch"])
def test_cli_has_no_read_command(name):
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if hasattr(a, "choices") and a.choices]
    commands = set(sub[0].choices) if sub else set()
    assert name not in commands


def test_cli_commands_are_exactly_these():
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if hasattr(a, "choices") and a.choices]
    assert set(sub[0].choices) == {
        "serve", "start", "stop", "status", "info",
        "new", "ls", "url", "kill", "rename", "has",
        "send", "keys", "kill-server",
    }
