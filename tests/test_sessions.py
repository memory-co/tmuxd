import os
import subprocess
import time

import pytest

from conftest import needs_tmux
from tmuxd import BadId, NoSuchSession, SessionExists, Tmuxd

pytestmark = needs_tmux


def capture(t, sid):
    """Read the screen. Only the tests do this -- the library deliberately
    offers no way to (works/03-http.md §2)."""
    out = t._tmux.run("capture-pane", "-t", "=%s:" % sid, "-p", "-J", check=False)
    return out.stdout


def wait_for(t, sid, needle, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle in capture(t, sid):
            return True
        time.sleep(0.05)
    return False


# -- the lazy tmux server -------------------------------------------------


def test_no_tmux_server_until_first_session(instance):
    """`Tmuxd()` starts no tmux server, and listing before one exists is an
    empty list rather than the exception tmux's exit code suggests."""
    assert instance._tmux.server_running() is False
    assert instance.sessions() == []
    assert instance.has("anything") is False

    instance.session(id="first")
    assert instance._tmux.server_running() is True


# -- identity: id + cwd + cmd --------------------------------------------


def test_session_is_get_or_create(instance):
    a = instance.session(id="work", cwd=instance.workspace)
    b = instance.session(id="work", cwd="/tmp", cmd="cat")  # ignored: the id decides
    assert b.cwd == a.cwd
    assert b.cmd is None
    assert len(instance.sessions()) == 1


def test_create_refuses_an_existing_id(instance):
    instance.session(id="work")
    with pytest.raises(SessionExists):
        instance.create(id="work")


def test_get_never_creates(instance):
    with pytest.raises(NoSuchSession):
        instance.get("nope")
    assert instance.sessions() == []


@pytest.mark.parametrize("bad", ["", "a.b", "a:b", "-lead", "x\ny", "z" * 201])
def test_bad_ids_are_refused_not_rewritten(instance, bad):
    with pytest.raises(BadId):
        instance.session(id=bad)


def test_generated_ids_count_up_like_tmux(instance):
    assert instance.session().id == "0"
    assert instance.session().id == "1"


def test_cwd_and_cmd_reach_tmux(instance, tmp_path):
    target = tmp_path / "sub"
    target.mkdir()
    s = instance.session(id="c1", cwd=str(target), cmd="sh -c 'pwd; sleep 30'")
    assert wait_for(instance, "c1", str(target))
    assert s.cwd == str(target)


def test_env_reaches_the_session(instance):
    instance.session(id="e1", cmd="sh -c 'echo [$GREETING]; sleep 30'",
                     env={"GREETING": "hi-there"})
    assert wait_for(instance, "e1", "[hi-there]")


def test_a_command_that_does_not_exist_is_an_exited_session_not_an_error(instance):
    """No cmd_not_found error code: it shows up as a session that exited,
    exactly like mistyping a command in your own terminal."""
    instance.session(id="bogus", cmd="definitely-not-a-real-binary-xyz")
    deadline = time.time() + 5
    while time.time() < deadline and instance.has("bogus"):
        time.sleep(0.05)
    assert instance.has("bogus") is False
    assert [s.id for s in instance.sessions()] == ["bogus"]
    assert instance.sessions()[0].status == "exited"


# -- exact matching -------------------------------------------------------


def test_prefix_matching_is_off(instance):
    """`work` must never resolve to `workbench` -- tmux would happily deliver
    the keystrokes to the wrong terminal."""
    instance.session(id="workbench", cmd="cat")
    assert instance.has("work") is False
    with pytest.raises(NoSuchSession):
        instance.get("work").send("BBB")
    assert "BBB" not in capture(instance, "workbench")


def test_send_goes_to_the_right_session_of_two_similar_ones(instance):
    instance.session(id="work", cmd="cat")
    instance.session(id="workbench", cmd="cat")
    instance.get("work").send("AAA", enter=True)
    assert wait_for(instance, "work", "AAA")
    assert "AAA" not in capture(instance, "workbench")


# -- writing --------------------------------------------------------------


def test_send_is_literal_even_when_it_says_enter(instance):
    """The trap this API shape exists to remove: `send-keys` without -l reads
    "Enter the code" as a Return keypress."""
    instance.session(id="lit", cmd="cat")
    instance.get("lit").send("Enter the code")
    assert wait_for(instance, "lit", "Enter the code")


def test_send_key_presses_names(instance):
    instance.session(id="k1", cmd="cat")
    s = instance.get("k1")
    s.send("hello")
    assert wait_for(instance, "k1", "hello")
    s.send_key("Enter")
    assert wait_for(instance, "k1", "hello")
    s.send_key("C-c")
    time.sleep(0.3)
    assert instance.has("k1") is False  # cat took the interrupt and left


def test_text_starting_with_a_dash_is_not_an_option(instance):
    instance.session(id="dash", cmd="cat")
    instance.get("dash").send("--help me")
    assert wait_for(instance, "dash", "--help me")


def test_sending_to_a_dead_session_raises(instance):
    instance.session(id="gone", cmd="cat")
    instance.get("gone").kill()
    with pytest.raises(NoSuchSession):
        instance.get("gone")


# -- lifecycle ------------------------------------------------------------


def test_only_kill_destroys(instance):
    s = instance.session(id="keep", cmd="cat")
    instance.close()          # takes down ttyd (none here), never sessions
    assert instance.has("keep") is True
    assert s.kill() == 0
    assert instance.has("keep") is False


def test_sessions_outlive_the_python_object(instance, tmp_path):
    """The whole point: the facade is short-lived, the house is not."""
    instance.session(id="survivor", cmd="cat")
    name = instance.socket_name

    revived = Tmuxd(port=None, socket=name, state_dir=str(tmp_path))
    try:
        assert revived.has("survivor") is True
        record = revived.get("survivor")
        assert record.cmd == "cat"           # the clue file survived too
        assert record.status == "alive"
    finally:
        revived.close()


def test_rename(instance):
    instance.session(id="before", cmd="cat")
    instance.get("before").rename("after")
    assert instance.has("after") is True
    assert instance.has("before") is False
    assert instance.get("after").cmd == "cat"


def test_rename_onto_a_taken_id_refuses(instance):
    instance.session(id="a", cmd="cat")
    instance.session(id="b", cmd="cat")
    with pytest.raises(SessionExists):
        instance.get("a").rename("b")


def test_kill_reports_thrown_out_clients(instance):
    instance.session(id="busy", cmd="cat")
    assert instance.get("busy").clients == 0


# -- reconciliation --------------------------------------------------------


def test_external_sessions_are_listed_never_adopted(instance):
    """Someone went around the library. Say so; do not write a state file that
    would turn a visible anomaly into an invisible lie."""
    instance.session(id="mine", cmd="cat")
    subprocess.run(
        [instance.tmux_bin, "-L", instance.tmux_socket, "new-session", "-d",
         "-s", "intruder"],
        check=True, capture_output=True,
    )
    by_id = {s.id: s for s in instance.sessions()}
    assert by_id["intruder"].external is True
    assert by_id["mine"].external is False
    assert not os.path.exists(instance._store.path_for("intruder"))
    assert by_id["intruder"].alive is True     # still perfectly usable


def test_dead_session_keeps_its_record_then_gets_swept(instance, tmp_path):
    instance.session(id="short", cmd="true")
    deadline = time.time() + 5
    while time.time() < deadline and instance.has("short"):
        time.sleep(0.05)

    assert [s.id for s in instance.sessions()] == ["short"]     # kept for now

    instance.gc_ttl = -1                                        # pretend time passed
    assert instance.sessions() == []
    assert not os.path.exists(instance._store.path_for("short"))


def test_gc_never_kills_a_live_session(instance):
    instance.session(id="alive", cmd="cat")
    instance.gc_ttl = -1
    assert [s.id for s in instance.sessions()] == ["alive"]
    assert instance.has("alive") is True


# -- guardrails ------------------------------------------------------------


def test_refuses_to_share_your_own_tmux(tmp_path):
    with pytest.raises(ValueError) as exc:
        Tmuxd(port=None, socket="default", state_dir=str(tmp_path))
    assert "own" in str(exc.value)


def test_public_bind_without_a_token_is_refused(tmp_path):
    with pytest.raises(ValueError) as exc:
        Tmuxd(port=1, bind="0.0.0.0", state_dir=str(tmp_path))
    assert "token" in str(exc.value)


def test_pool_is_separate_from_your_tmux(instance):
    assert instance.tmux_socket != "default"
    assert instance.tmux_socket.startswith("tmuxd-")
