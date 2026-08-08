"""http_shell — 可选的 HTTP 壳. See README.md."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests.conftest import free_port, needs_tmux, screen, wait_for
from tmuxd import BadId, NoSuchSession, Unauthorized, Unreachable
from tmuxd.remote import RemoteTmuxd

pytestmark = needs_tmux

TOKEN = "s3cret"


@pytest.fixture
def api(instance):
    port = free_port()
    shell = instance.serve_http(port, token=TOKEN)
    yield instance, "http://127.0.0.1:%d" % port
    shell.stop()


def call(base, method, path, body=None, token=TOKEN, headers=None):
    """裸 urllib —— 验壳的时候不该用另一层壳当放大镜。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


# -- 默认不开 ---------------------------------------------------------------


def test_http_is_off_until_you_ask(instance):
    assert instance._http is None


# -- 鉴权 -------------------------------------------------------------------


def test_health_needs_no_token(api):
    _, base = api
    assert call(base, "GET", "/api/health", token=None) == (200, {"ok": True})


def test_everything_else_needs_one(api):
    _, base = api
    status, body = call(base, "GET", "/api/sessions", token=None)
    assert (status, body["error"]) == (401, "unauthorized")


# -- 八个端点 ---------------------------------------------------------------


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

    from tests.conftest import wait_until

    assert wait_until(lambda: not t.has("k"))


def test_rename_endpoint(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "a", "cmd": "cat"})
    status, body = call(base, "POST", "/api/sessions/a/rename", {"id": "b"})
    assert (status, body["id"]) == (200, "b")
    assert t.has("b")


def test_info_endpoint(api):
    _, base = api
    status, info = call(base, "GET", "/api/info")
    assert status == 200
    assert info["tmux"]["version"]
    assert set(info) >= {"version", "socket", "tmux", "sessions"}


# -- 两个口,两拨用户 -------------------------------------------------------


def test_the_url_points_at_ttyd_not_at_this_port(api):
    """API 答程序,URL 给人 —— 实现里最容易搞混的一处。"""
    t, base = api
    t.port = 12345                            # 假装配了 ttyd
    _, created = call(base, "POST", "/api/sessions", {"id": "u1"})

    api_port = base.rsplit(":", 1)[1]
    assert created["url"] == "http://127.0.0.1:12345/?arg=u1"
    assert api_port not in created["url"]


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
    status, body = call(base, "GET", "/api/nothing")
    assert (status, body["error"]) == (404, "not_found")


def test_broken_json_is_400(api):
    _, base = api
    req = urllib.request.Request(base + "/api/sessions", data=b"{oops", method="POST")
    req.add_header("Authorization", "Bearer " + TOKEN)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


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

    from tests.conftest import wait_until

    assert wait_until(lambda: screen(t, "idem2").count("Y") == 2)


# -- RemoteTmuxd:同一批异常 ------------------------------------------------


def test_remote_round_trip(api):
    t, base = api
    remote = RemoteTmuxd(base, token=TOKEN)

    s = remote.session(id="r1", cwd=t.workspace, cmd="cat")
    assert (s.id, s.alive) == ("r1", True)

    s.send("remote hello", enter=True)
    assert wait_for(t, "r1", "remote hello")

    assert [x.id for x in remote.sessions()] == ["r1"]
    assert remote.has("r1") is True
    assert remote.info()["tmux"]["version"]

    s.kill()
    assert remote.has("r1") is False


def test_remote_errors_arrive_as_the_same_exception_classes(api):
    """本地和远程可以用同一个 except 接住 —— 这层壳最值钱的一条。"""
    _, base = api
    remote = RemoteTmuxd(base, token=TOKEN)

    with pytest.raises(NoSuchSession):
        remote.get("absent")
    with pytest.raises(BadId):
        remote.session(id="bad:id")


def test_remote_url_comes_from_the_other_side(api):
    t, base = api
    t.port = 23456
    assert RemoteTmuxd(base, token=TOKEN).session(id="ru").url \
        == "http://127.0.0.1:23456/?arg=ru"


def test_remote_bad_token(api):
    _, base = api
    with pytest.raises(Unauthorized):
        RemoteTmuxd(base, token="wrong").sessions()


def test_remote_unreachable_is_its_own_error():
    remote = RemoteTmuxd("http://127.0.0.1:%d" % free_port(), token=TOKEN)
    with pytest.raises(Unreachable):
        remote.sessions()
