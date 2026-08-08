"""typing_in — 往里敲. See README.md."""
from __future__ import annotations

import pytest

from tests.conftest import needs_tmux, screen, wait_for, wait_until
from tmuxd import NoSuchSession

pytestmark = needs_tmux


def test_send_is_literal_even_when_it_says_enter(instance):
    """这句话就是那个坑本身。

    `tmux send-keys "Enter the code"` 会把 Enter 当回车键按下去。
    走 send() 就该是七个词原样进去。
    """
    instance.session(id="lit", cmd="cat")
    instance.get("lit").send("Enter the code")

    assert wait_for(instance, "lit", "Enter the code")


def test_send_does_not_press_enter_unless_asked(instance):
    instance.session(id="noenter", cmd="cat")
    instance.get("noenter").send("half a line")

    assert wait_for(instance, "noenter", "half a line")
    # cat 只在读到换行后才回显,所以还没有第二行
    assert screen(instance, "noenter").count("half a line") == 1


def test_enter_true_submits_it(instance):
    instance.session(id="withenter", cmd="cat")
    instance.get("withenter").send("echoed back", enter=True)

    # cat 收到整行后回显,于是屏幕上出现两次
    assert wait_until(
        lambda: screen(instance, "withenter").count("echoed back") >= 2
    )


def test_text_starting_with_a_dash_is_not_an_option(instance):
    instance.session(id="dash", cmd="cat")
    instance.get("dash").send("--help me")

    assert wait_for(instance, "dash", "--help me")


def test_send_key_really_presses_the_key(instance):
    """屏幕上有字 ≠ 按键被终端处理了。用 C-c 打断 cat 来证明后者。"""
    instance.session(id="k1", cmd="cat")
    s = instance.get("k1")

    s.send("hello", enter=True)
    assert wait_for(instance, "k1", "hello")

    s.send_key("C-c")
    assert wait_until(lambda: not instance.has("k1")), "C-c 没有真的送达"


def test_send_key_takes_several(instance):
    instance.session(id="k2", cmd="cat")
    instance.get("k2").send("x").send_key("Enter", "Enter")
    assert wait_for(instance, "k2", "x")


def test_sending_says_nothing_about_the_outcome(instance):
    """一条注定失败的命令,send() 照样正常返回 —— 它只承诺"字符交出去了"。"""
    instance.session(id="fails", cmd="bash")
    s = instance.get("fails")

    assert s.send("definitely-not-a-real-binary-xyz", enter=True) is s

    assert wait_for(instance, "fails", "not found") or \
        wait_for(instance, "fails", "definitely-not-a-real-binary-xyz")
    assert s.alive                      # shell 还在,失败的是它里面那条命令


def test_sending_to_a_session_that_is_gone_raises(instance):
    instance.session(id="gone", cmd="cat")
    s = instance.get("gone")
    s.kill()

    with pytest.raises(NoSuchSession):
        s.send("anyone there")
    with pytest.raises(NoSuchSession):
        s.send_key("C-c")


def test_send_returns_self_so_it_chains(instance):
    instance.session(id="chain", cmd="cat")
    s = instance.get("chain")
    assert s.send("a").send_key("Enter").send("b") is s
