"""nothing_reads — 守住"不读"这条线. See README.md.

这里的用例断言的是**没有**。要加读能力,先改 works/03-server.md 的论证,
再让这里红掉 —— 顺序反过来就是在悄悄扩大这一层的职责。
"""
from __future__ import annotations

import pytest

from tests.conftest import needs_tmux
from tmuxd.core import Tmuxd
from tmuxd.session import Session

pytestmark = needs_tmux

READING = ["capture", "run", "stream", "wait_for", "resize", "split", "record",
           "output", "read", "screen"]


# -- 库 ---------------------------------------------------------------------


@pytest.mark.parametrize("name", READING)
def test_session_has_no_read_method(name):
    assert not hasattr(Session, name), \
        "Session.%s 出现了 —— 先去改 works/03-server.md 的论证" % name


@pytest.mark.parametrize("name", READING)
def test_tmuxd_has_no_read_method(name):
    assert not hasattr(Tmuxd, name)


def test_the_whole_session_surface_is_four_things_and_a_url():
    public = {n for n in dir(Session) if not n.startswith("_")}
    assert public == {
        "send", "send_key", "kill", "to_dict",                    # 方法
        "url", "status", "alive", "clients", "current_command",   # 属性
        "id", "cwd", "cmd", "created_at", "last_attached", "external",  # 字段
    }


def test_the_library_starts_no_http_server_of_its_own():
    """嵌进来的人已经有一个 app 在跑了 —— 库该给 router,不该自己起 server。"""
    assert not hasattr(Tmuxd, "serve_http")


def test_import_tmuxd_does_not_drag_in_fastapi():
    """基础安装零依赖,靠的就是这条 —— FastAPI 只在 [server] 那条链路上。"""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, tmuxd; print('fastapi' in sys.modules or 'uvicorn' in sys.modules)"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# -- 控制口 -----------------------------------------------------------------


@pytest.fixture
def app(instance):
    pytest.importorskip("fastapi", reason="control API needs tmuxd[server]")
    from tmuxd.server import create_app

    return create_app(instance)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.mark.parametrize("path", [
    "/api/sessions/x/capture",
    "/api/sessions/x/run",
    "/api/sessions/x/stream",
    "/api/sessions/x/record",
    "/api/events",
])
def test_the_control_api_has_no_read_routes(client, path):
    assert client.get(path).status_code == 404


def test_the_control_api_exposes_exactly_seven_routes(app):
    """路由表短是有意的。多一条就该有人解释为什么。

    断言走 OpenAPI 而不是 app.routes:那是对外契约(管控口本来就是给别的语言
    调的),而且不受 FastAPI 内部怎么存路由的影响 —— 0.141 就把 include_router
    进来的东西包成了 _IncludedRouter,不再摊平到 app.routes 里。
    """
    paths = app.openapi()["paths"]
    routes = {(m.upper(), path) for path, ops in paths.items() for m in ops}
    assert routes == {
        ("GET", "/api/health"),
        ("GET", "/api/info"),
        ("GET", "/api/sessions"),
        ("POST", "/api/sessions"),
        ("GET", "/api/sessions/{sid}"),
        ("DELETE", "/api/sessions/{sid}"),
        ("POST", "/api/sessions/{sid}/keys"),
    }


def test_no_websocket_route(instance):
    """ttyd 那条终端通道除外,而那条不是我们写的。"""
    from starlette.routing import WebSocketRoute

    from tmuxd.server import router

    assert not [r for r in router(instance).routes
                if isinstance(r, WebSocketRoute)]


# -- CLI --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["capture", "run", "wait", "stream", "watch", "rename"])
def test_cli_has_no_such_command(name):
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    assert name not in sub.choices


def test_cli_commands_are_exactly_these():
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    assert set(sub.choices) == {
        "serve", "start", "stop", "status", "info", "install",
        "new", "ls", "url", "kill", "has", "send", "keys", "kill-server",
    }


def test_there_is_no_remote_client():
    """远端用 ssh,或者直接 requests 打那七个端点(works/03 §13)。"""
    import tmuxd

    assert not hasattr(tmuxd, "RemoteTmuxd")
    with pytest.raises(ImportError):
        __import__("tmuxd.remote")
