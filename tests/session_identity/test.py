"""session_identity — id 说了算. See README.md."""
from __future__ import annotations

import pytest

from tests.conftest import needs_tmux, wait_for, wait_until
from tmuxd import BadId, NoSuchSession, SessionExists

pytestmark = needs_tmux


# -- 有则接上,无则创建 ----------------------------------------------------


def test_session_is_get_or_create(instance):
    a = instance.session(id="work", cwd=instance.workspace)
    b = instance.session(id="work")
    assert (a.id, b.id) == ("work", "work")
    assert len(instance.sessions()) == 1


def test_the_id_decides_not_the_payload(instance):
    """再 session() 一次不会按新参数重建 —— 你接上的是原来那个现场。

    误以为"参数变了就会重建"的人,会在生产上以为自己重启了服务。
    """
    instance.session(id="work", cmd="cat")
    again = instance.session(id="work", cwd="/tmp", cmd="sh")
    assert again.cmd == "cat"
    assert again.cwd == instance.workspace


def test_create_refuses_an_existing_id(instance):
    instance.session(id="work", cmd="cat")
    with pytest.raises(SessionExists):
        instance.create(id="work")


def test_get_never_creates(instance):
    with pytest.raises(NoSuchSession):
        instance.get("nope")
    assert instance.sessions() == []


def test_has_answers_without_creating(instance):
    assert instance.has("ghost") is False
    assert instance.sessions() == []


# -- id 本身 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "bad, why",
    [
        ("", "空"),
        ("a.b", "tmux 会话名不能含点"),
        ("a:b", "tmux 会话名不能含冒号"),
        ("-lead", "会被当成命令行选项"),
        ("x\ny", "控制字符会把任何列表打乱"),
        ("z" * 201, "太长"),
    ],
)
def test_bad_ids_are_refused_not_rewritten(instance, bad, why):
    """悄悄改过的 id,下次就再也找不回那个现场了。"""
    with pytest.raises(BadId):
        instance.session(id=bad)


def test_has_says_false_for_an_illegal_id(instance):
    """`has` 是给脚本判断用的,不该因为参数脏就抛。"""
    assert instance.has("a:b") is False


def test_generated_ids_count_up_like_tmux(instance):
    assert instance.session().id == "0"
    assert instance.session().id == "1"


def test_rename(instance):
    instance.session(id="before", cmd="cat")
    s = instance.get("before")
    s.rename("after")

    assert s.id == "after"
    assert instance.has("after")
    assert not instance.has("before")
    assert instance.get("after").cmd == "cat"       # 记录跟着搬了家


def test_rename_onto_a_taken_id_refuses(instance):
    instance.session(id="a", cmd="cat")
    instance.session(id="b", cmd="cat")
    with pytest.raises(SessionExists):
        instance.get("a").rename("b")
    assert instance.has("a")                        # 原地不动


# -- cwd / cmd / env 真的传到了 tmux ---------------------------------------


def test_cwd_reaches_the_session(instance, tmp_path):
    target = tmp_path / "sub"
    target.mkdir()
    s = instance.session(id="c1", cwd=str(target), cmd="sh -c 'pwd; sleep 30'")
    assert wait_for(instance, "c1", str(target))
    assert s.cwd == str(target)


def test_env_reaches_the_session(instance):
    instance.session(id="e1", cmd="sh -c 'echo [$GREETING]; sleep 30'",
                     env={"GREETING": "hi-there"})
    assert wait_for(instance, "e1", "[hi-there]")


def test_cwd_defaults_to_the_workspace(instance):
    assert instance.session(id="d1").cwd == instance.workspace


def test_a_command_that_does_not_exist_is_an_exited_session(instance):
    """不是 cmd_not_found 这种错误码 —— 和你在自己终端里敲错命令是一回事。"""
    instance.session(id="bogus", cmd="definitely-not-a-real-binary-xyz")

    assert wait_until(lambda: not instance.has("bogus"))
    listed = instance.sessions()
    assert [s.id for s in listed] == ["bogus"]
    assert listed[0].status == "exited"
