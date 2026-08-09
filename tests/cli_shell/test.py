"""cli_shell — 命令行壳. See README.md."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from tests.conftest import (
    free_port,
    kill_pool,
    needs_ttyd,
    needs_tmux,
    pool_name,
    wait_until,
)
from tmuxd import cli

pytestmark = [needs_tmux, needs_ttyd]

pytest.importorskip("fastapi", reason="the CLI needs a server: tmuxd[server]")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def bare(tmp_path, request, monkeypatch):
    """一套配好的实例参数,但**不起 server** —— 用来验"没起 server 会怎样"。"""
    monkeypatch.setenv("TMUXD_CONFIG", str(tmp_path / "absent.conf"))
    for leak in ("TMUXD_PORT", "TMUXD_CONTROL_PORT", "TMUXD_TOKEN", "TMUXD_SOCKET"):
        monkeypatch.delenv(leak, raising=False)

    name = pool_name(request, prefix="cli")
    base = ["-L", name, "--state-dir", str(tmp_path),
            "--port", str(free_port()), "--control-port", str(free_port())]

    def _run(*argv):
        return cli.main(base + list(argv))

    _run.argv = base
    _run.socket = name
    _run.state_dir = str(tmp_path)
    yield _run
    kill_pool(name)


@pytest.fixture
def run(bare):
    """同上,但先把 server 起起来 —— CLI 的每条命令都要它。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "tmuxd"] + bare.argv + ["serve"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # 等一次**真的 API 调用**通,而不是等 pid 文件出现 —— `status` 在只有
        # pid、uvicorn 还没绑上端口时就会返回 0,拿它当就绪信号会让用例跑在
        # server 起来之前。
        assert wait_until(lambda: bare("ls") == 0, timeout=30), \
            "control 口没起来:\n%s" % (proc.stdout.read() if proc.poll() else "")
        yield bare
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def out(capsys):
    return capsys.readouterr().out


# -- CLI 离不开 server ------------------------------------------------------


def test_without_a_server_the_cli_says_so(bare, capsys):
    """一条只读命令不该顺手起一个门面又立刻带走它 —— 如实说没有就够了。"""
    assert bare("ls") == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "no server running" in err
    assert "tmuxd start" in err


def test_and_it_names_the_control_port_not_the_ttyd_one(bare, capsys):
    bare("ls")
    err = capsys.readouterr().err
    control = bare.argv[bare.argv.index("--control-port") + 1]
    ttyd = bare.argv[bare.argv.index("--port") + 1]
    assert control in err and ttyd not in err


def test_status_shows_one_thing_with_two_ports(run, capsys):
    """一个进程,两个口 —— 不是三件事。"""
    assert run("status") == 0
    text = out(capsys)
    assert "running" in text
    assert run.argv[run.argv.index("--port") + 1] in text
    assert run.argv[run.argv.index("--control-port") + 1] in text


def test_status_never_contradicts_itself(run, capsys):
    """管控口答了,就不许再说"没在跑"。

    早先 `server:` 那行读的是 pid 文件(一句**声称**),而 `control:` 那行读的是
    真的应答(一个**证明**),于是能打印出"server: not running / control:
    listening"这种自相矛盾的东西。现在判据只有一个:它答不答。
    """
    assert run("status") == 0
    text = out(capsys)
    assert "not running" not in text, text


def test_stop_leaves_no_daemon_file(run, tmp_path):
    """SIGTERM 默认处置会直接杀掉进程,`finally` 一次都不会跑 ——
    所以每次 stop 都会留下一个 daemon.json。信号必须变成异常。"""
    assert (tmp_path / "daemon.json").exists()
    assert run("stop") == 0
    assert wait_until(lambda: not (tmp_path / "daemon.json").exists(), timeout=10), \
        "stop 之后文件还在"


def test_a_stale_file_does_not_block_start(bare, tmp_path, capsys):
    """活着的 pid 不是证据 —— pid 会被复用,崩溃留下的文件不该挡住以后每一次启动。"""
    import json as _json
    import os as _os

    (tmp_path / "daemon.json").write_text(_json.dumps(
        {"pid": _os.getpid(), "socket": bare.socket, "bind": "127.0.0.1",
         "ttyd_port": 1, "control_port": 2}))          # pid 活着,但那是 pytest 自己
    assert bare("start") == 0
    text = out(capsys)
    assert "already running" not in text, text
    bare("stop")


def test_a_stale_daemon_file_is_called_stale(bare, capsys, tmp_path):
    """进程被 SIGKILL 掉,文件留着 —— 说清是文件过期,而不是装作还在跑。"""
    import json as _json

    (tmp_path / "daemon.json").write_text(_json.dumps(
        {"pid": 999999, "socket": bare.socket, "bind": "127.0.0.1",
         "ttyd_port": 1, "control_port": 2}))
    assert bare("status") == cli.EXIT_FAIL
    text = out(capsys)
    assert "not running" in text and "stale" in text


# -- 参数真的递下去了 -------------------------------------------------------


def test_new_creates_and_prints_the_entrance(run, capsys):
    assert run("new", "-s", "work") == 0
    text = out(capsys)
    ttyd_port = run.argv[run.argv.index("--port") + 1]
    assert "work" in text and ("?arg=work" in text) and ttyd_port in text


def test_a_session_is_named_one_way(run, capsys):
    """`-s` 回答"哪一个",`--id` 回答"按什么认" —— 两半都要,但只有这两半。"""
    for flag in ("-s", "--id"):
        assert run("new", flag, "same-%s" % flag.strip("-"), "--", "cat") == 0
    capsys.readouterr()

    assert run("--json", "ls") == 0
    ids = {item["id"] for item in json.loads(out(capsys))}
    assert ids == {"same-s", "same-id"}


def test_help_and_version_still_exit_zero(bare):
    """argparse 用 SystemExit 表达这两件事,而它们不是错误。

    main() 契约是返回退出码,所以它得把 SystemExit 接住 —— 接住的时候
    很容易顺手把 0 也当成失败,这条就是防那个。
    """
    assert bare("--version") == cli.EXIT_OK
    assert bare("--help") == cli.EXIT_OK


@pytest.mark.parametrize("gone", ["-t", "--session", "--target"])
def test_the_old_spellings_are_gone_not_hidden(bare, gone):
    """2.0 是有意破坏兼容的(CHANGELOG.md)。

    留一个 --help 和文档里都查不到的别名,是个没人找得到的承诺 —— 不如不留。
    """
    assert bare("new", gone, "x") == cli.EXIT_USAGE


def test_help_shows_only_the_one_spelling(bare):
    """`-t` 尤其不该出现:那个字母在 tmux 里绑着 target 那套语法,而这一层没有。"""
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    for name in ("new", "send", "kill", "url", "has", "keys"):
        text = sub.choices[name].format_help()
        assert "-s ID" in text and "--id" in text, "%s 的 help 里没有 -s/--id" % name
        for stale in ("-t ", "--session", "--target"):
            assert stale not in text, "%s 的 help 里还印着 %s" % (name, stale)


def test_a_missing_id_is_a_usage_error(bare, capsys):
    assert bare("send", "x") == cli.EXIT_USAGE
    assert "needs a session: -s ID" in capsys.readouterr().err


def test_the_dash_dash_is_punctuation_not_the_command(run, capsys):
    """argparse.REMAINDER 会把 "--" 一起交回来。不剥掉就在跑命令 `-- cat`。"""
    assert run("new", "-s", "c1", "--", "sh", "-c", "echo marker; sleep 30") == 0
    capsys.readouterr()

    assert run("--json", "ls") == 0
    listed = {item["id"]: item for item in json.loads(out(capsys))}
    assert listed["c1"]["cmd"] == "sh -c 'echo marker; sleep 30'"
    assert listed["c1"]["status"] == "alive"


def test_cwd_and_env_reach_the_session(run, capsys, tmp_path):
    target = tmp_path / "sub"
    target.mkdir()
    assert run("new", "-s", "e1", "-c", str(target), "-e", "GREETING=hi",
               "--", "sh", "-c", "pwd; echo [$GREETING]; sleep 30") == 0
    capsys.readouterr()

    assert run("--json", "ls") == 0
    listed = {item["id"]: item for item in json.loads(out(capsys))}
    assert listed["e1"]["cwd"] == str(target)


def test_send_and_keys(run, capsys):
    run("new", "-s", "s1", "--", "cat")
    capsys.readouterr()

    assert run("send", "-s", "s1", "Enter the code") == 0
    assert "sent" in out(capsys)

    assert run("keys", "-s", "s1", "C-c") == 0
    capsys.readouterr()
    assert wait_until(lambda: run("has", "-s", "s1") == cli.EXIT_NO_SESSION)


def test_url_prints_just_the_address(run, capsys):
    run("new", "-s", "u")
    capsys.readouterr()

    assert run("url", "-s", "u") == 0
    ttyd_port = run.argv[run.argv.index("--port") + 1]
    assert out(capsys).strip() == "http://127.0.0.1:%s/?arg=u" % ttyd_port


def test_kill(run, capsys):
    run("new", "-s", "doomed", "--", "cat")
    capsys.readouterr()

    assert run("kill", "-s", "doomed") == 0
    assert run("has", "-s", "doomed") == cli.EXIT_NO_SESSION


def test_ls_and_its_format_string(run, capsys):
    run("new", "-s", "f1", "--", "cat")
    capsys.readouterr()

    assert run("ls") == 0
    assert "f1" in out(capsys)

    assert run("ls", "-F", "#{session_id}|#{session_status}") == 0
    assert out(capsys).strip() == "f1|alive"


def test_info_reports_both_ports(run, capsys):
    assert run("--json", "info") == 0
    info = json.loads(out(capsys))
    assert info["ttyd"]["port"] == int(run.argv[run.argv.index("--port") + 1])
    assert info["control"]["port"] == int(
        run.argv[run.argv.index("--control-port") + 1])


# -- 退出码是接口 -----------------------------------------------------------


def test_has_uses_3_for_absent_which_is_an_answer(run):
    run("new", "-s", "there", "--", "cat")
    assert run("has", "-s", "there") == 0
    assert run("has", "-s", "absent") == cli.EXIT_NO_SESSION


def test_missing_session_exits_3(run):
    assert run("send", "-s", "nope", "x") == cli.EXIT_NO_SESSION


def test_bad_id_exits_4(run):
    assert run("new", "-s", "bad:id") == cli.EXIT_STATE


def test_errors_go_to_stderr_so_stdout_stays_pipeable(run, capsys):
    run("send", "-s", "ghost", "x")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "✗" in captured.err


def test_exit_code_5_is_left_unused(bare):
    """它曾是"连不上远端 tmuxd",`-H` 去掉后没有产出者。

    不复用 —— 已经发出去的退出码不该改含义。
    """
    assert not hasattr(cli, "EXIT_UNREACHABLE")
    assert 5 not in {cli.EXIT_OK, cli.EXIT_FAIL, cli.EXIT_USAGE,
                     cli.EXIT_NO_SESSION, cli.EXIT_STATE, cli.EXIT_TMUX_GONE}


# -- kill-server 是本机动作 -------------------------------------------------


def test_kill_server_demands_the_flag(bare, capsys):
    assert bare("kill-server") == cli.EXIT_USAGE


def test_kill_server_works_without_a_server(bare, capsys):
    """收拾残局的命令,得在 server 已经挂了的时候还能用 —— 那正是你要它的时候。"""
    from tmuxd import Tmuxd

    t = Tmuxd(port=free_port(), socket=bare.socket, state_dir=bare.state_dir)
    try:
        t.session(id="doomed", cmd="cat")
        assert t.has("doomed")
    finally:
        t.close()

    assert bare("kill-server", "--tmux", "-y") == 0
    assert "untouched" in out(capsys)


# -- 配置文件 ---------------------------------------------------------------


def test_config_is_another_spelling_of_the_flags(tmp_path, monkeypatch):
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("# comment\nset -g port 23456\nset -g control-port 23457\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    for leak in ("TMUXD_PORT", "TMUXD_CONTROL_PORT"):
        monkeypatch.delenv(leak, raising=False)

    values = cli.read_config()
    assert (values["port"], values["control-port"]) == ("23456", "23457")

    args = cli.build_parser().parse_args(["ls"])
    settings = cli.Settings(args)
    assert (settings.port, settings.control_port) == (23456, 23457)


def test_command_line_beats_the_config_file(tmp_path, monkeypatch):
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("set -g control-port 23457\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    monkeypatch.delenv("TMUXD_CONTROL_PORT", raising=False)

    args = cli.build_parser().parse_args(["--control-port", "34567", "ls"])
    assert cli.Settings(args).control_port == 34567


# -- server 的生命周期 ------------------------------------------------------


def test_stop_leaves_the_sessions_running(bare, capsys):
    """`stop` 停的是门面,不是屋子。"""
    assert bare("start") == 0
    capsys.readouterr()
    try:
        assert bare("new", "-s", "held", "--", "cat") == 0
        capsys.readouterr()

        assert bare("stop") == 0
        assert "仍在运行" in out(capsys)
        assert wait_until(lambda: bare("ls") == cli.EXIT_FAIL)   # 门关了
    finally:
        record = json.loads(open(os.path.join(bare.state_dir, bare.socket,
                                              "daemon.json")).read()) \
            if os.path.exists(os.path.join(bare.state_dir, bare.socket,
                                           "daemon.json")) else None
        if record and record.get("pid"):
            try:
                os.kill(record["pid"], 15)
            except OSError:
                pass

    # 屋里的人还在:直接问 tmux,不经过已经停掉的 server
    from tmuxd import Tmuxd

    t = Tmuxd(port=free_port(), socket=bare.socket, state_dir=bare.state_dir)
    try:
        assert t.has("held")
    finally:
        t.close()


def test_start_is_idempotent(bare, capsys):
    assert bare("start") == 0
    capsys.readouterr()
    try:
        assert bare("start") == 0
        assert "already running" in out(capsys)
    finally:
        bare("stop")
        capsys.readouterr()
