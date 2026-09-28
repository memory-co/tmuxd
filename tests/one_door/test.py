"""one_door — ttyd 走 unix socket,窗从宿主的端口、宿主的门进来. See README.md."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat

import pytest

from tests.conftest import (
    free_port,
    kill_pool,
    needs_tmux,
    needs_ttyd,
    pool_name,
    wait_for,
    wait_until,
)
from tmuxd import PortInUse, Tmuxd
from tmuxd.ttyd import socket_open

pytestmark = [needs_tmux, needs_ttyd]

pytest.importorskip("websockets", reason="t.asgi() needs tmuxd[asgi]")
pytest.importorskip("starlette", reason="the host app in these tests is Starlette")


@pytest.fixture
def root(tmp_path_factory):
    """短路径。sun_path 只有 108 字节,pytest 的 tmp_path 加池名会超。"""
    return str(tmp_path_factory.mktemp("d"))


@pytest.fixture
def pool(request):
    name = pool_name(request, prefix="u")
    yield name
    kill_pool(name)


@pytest.fixture
def door(root, pool):
    t = Tmuxd(socket=pool, state_dir=root, workspace=root, base_path="/tty")
    yield t
    t.close()


def host(t, authorize=None, at="/tty"):
    """宿主 app:它自己的路由,外加挂上去的那扇窗。"""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route
    from starlette.testclient import TestClient

    app = Starlette(routes=[
        Route("/api/hello", lambda request: PlainTextResponse("host")),
        Mount(at, app=t.asgi(authorize=authorize)),
    ])
    return TestClient(app)


def ttyd_hello(ws):
    """ttyd 协议的第一帧:一段 JSON(token + 尺寸)。之后它才 spawn attach.sh。"""
    ws.send_bytes(json.dumps({"AuthToken": "", "columns": 80, "rows": 24}).encode())


def first_output(ws, limit=50):
    """ttyd 发给浏览器的帧,首字节 '0' 是终端输出('1' 标题、'2' 偏好)。"""
    for _ in range(limit):
        frame = ws.receive_bytes()
        if frame[:1] == b"0":
            return frame[1:]
    raise AssertionError("ttyd never sent terminal output")


# -- 默认就是 socket ----------------------------------------------------------


def test_no_port_asked_for_means_a_socket_not_a_port(root, pool):
    t = Tmuxd(socket=pool, state_dir=root)
    try:
        assert t.listen == "unix"
        assert t.port is None and t.bind is None
        assert t.socket_path == os.path.join(t.state_dir, "ttyd.sock")
        assert socket_open(t.socket_path)
    finally:
        t.close()


def test_the_socket_is_the_owners_alone(door):
    """socket 的文件权限就是门。lws 自己建成 0660,组里的人不是我们在服务的那个人。"""
    mode = stat.S_IMODE(os.stat(door.socket_path).st_mode)
    assert mode == 0o600, oct(mode)


def test_asking_for_a_port_still_means_tcp(root, pool):
    """`Tmuxd(port=…)` 的意思一直没变。"""
    port = free_port()
    t = Tmuxd(port=port, socket=pool, state_dir=root)
    try:
        assert t.listen == "tcp" and t.socket_path is None
        assert t.session(id="x").url == "http://127.0.0.1:%d/?arg=x" % port
    finally:
        t.close()


def test_listen_tcp_tuple_names_the_port(root, pool):
    port = free_port()
    t = Tmuxd(listen=("tcp", port), socket=pool, state_dir=root)
    try:
        assert (t.listen, t.port) == ("tcp", port)
    finally:
        t.close()


@pytest.mark.parametrize("kwargs", [
    {"listen": "unix", "port": 1234},
    {"listen": "unix", "bind": "0.0.0.0"},
    {"listen": "udp"},
    {"listen": ("unix", 1)},
    {"port": 1, "listen": ("tcp", 2)},
    {"base_path": "tty"},
])
def test_contradictions_are_refused_not_resolved(root, pool, kwargs):
    with pytest.raises(ValueError):
        Tmuxd(socket=pool, state_dir=root, **kwargs)


def test_no_token_is_fine_on_a_socket(root, pool, monkeypatch):
    """「bind 0.0.0.0 必须带 token」只对 TCP 有意义 —— socket 不在网络上。"""
    monkeypatch.setenv("TMUXD_BIND", "0.0.0.0")
    t = Tmuxd(socket=pool, state_dir=root)
    try:
        assert t.token is None and t.listen == "unix"
    finally:
        t.close()


def test_a_path_too_long_for_a_socket_says_so(tmp_path, pool):
    deep = tmp_path / ("x" * 120)
    with pytest.raises(ValueError, match="too long for a unix socket"):
        Tmuxd(socket=pool, state_dir=str(deep))


# -- URL ----------------------------------------------------------------------


def test_the_url_is_relative_under_the_base_path(door):
    """没有 host 可报:窗在宿主挂它的地方,浏览器按当前页解析。"""
    assert door.session(id="a b").url == "/tty/?arg=a%20b"


def test_without_a_base_path_the_url_is_the_root(root, pool):
    t = Tmuxd(socket=pool, state_dir=root, base_path="/")
    try:
        assert t.base_path is None
        assert t.url_for("x") == "/?arg=x"
    finally:
        t.close()


def test_info_says_where_the_door_is(door):
    ttyd = door.info()["ttyd"]
    assert ttyd["listen"] == "unix"
    assert ttyd["socket_path"] == door.socket_path
    assert ttyd["base_path"] == "/tty"
    assert ttyd["port"] is None


# -- 接手 / 陌生人 / 遗骸 --------------------------------------------------------


def test_a_second_instance_adopts_the_socket(door, root, pool):
    second = Tmuxd(socket=pool, state_dir=root, base_path="/tty")
    try:
        assert second._ttyd.owned is False
        assert second._ttyd.pid == door._ttyd.pid
    finally:
        second.close()
    assert socket_open(door.socket_path), "接手来的不是你的孩子,别带走"


def test_a_different_base_path_is_not_adopted(door, root, pool):
    """接手一个前缀不同的 ttyd,发出去的 URL 它不认。"""
    with pytest.raises(PortInUse) as exc:
        Tmuxd(socket=pool, state_dir=root, base_path="/other")
    assert exc.value.details["path"] == door.socket_path


def test_a_stranger_on_the_socket_is_an_error_not_a_guess(root, pool):
    state = os.path.join(root, pool)
    os.makedirs(state)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(os.path.join(state, "ttyd.sock"))
    listener.listen(1)
    try:
        with pytest.raises(PortInUse):
            Tmuxd(socket=pool, state_dir=root)
    finally:
        listener.close()


def test_a_socket_left_by_a_killed_ttyd_is_cleared(root, pool):
    """SIGKILL 下 ttyd 来不及删 socket 文件。没人应答的 socket 删掉不断开任何人。"""
    first = Tmuxd(socket=pool, state_dir=root)
    path = first.socket_path
    os.kill(first._ttyd.pid, signal.SIGKILL)
    assert wait_until(lambda: not socket_open(path))
    assert os.path.lexists(path)
    first.close()

    second = Tmuxd(socket=pool, state_dir=root)
    try:
        assert second._ttyd.owned is True
        assert socket_open(path)
    finally:
        second.close()


def test_close_takes_the_socket_away(root, pool):
    t = Tmuxd(socket=pool, state_dir=root)
    path = t.socket_path
    t.close()
    assert wait_until(lambda: not socket_open(path))


# -- 从宿主的门进来 ------------------------------------------------------------


def test_the_page_comes_through_the_hosts_port(door):
    client = host(door)
    resp = client.get(door.session(id="p").url)
    assert resp.status_code == 200
    assert b"<!doctype html>" in resp.content[:200].lower()
    assert client.get("/api/hello").text == "host", "宿主自己的路由不受影响"


def test_the_token_endpoint_comes_through(door):
    resp = host(door).get("/tty/token")
    assert resp.status_code == 200
    assert resp.json() == {"token": ""}


def test_the_bare_prefix_lands_on_the_page(door):
    """`/tty` 没有斜杠:Starlette 自己先 307 到 `/tty/`,别的宿主可能交给 ttyd 302。
    谁发的不重要,落点对就行。"""
    resp = host(door).get("/tty", follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308)
    assert resp.headers["location"].endswith("/tty/")
    assert host(door).get("/tty").status_code == 200


def test_the_terminal_works_end_to_end(door):
    """浏览器那一路:握手、tty 子协议、输出回来、输入进去。"""
    door.session(id="e2e", cmd="cat")
    with host(door).websocket_connect("/tty/ws?arg=e2e", subprotocols=["tty"]) as ws:
        assert ws.accepted_subprotocol == "tty"
        ttyd_hello(ws)
        assert first_output(ws) is not None
        ws.send_bytes(b"0through-the-door\r")
        assert wait_for(door, "e2e", "through-the-door")


def test_tcp_mode_can_be_relayed_too(root, pool):
    """转发器不在乎 ttyd 听在哪 —— 过渡形态:TCP 只绑本机,前面挂同一个 app。"""
    t = Tmuxd(port=free_port(), socket=pool, state_dir=root, base_path="/tty")
    try:
        assert host(t).get("/tty/token").status_code == 200
    finally:
        t.close()


# -- 门是宿主的 ----------------------------------------------------------------


def test_a_no_is_403_for_the_page(door):
    resp = host(door, authorize=lambda scope: False).get("/tty/")
    assert resp.status_code == 403


def test_a_no_closes_the_websocket_with_1008(door):
    from starlette.websockets import WebSocketDisconnect

    client = host(door, authorize=lambda scope: False)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/tty/ws?arg=x", subprotocols=["tty"]):
            pass
    assert exc.value.code == 1008


def test_the_gate_sees_every_way_in(door):
    """页面、/token、/ws 各是一条路;漏问一条就是一扇后门。"""
    seen = []

    def gate(scope):
        seen.append((scope["type"], scope["path"]))
        return True

    client = host(door, authorize=gate)
    client.get("/tty/")
    client.get("/tty/token")
    door.session(id="g", cmd="cat")
    with client.websocket_connect("/tty/ws?arg=g", subprotocols=["tty"]) as ws:
        ttyd_hello(ws)
        first_output(ws)
    assert [kind for kind, _ in seen] == ["http", "http", "websocket"]
    assert [path.rsplit("/", 1)[-1] for _, path in seen] == ["", "token", "ws"]


def test_a_gate_can_bind_a_ticket_to_one_window(door):
    """memory.talk 的门:票上写着哪个 worklet,ws 的 arg 就只能是它。"""
    from urllib.parse import parse_qs

    from starlette.websockets import WebSocketDisconnect

    def gate(scope):
        arg = parse_qs(scope["query_string"].decode()).get("arg", [None])[0]
        return scope["type"] != "websocket" or arg == "mine"

    door.session(id="mine", cmd="cat")
    door.session(id="theirs", cmd="cat")
    client = host(door, authorize=gate)
    with client.websocket_connect("/tty/ws?arg=mine", subprotocols=["tty"]) as ws:
        ttyd_hello(ws)
        first_output(ws)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/tty/ws?arg=theirs", subprotocols=["tty"]):
            pass


def test_a_yes_can_carry_headers_for_the_cookie(door):
    """核过票种 cookie:门说「行,顺便带上这几个头」。"""
    def gate(scope):
        return [("Set-Cookie", "tty=abc; Path=/tty; HttpOnly; SameSite=Strict")]

    resp = host(door, authorize=gate).get("/tty/?arg=x")
    assert resp.status_code == 200
    assert resp.headers["set-cookie"].startswith("tty=abc")


def test_the_gate_may_be_async(door):
    async def gate(scope):
        return scope["path"].endswith("/token")

    client = host(door, authorize=gate)
    assert client.get("/tty/token").status_code == 200
    assert client.get("/tty/").status_code == 403


def test_a_dead_ttyd_is_a_502_not_a_hang(root, pool):
    t = Tmuxd(socket=pool, state_dir=root, base_path="/tty")
    client = host(t)
    t.close()
    assert wait_until(lambda: not socket_open(t.socket_path))
    assert client.get("/tty/").status_code == 502
