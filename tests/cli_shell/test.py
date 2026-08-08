"""cli_shell — 命令行壳. See README.md."""
from __future__ import annotations

import json
import os

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
from tmuxd import Tmuxd, cli

pytestmark = needs_tmux


@pytest.fixture
def run(tmp_path, request, monkeypatch):
    """调 cli.main(argv),不 spawn 子进程 —— 壳的逻辑进程内就验得完。"""
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "absent.conf"))
    for leak in ("TMUXD_HOST", "TMUXD_TOKEN", "TMUXD_PORT"):
        monkeypatch.delenv(leak, raising=False)

    name = pool_name(request, prefix="cli")
    base = ["-L", name, "--state-dir", str(tmp_path)]

    def _run(*argv):
        return cli.main(base + list(argv))

    _run.socket = name
    _run.state_dir = str(tmp_path)
    yield _run
    kill_pool(name)


def lib(run):
    """同一个池的库句柄,用来验 CLI 到底做了什么。"""
    return Tmuxd(port=None, socket=run.socket, state_dir=run.state_dir)


def out(capsys):
    return capsys.readouterr().out


# -- 参数真的递下去了 -------------------------------------------------------


def test_new_creates_and_prints_the_entrance(run, capsys, monkeypatch):
    monkeypatch.setenv("TMUXD_PORT", "12345")
    assert run("new", "-s", "work") == 0

    text = out(capsys)
    assert "work" in text and "?arg=work" in text


def test_new_says_so_when_there_is_no_port(run, capsys):
    assert run("new", "-s", "work") == 0
    assert "no ttyd port" in out(capsys)


def test_the_dash_dash_is_punctuation_not_the_command(run, capsys):
    """argparse.REMAINDER 会把 "--" 一起交回来。不剥掉就在跑命令 `-- cat`。"""
    assert run("new", "-s", "c1", "--", "sh", "-c", "echo marker; sleep 30") == 0
    capsys.readouterr()

    t = lib(run)
    try:
        assert t.get("c1").cmd == "sh -c 'echo marker; sleep 30'"
        assert wait_for(t, "c1", "marker")
    finally:
        t.close()


def test_cwd_and_env_reach_the_session(run, capsys, tmp_path):
    target = tmp_path / "sub"
    target.mkdir()
    assert run("new", "-s", "e1", "-c", str(target), "-e", "GREETING=hi",
               "--", "sh", "-c", "pwd; echo [$GREETING]; sleep 30") == 0
    capsys.readouterr()

    t = lib(run)
    try:
        assert wait_for(t, "e1", str(target))
        assert wait_for(t, "e1", "[hi]")
    finally:
        t.close()


def test_send_passes_the_text_through_unchanged(run, capsys):
    """还是那句话 —— 壳这一层也不能把 Enter 变成回车。"""
    run("new", "-s", "lit", "--", "cat")
    capsys.readouterr()

    assert run("send", "-t", "lit", "Enter the code") == 0
    assert "sent" in out(capsys)

    t = lib(run)
    try:
        assert wait_for(t, "lit", "Enter the code")
    finally:
        t.close()


def test_keys_presses_key_names(run, capsys):
    run("new", "-s", "k1", "--", "cat")
    capsys.readouterr()
    assert run("keys", "-t", "k1", "C-c") == 0

    t = lib(run)
    try:
        assert wait_until(lambda: not t.has("k1"))
    finally:
        t.close()


def test_url_prints_just_the_address(run, capsys, monkeypatch):
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


def test_ls_and_its_format_string(run, capsys):
    run("new", "-s", "f1", "--", "cat")
    capsys.readouterr()

    assert run("ls") == 0
    assert "f1" in out(capsys)

    assert run("ls", "-F", "#{session_id}|#{session_status}") == 0
    assert out(capsys).strip() == "f1|alive"


