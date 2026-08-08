"""port_reuse — 同一个端口上已经有 ttyd 了怎么办. See README.md."""
from __future__ import annotations

import socket

import pytest

from tests.conftest import (
    free_port,
    kill_pool,
    needs_tmux,
    needs_ttyd,
    pool_name,
    wait_until,
)
from tmuxd import PortInUse, Tmuxd
from tmuxd.ttyd import port_open

pytestmark = [needs_tmux, needs_ttyd]


def test_an_empty_port_gets_one_that_we_own(tmp_path, request):
    name = pool_name(request, prefix="own")
    port = free_port()
    t = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    try:
        assert t._ttyd.owned is True
        assert port_open("127.0.0.1", port)
    finally:
        t.close()
        kill_pool(name)
    assert wait_until(lambda: not port_open("127.0.0.1", port)), \
        "自己起的那个,close() 之后该走"


def test_a_second_instance_adopts_instead_of_fighting(tmp_path, request):
    """Web 后端重启 worker 时的现实路径。"""
    name = pool_name(request, prefix="adopt")
    port = free_port()
    first = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    try:
        second = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
        assert second._ttyd.owned is False
        assert second._ttyd.pid == first._ttyd.pid

        second.close()
        assert port_open("127.0.0.1", port), "接手来的不是你的孩子,别带走"
        assert first._ttyd.alive()
    finally:
        first.close()
        kill_pool(name)


def test_the_adopted_ttyd_still_serves_the_adopters_sessions(tmp_path, request):
    """接手不是"凑合能用":新实例建的会话,得能从那个 ttyd 进去。"""
    name = pool_name(request, prefix="adopt2")
    port = free_port()
    first = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    try:
        second = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
        s = second.session(id="via-adopted", cmd="cat")
        assert s.url == "http://127.0.0.1:%d/?arg=via-adopted" % port
        assert first.has("via-adopted"), "同一个池,两个实例看到的是同一批会话"
        second.close()
    finally:
        first.close()
        kill_pool(name)


def test_a_stranger_on_the_port_is_an_error_not_a_guess(tmp_path, request):
    """猜错的两种后果都不能接受:劫持别人的服务,或者把人送进错的终端。"""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    name = pool_name(request, prefix="stranger")
    try:
        with pytest.raises(PortInUse) as exc:
            Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
        assert exc.value.details["port"] == port
    finally:
        listener.close()
        kill_pool(name)


def test_info_says_who_owns_the_door(tmp_path, request):
    """`owned` 是运维时要知道的那件事:我退出会不会把网页入口带走。"""
    name = pool_name(request, prefix="peek")
    port = free_port()
    owner = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
    try:
        assert owner.info()["ttyd"]["owned"] is True

        adopter = Tmuxd(port=port, socket=name, state_dir=str(tmp_path))
        try:
            assert adopter.info()["ttyd"]["owned"] is False
            assert adopter.info()["ttyd"]["port"] == port
        finally:
            adopter.close()
        assert owner._ttyd.alive()               # 看一眼不该影响它
    finally:
        owner.close()
        kill_pool(name)
