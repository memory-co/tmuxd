"""exact_targeting — work 绝不能打到 workbench. See README.md."""
from __future__ import annotations

import os
import subprocess

import pytest

from tests.conftest import TOKEN, needs_tmux, needs_ttyd, screen, wait_for
from tmuxd import NoSuchSession
from tmuxd.tmux import target_pane, target_session

pytestmark = needs_tmux


# -- tmux 本身的行为,锁住,换版本要由这里先炸 ------------------------------


def test_tmux_really_does_prefix_match_without_the_equals(instance):
    """先证明危险是真的,再证明我们躲开了它。"""
    instance.session(id="workbench", cmd="cat")

    naive = instance._tmux.run("send-keys", "-t", "work:", "-l", "--", "BBB", check=False)
    assert naive.returncode == 0, "tmux 竟然没有前缀匹配?那下面几条断言的前提要重看"
    assert wait_for(instance, "workbench", "BBB"), "字符落进了 workbench —— 这正是要躲的"


def test_a_bare_equals_is_not_a_valid_pane_target(instance):
    """`=work` 能当 session 目标,但当 pane 目标时 tmux 直接解析不了。

    少写一个冒号不会"退化成前缀匹配",而是整条命令失败 —— 这算好事,
    但得知道是这个形状。
    """
    instance.session(id="work", cmd="cat")

    bad = instance._tmux.run("send-keys", "-t", "=work", "-l", "--", "x", check=False)
    assert bad.returncode != 0
    assert "find pane" in (bad.stderr + bad.stdout)

    good = instance._tmux.run("send-keys", "-t", "=work:", "-l", "--", "x", check=False)
    assert good.returncode == 0


def test_the_two_target_spellings_are_what_we_think(instance):
    assert target_session("work") == "=work"
    assert target_pane("work") == "=work:"


def test_equals_targets_refuse_instead_of_guessing(instance):
    instance.session(id="workbench", cmd="cat")

    for args in (
        ("has-session", "-t", target_session("work")),
        ("send-keys", "-t", target_pane("work"), "-l", "--", "CCC"),
    ):
        out = instance._tmux.run(*args, check=False)
        assert out.returncode != 0, "%s 竟然匹配上了 workbench" % (args,)
    assert "CCC" not in screen(instance, "workbench")


# -- 库的入口 --------------------------------------------------------------


def test_has_does_not_prefix_match(instance):
    instance.session(id="workbench", cmd="cat")
    assert instance.has("work") is False


def test_get_does_not_prefix_match(instance):
    instance.session(id="workbench", cmd="cat")
    with pytest.raises(NoSuchSession):
        instance.get("work")


def test_send_goes_to_the_right_one_of_two_similar_ids(instance):
    instance.session(id="work", cmd="cat")
    instance.session(id="workbench", cmd="cat")

    instance.get("work").send("AAA", enter=True)

    assert wait_for(instance, "work", "AAA")
    assert "AAA" not in screen(instance, "workbench")


# -- ttyd 那条路上的入口 ---------------------------------------------------


def _attach(t, sid):
    env = dict(os.environ, _TMUXD_SOCK=t.tmux_socket, _TMUXD_TMUX=t.tmux_bin)
    return subprocess.run(
        [os.path.join(t.state_dir, "attach.sh"), sid],
        capture_output=True, text=True, env=env, timeout=10,
    )


@needs_ttyd
def test_attach_does_not_prefix_match(served):
    """`?arg=` 是调用方可以随便填的,近似的 id 不能把人送进别人的终端。"""
    served.session(id="workbench", cmd="cat")

    out = _attach(served, "work")
    assert out.returncode == 1
    assert "unknown session" in out.stderr
