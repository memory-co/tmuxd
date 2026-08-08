"""control_api — CLI 打的那个口. See README.md."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests.conftest import free_port, needs_tmux, screen, wait_for, wait_until

pytestmark = needs_tmux

fastapi = pytest.importorskip("fastapi", reason="control API needs tmuxd[server]")
TOKEN = "s3cret"


@pytest.fixture
def api(instance):
    """把 router 挂进一个真的 uvicorn 里 —— 这就是 `tmuxd serve` 干的事。"""
    import threading

    import uvicorn

    from tmuxd.server import create_app

    port = free_port()
    app = create_app(instance, token=TOKEN, control_port=port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = "http://127.0.0.1:%d" % port
    assert wait_until(lambda: _reachable(base), timeout=10), "control API 没起来"
    yield instance, base

    server.should_exit = True
    thread.join(timeout=10)


def _reachable(base):
    try:
        urllib.request.urlopen(base + "/api/health", timeout=1)
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False
    return True


def call(base, method, path, body=None, token=TOKEN, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


# -- 鉴权 -------------------------------------------------------------------


def test_health_needs_no_token(api):
    _, base = api
    assert call(base, "GET", "/api/health", token=None) == (200, {"ok": True})


def test_everything_else_needs_one(api):
    _, base = api
    status, body = call(base, "GET", "/api/sessions", token=None)
    assert (status, body["error"]) == (401, "unauthorized")


# -- 七个端点 ---------------------------------------------------------------


def test_create_list_get_delete(api):
    t, base = api

    status, created = call(base, "POST", "/api/sessions",
                           {"id": "id5", "cwd": t.workspace, "cmd": "cat"})
    assert status == 201
    assert (created["id"], created["cmd"], created["status"]) == ("id5", "cat", "alive")

    status, listing = call(base, "GET", "/api/sessions")
    assert (status, [s["id"] for s in listing["sessions"]]) == (200, ["id5"])

    status, one = call(base, "GET", "/api/sessions/id5")
    assert (status, one["id"]) == (200, "id5")

    status, gone = call(base, "DELETE", "/api/sessions/id5")
    assert (status, gone["killed"]) == (200, True)
    assert t.has("id5") is False


def test_post_is_get_or_create_here_too(api):
    """换了层壳不该换语义。"""
    _, base = api
    call(base, "POST", "/api/sessions", {"id": "twice", "cmd": "cat"})
    status, again = call(base, "POST", "/api/sessions", {"id": "twice", "cmd": "sh"})
    assert (status, again["cmd"]) == (201, "cat")


def test_keys_endpoint_types_into_the_session(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "w", "cmd": "cat"})

    assert call(base, "POST", "/api/sessions/w/keys",
                {"text": "hello", "enter": True}) == (200, {"ok": True})
    assert wait_for(t, "w", "hello")


def test_keys_endpoint_takes_key_names_too(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "k", "cmd": "cat"})
    call(base, "POST", "/api/sessions/k/keys", {"keys": ["C-c"]})

    assert wait_until(lambda: not t.has("k"))


def test_info_reports_both_ports(api):
    """两个口,两拨用户 —— info 得把两个都说出来。"""
    t, base = api
    status, info = call(base, "GET", "/api/info")
    assert status == 200
    assert info["ttyd"]["port"] == t.port
    assert info["control"]["port"] == int(base.rsplit(":", 1)[1])
    assert info["ttyd"]["port"] != info["control"]["port"]


# -- 两个口,两拨用户 -------------------------------------------------------


def test_the_url_points_at_ttyd_not_at_the_control_port(api):
    """API 答程序,URL 给人 —— 实现里最容易搞混的一处。"""
    t, base = api
    _, created = call(base, "POST", "/api/sessions", {"id": "u1"})

    control_port = base.rsplit(":", 1)[1]
    assert created["url"] == "http://127.0.0.1:%d/?arg=u1" % t.port
    assert control_port not in created["url"]


# -- 错误是库异常的投影 -----------------------------------------------------


def test_missing_session_is_404_with_the_library_code(api):
    _, base = api
    status, body = call(base, "GET", "/api/sessions/nope")
    assert (status, body["error"]) == (404, "no_such_session")


def test_bad_id_is_400_with_the_library_code(api):
    _, base = api
    status, body = call(base, "POST", "/api/sessions", {"id": "a:b"})
    assert (status, body["error"]) == (400, "bad_id")


def test_unknown_route_is_404(api):
    _, base = api
    status, _ = call(base, "GET", "/api/nothing")
    assert status == 404


def test_there_is_no_rename_endpoint(api):
    """id 是身份不是标签(works/02 §6.1)。

    顺带锁住路由的形状:用 {sid:path} 的话这条路径会被当成 id 为 "a/rename"
    的会话,于是返回 405 而不是 404 —— 看着像"方法不对",其实是路由吃错了。
    """
    _, base = api
    call(base, "POST", "/api/sessions", {"id": "a", "cmd": "cat"})
    status, _ = call(base, "POST", "/api/sessions/a/rename", {"id": "b"})
    assert status == 404


def test_a_slash_in_an_id_is_refused_rather_than_half_working(api):
    """斜杠在路径段里活不下来 —— ASGI 会把 %2F 解码回 "/" 再路由。

    所以它和 `.` `:` 一样直接拒掉。让它建得出来却取不回来,是最坏的那种"支持"。
    """
    _, base = api
    status, body = call(base, "POST", "/api/sessions", {"id": "team/proj"})
    assert (status, body["error"]) == (400, "bad_id")


# -- 幂等 -------------------------------------------------------------------


def test_replaying_a_key_does_not_type_twice(api):
    """网络重试敲两遍 terraform apply 是真实事故。断言的是屏幕,不是响应。"""
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "idem", "cmd": "cat"})

    headers = {"Idempotency-Key": "abc-123"}
    for _ in range(3):
        call(base, "POST", "/api/sessions/idem/keys", {"text": "X"}, headers=headers)

    assert wait_for(t, "idem", "X")
    assert screen(t, "idem").count("X") == 1


def test_a_different_key_is_a_different_action(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "idem2", "cmd": "cat"})

    for n in range(2):
        call(base, "POST", "/api/sessions/idem2/keys", {"text": "Y"},
             headers={"Idempotency-Key": "key-%d" % n})

    assert wait_until(lambda: screen(t, "idem2").count("Y") == 2)


def test_replaying_a_create_returns_the_same_body(api):
    _, base = api
    headers = {"Idempotency-Key": "make-once"}
    first = call(base, "POST", "/api/sessions", {"id": "once", "cmd": "cat"},
                 headers=headers)
    second = call(base, "POST", "/api/sessions", {"id": "once", "cmd": "cat"},
                  headers=headers)
    assert first == second


# -- 挂进别人的应用 ---------------------------------------------------------


def test_the_router_mounts_under_a_prefix(instance):
    """链路 ① 的人不起 server —— 他们把 router 挂进自己那个 app。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tmuxd.server import router

    app = FastAPI()
    app.include_router(router(instance), prefix="/tmuxd")
    client = TestClient(app)

    assert client.get("/tmuxd/api/health").json() == {"ok": True}
    created = client.post("/tmuxd/api/sessions", json={"id": "mounted", "cmd": "cat"})
    assert created.status_code == 201
    assert instance.has("mounted")