def test_json_output_is_the_library_object(run, capsys):
    assert run("--json", "info") == 0
    payload = json.loads(out(capsys))
    assert payload["tmux"]["socket"].startswith("tmuxd-")


# -- 退出码是接口 -----------------------------------------------------------


def test_has_uses_3_for_absent_which_is_an_answer(run):
    run("new", "-s", "there", "--", "cat")
    assert run("has", "-t", "there") == 0
    assert run("has", "-t", "absent") == cli.EXIT_NO_SESSION


def test_missing_session_exits_3(run):
    assert run("send", "-t", "nope", "x") == cli.EXIT_NO_SESSION


def test_bad_id_exits_4(run):
    assert run("new", "-s", "bad:id") == cli.EXIT_STATE


def test_unreachable_remote_exits_5(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "absent.conf"))
    assert cli.main(["-H", "http://127.0.0.1:%d" % free_port(), "ls"]) \
        == cli.EXIT_UNREACHABLE


def test_errors_go_to_stderr_so_stdout_stays_pipeable(run, capsys):
    run("send", "-t", "ghost", "x")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "✗" in captured.err


# -- kill-server 要两道确认 -------------------------------------------------


def test_kill_server_demands_the_flag(run, capsys):
    assert run("kill-server") == cli.EXIT_USAGE


def test_kill_server_with_the_flag(run, capsys):
    run("new", "-s", "doomed", "--", "cat")
    capsys.readouterr()
    assert run("kill-server", "--tmux", "-y") == 0
    assert run("has", "-t", "doomed") == cli.EXIT_NO_SESSION


# -- 配置文件 ---------------------------------------------------------------


def test_config_is_another_spelling_of_the_constructor(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("# comment\nset -g port 23456\nset -g history-limit 500\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    monkeypatch.delenv("TMUXD_PORT", raising=False)

    assert cli.read_config()["port"] == "23456"

    name = "conf-%d" % os.getpid()
    try:
        assert cli.main(["-L", name, "--state-dir", str(tmp_path),
                         "new", "-s", "cfg"]) == 0
        assert ":23456/?arg=cfg" in capsys.readouterr().out
    finally:
        kill_pool(name)


def test_command_line_beats_the_config_file(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("set -g port 23456\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    monkeypatch.delenv("TMUXD_PORT", raising=False)

    name = "prec-%d" % os.getpid()
    try:
        cli.main(["-L", name, "--state-dir", str(tmp_path), "--port", "34567",
                  "new", "-s", "p"])
        assert ":34567/?arg=p" in capsys.readouterr().out
    finally:
        kill_pool(name)


# -- daemon:停的是门面 -----------------------------------------------------


@needs_ttyd
def test_start_status_stop_leaves_the_sessions_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "absent.conf"))
    from tmuxd.ttyd import port_open

    port = free_port()
    name = "daemon-%d" % os.getpid()
    base = ["-L", name, "--state-dir", str(tmp_path), "--port", str(port)]
    try:
        assert cli.main(base + ["start"]) == 0
        capsys.readouterr()

        assert cli.main(base + ["status"]) == 0
        assert "running" in capsys.readouterr().out
        assert port_open("127.0.0.1", port)

        assert cli.main(base + ["new", "-s", "held", "--", "cat"]) == 0
        capsys.readouterr()

        assert cli.main(base + ["stop"]) == 0
        assert "still running" in capsys.readouterr().out
        assert wait_until(lambda: not port_open("127.0.0.1", port))

        assert cli.main(base + ["has", "-t", "held"]) == 0     # 门关了,人还在
    finally:
        kill_pool(name)


@needs_ttyd
def test_remote_mode_refuses_lifecycle_commands(tmp_path, monkeypatch, capsys):
    """进程生命周期是对面那台机器的事。"""
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "absent.conf"))
    assert cli.main(["-H", "http://127.0.0.1:1", "stop"]) == cli.EXIT_USAGE
    assert "远端" in capsys.readouterr().err or True
