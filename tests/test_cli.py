import json
import os
import time

import pytest

from conftest import _kill_pool, free_port, needs_tmux, needs_ttyd
from tmuxd import cli

pytestmark = needs_tmux


@pytest.fixture
def run(tmp_path, request, monkeypatch):
    """Run CLI argv against a private instance; returns (exit_code, stdout)."""
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "empty.conf"))
    monkeypatch.delenv("TMUXD_HOST", raising=False)
    monkeypatch.delenv("TMUXD_TOKEN", raising=False)
    name = "cli-%s-%d" % (request.node.name.replace("_", "-")[:20], os.getpid())
    base = ["-L", name, "--state-dir", str(tmp_path)]

    def _run(*argv, capsys=None):
        code = cli.main(base + list(argv))
        return code

    _run.socket = name
    yield _run
    _kill_pool(name)


def out(capsys):
    return capsys.readouterr().out


# -- the eleven commands ---------------------------------------------------


def test_new_prints_id_and_url(run, capsys, monkeypatch):
    monkeypatch.setenv("TMUXD_PORT", "12345")
    assert run("new", "-s", "work") == 0
    text = out(capsys)
    assert "work" in text
    assert "?arg=work" in text


def test_new_without_a_port_says_so_instead_of_printing_a_broken_url(run, capsys):
    assert run("new", "-s", "work") == 0
    assert "no ttyd port" in out(capsys)


def test_ls_and_has(run, capsys):
    run("new", "-s", "a", "--", "cat")
    capsys.readouterr()

    assert run("ls") == 0
    assert "a" in out(capsys)

    assert run("has", "-t", "a") == 0
    assert run("has", "-t", "ghost") == cli.EXIT_NO_SESSION


def test_send_and_keys(run, capsys, tmp_path):
    run("new", "-s", "s1", "--", "cat")
    capsys.readouterr()

    assert run("send", "-t", "s1", "hello there", "--enter") == 0
    assert "sent" in out(capsys)

    from tmuxd import Tmuxd
    from test_sessions import wait_for

    t = Tmuxd(port=None, socket=run.socket, state_dir=str(tmp_path))
    try:
        assert wait_for(t, "s1", "hello there")
    finally:
        t.close()

    assert run("keys", "-t", "s1", "C-c") == 0


def test_send_text_is_never_read_as_a_key_name(run, capsys, tmp_path):
    run("new", "-s", "lit", "--", "cat")
    capsys.readouterr()
    run("send", "-t", "lit", "Enter the code")

    from tmuxd import Tmuxd
    from test_sessions import wait_for

    t = Tmuxd(port=None, socket=run.socket, state_dir=str(tmp_path))
    try:
        assert wait_for(t, "lit", "Enter the code")
    finally:
        t.close()


def test_url(run, capsys, monkeypatch):
    monkeypatch.setenv("TMUXD_PORT", "12345")
    run("new", "-s", "u")
    capsys.readouterr()
    assert run("url", "-t", "u") == 0
    assert out(capsys).strip() == "http://127.0.0.1:12345/?arg=u"


def test_rename_and_kill(run, capsys):
    run("new", "-s", "old", "--", "cat")
    capsys.readouterr()

    assert run("rename", "-t", "old", "new") == 0
    assert run("has", "-t", "new") == 0

    assert run("kill", "-t", "new") == 0
    assert run("has", "-t", "new") == cli.EXIT_NO_SESSION


def test_new_with_a_command_after_dashdash(run, capsys, tmp_path):
    assert run("new", "-s", "c1", "-c", str(tmp_path), "--", "sh", "-c",
               "echo marker; sleep 30") == 0
    capsys.readouterr()

    from tmuxd import Tmuxd
    from test_sessions import wait_for

    t = Tmuxd(port=None, socket=run.socket, state_dir=str(tmp_path))
    try:
        assert wait_for(t, "c1", "marker")
    finally:
        t.close()


def test_info_json(run, capsys):
    assert run("--json", "info") == 0
    payload = json.loads(out(capsys))
    assert payload["tmux"]["socket"].startswith("tmuxd-")


def test_ls_format_string(run, capsys):
    run("new", "-s", "f1", "--", "cat")
    capsys.readouterr()
    assert run("ls", "-F", "#{session_id}|#{session_status}") == 0
    assert out(capsys).strip() == "f1|alive"


# -- exit codes ------------------------------------------------------------


def test_missing_session_exits_3(run, capsys):
    assert run("send", "-t", "nope", "x") == cli.EXIT_NO_SESSION


def test_bad_id_exits_4(run, capsys):
    assert run("new", "-s", "bad:id") == cli.EXIT_STATE


def test_unreachable_remote_exits_5(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "none.conf"))
    assert cli.main(["-H", "http://127.0.0.1:%d" % free_port(), "ls"]) == cli.EXIT_UNREACHABLE


def test_kill_server_demands_confirmation(run, capsys):
    assert run("kill-server") == cli.EXIT_USAGE


def test_kill_server_with_tmux_flag(run, capsys):
    run("new", "-s", "doomed", "--", "cat")
    capsys.readouterr()
    assert run("kill-server", "--tmux", "-y") == 0
    assert run("has", "-t", "doomed") == cli.EXIT_NO_SESSION


# -- config file -----------------------------------------------------------


def test_config_file_is_another_way_to_spell_constructor_args(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("# comment\nset -g port 23456\nset -g history-limit 500\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    monkeypatch.delenv("TMUXD_PORT", raising=False)

    values = cli.read_config()
    assert values["port"] == "23456"
    assert values["history-limit"] == "500"

    name = "conf-%d" % os.getpid()
    try:
        assert cli.main(["-L", name, "--state-dir", str(tmp_path), "new", "-s", "cfg"]) == 0
        assert ":23456/?arg=cfg" in capsys.readouterr().out
    finally:
        _kill_pool(name)


# -- daemon ----------------------------------------------------------------


@needs_ttyd
def test_start_status_stop(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "none.conf"))
    port = free_port()
    name = "daemon-%d" % os.getpid()
    base = ["-L", name, "--state-dir", str(tmp_path), "--port", str(port)]
    try:
        assert cli.main(base + ["start"]) == 0
        capsys.readouterr()

        assert cli.main(base + ["status"]) == 0
        assert "running" in capsys.readouterr().out

        from tmuxd.ttyd import port_open

        assert port_open("127.0.0.1", port)

        assert cli.main(base + ["new", "-s", "held", "--", "cat"]) == 0
        capsys.readouterr()

        assert cli.main(base + ["stop"]) == 0
        assert "still running" in capsys.readouterr().out

        deadline = time.time() + 5
        while time.time() < deadline and port_open("127.0.0.1", port):
            time.sleep(0.1)
        assert not port_open("127.0.0.1", port)

        # the door closed; the room did not
        assert cli.main(base + ["has", "-t", "held"]) == 0
    finally:
        _kill_pool(name)


@needs_ttyd
def test_remote_mode_refuses_lifecycle_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "none.conf"))
    code = cli.main(["-H", "http://127.0.0.1:1", "stop"])
    assert code == cli.EXIT_USAGE
