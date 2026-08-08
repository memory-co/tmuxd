"""installing —— `tmuxd install` 与 `~/.tmuxd.json`. See README.md."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from tmuxd import install as I
from tmuxd import tmux as M
from tmuxd import toolchain
from tmuxd import ttyd as T
from tmuxd.cli import main


def fake_ttyd(path, version="1.7.7"):
    path.write_text('#!/bin/sh\necho "ttyd version %s"\n' % version)
    path.chmod(0o755)
    return str(path)


def fake_tmux(path, version="3.3a"):
    path.write_text('#!/bin/sh\necho "tmux %s"\n' % version)
    path.chmod(0o755)
    return str(path)


def point_at(monkeypatch, tmp_path, **values):
    """把 TMUXD_JSON 指到用例自己的文件上,可选地先写点内容进去。"""
    target = tmp_path / "tmuxd.json"
    monkeypatch.setenv("TMUXD_JSON", str(target))
    if values:
        target.write_text(json.dumps(values))
    return target


# -- 不跑它的时候,一切照旧 -------------------------------------------------


def test_no_file_means_nothing_changes(tmp_path, monkeypatch):
    """这条命令是辅助不是步骤:没有 json,查找就该和以前一模一样。"""
    point_at(monkeypatch, tmp_path)          # 指向一个不存在的文件
    found = fake_ttyd(tmp_path / "ttyd")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    assert toolchain.read() == {}
    assert T.find_binary(state_dir=str(tmp_path / "state")) == found


def test_a_corrupt_file_reads_as_absent(tmp_path, monkeypatch):
    """它是上一次查找的缓存,不是真相。坏了该退回去找,不是崩。"""
    target = point_at(monkeypatch, tmp_path)
    target.write_text("{ this is not json")
    assert toolchain.read() == {}

    target.write_text('["a", "b"]')          # 合法 json,但不是对象
    assert toolchain.read() == {}


# -- 只有两个键 -------------------------------------------------------------


def test_the_file_holds_exactly_two_keys(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    toolchain.write(tmux="/usr/bin/tmux", ttyd="/opt/ttyd")
    assert set(json.loads((tmp_path / "tmuxd.json").read_text())) == {"tmux", "ttyd"}


def test_a_third_key_is_refused(tmp_path, monkeypatch):
    """端口是行为,不是机器事实 —— 允许它进来,库就不敢默认读这个文件了
    (works/07 §5)。"""
    point_at(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as exc:
        toolchain.write(tmux="/usr/bin/tmux", port="7681")
    assert "port" in str(exc.value)


def test_unknown_keys_already_in_the_file_are_ignored(tmp_path, monkeypatch):
    """手写进去一个 port 也不该生效 —— 读的时候就滤掉了。"""
    point_at(monkeypatch, tmp_path, tmux="/usr/bin/tmux", port=9999, token="x")
    assert toolchain.read() == {"tmux": "/usr/bin/tmux"}


def test_writing_none_drops_the_key(tmp_path, monkeypatch):
    """这次没找到,就该说没有 —— 留着上次那条陈的更糟。"""
    point_at(monkeypatch, tmp_path, tmux="/usr/bin/tmux", ttyd="/opt/ttyd")
    toolchain.write(ttyd=None)
    assert toolchain.read() == {"tmux": "/usr/bin/tmux"}


# -- 它在查找顺序里的位置 ---------------------------------------------------


def test_the_recorded_path_beats_path(tmp_path, monkeypatch):
    """跑过 install 就是表达了"用这一份",所以它排在 PATH 前面。"""
    recorded = fake_ttyd(tmp_path / "recorded")
    on_path = tmp_path / "bin"
    on_path.mkdir()
    fake_ttyd(on_path / "ttyd")
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=recorded)

    assert T.find_binary(state_dir=str(tmp_path / "state")) == recorded


def test_explicit_still_beats_the_recorded_path(tmp_path, monkeypatch):
    mine = fake_ttyd(tmp_path / "mine")
    point_at(monkeypatch, tmp_path, ttyd=fake_ttyd(tmp_path / "recorded"))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    assert T.find_binary(mine, state_dir=str(tmp_path / "state")) == mine


def test_a_stale_entry_falls_through_instead_of_raising(tmp_path, monkeypatch):
    """二进制被删了、被升级搬走了 —— 一个过期的缓存文件不该让本来能跑的机器跑不起来。"""
    on_path = tmp_path / "bin"
    on_path.mkdir()
    good = fake_ttyd(on_path / "ttyd")
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=str(tmp_path / "gone"))

    told = []
    picked = T.find_binary(state_dir=str(tmp_path / "state"), on_stale=told.append)
    assert picked == good
    assert told == [str(tmp_path / "gone")], "降级了却没说一声"


def test_a_too_old_recorded_ttyd_also_falls_through(tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    good = fake_ttyd(on_path / "ttyd")
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=fake_ttyd(tmp_path / "old", version="1.4.0"))

    assert T.find_binary(state_dir=str(tmp_path / "state")) == good


def test_tmux_reads_the_same_file(tmp_path, monkeypatch):
    recorded = fake_tmux(tmp_path / "recorded-tmux")
    point_at(monkeypatch, tmp_path, tmux=recorded)
    monkeypatch.delenv("TMUXD_TMUX_BIN", raising=False)

    assert M.find_binary() == recorded


def test_a_stale_tmux_entry_falls_through_too(tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    good = fake_tmux(on_path / "tmux")
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.delenv("TMUXD_TMUX_BIN", raising=False)
    point_at(monkeypatch, tmp_path, tmux=str(tmp_path / "gone-tmux"))

    told = []
    assert M.find_binary(on_stale=told.append) == good
    assert told


# -- 下载:验、降级、拒绝 ---------------------------------------------------


def stub_network(monkeypatch, payloads):
    """把 urlopen 换掉。要测的是三条决策,不是 HTTP。"""
    class Response:
        def __init__(self, blob):
            self._blob = blob

        def read(self):
            return self._blob

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        for suffix, blob in payloads.items():
            if url.endswith(suffix):
                if isinstance(blob, Exception):
                    raise blob
                return Response(blob)
        raise OSError("404 %s" % url)

    monkeypatch.setattr(I.urllib.request, "urlopen", fake_urlopen)


def latest(monkeypatch, version="1.7.7"):
    monkeypatch.setattr(I, "latest_version", lambda: version)
    return version


def test_it_asks_upstream_what_latest_is(monkeypatch):
    """从 /releases/latest 重定向到哪儿读版本号 —— 不用 JSON API,没有限流。"""
    class Response:
        def geturl(self):
            return "https://github.com/tsl0922/ttyd/releases/tag/1.7.9"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(I.urllib.request, "urlopen", lambda url, timeout=None: Response())
    assert I.latest_version() == "1.7.9"


def test_the_checksum_comes_from_that_releases_own_sums(monkeypatch):
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  ttyd.x86_64\ncafebabe  ttyd.aarch64\n"})
    assert I.expected_checksum("1.7.6", "ttyd.x86_64") == "deadbeef"


def test_a_checksum_mismatch_installs_nothing(tmp_path, monkeypatch):
    """不给 --force。能被绕过的校验等于没有校验(works/07 §9)。"""
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    latest(monkeypatch)
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  %s\n" % I.asset_name().encode(),
        I.asset_name(): b"not the real ttyd"})

    with pytest.raises(I.DownloadFailed) as exc:
        I.download_ttyd()
    assert "checksum mismatch" in str(exc.value)
    assert not os.path.exists(os.path.join(I.bin_dir(), "ttyd")), "坏文件留在盘上了"


def test_download_falls_back_to_the_bundled_build(tmp_path, monkeypatch):
    """网络不通不是终点 —— 包里那份还在,而且必须说一声(works/07 §3)。"""
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(I, "download_ttyd", lambda report: (_ for _ in ()).throw(
        I.DownloadFailed("connection refused")))
    monkeypatch.setattr(I.shutil, "which", lambda name: None)

    said = []
    picked, how = I.install_ttyd(report=lambda level, text: said.append((level, text)))
    assert how == "bundled"
    assert picked == os.path.join(I.bin_dir(), "ttyd")
    assert os.access(picked, os.X_OK)
    assert any(level == "warn" for level, _ in said), "降级了却没说一声"


def test_every_kind_of_trouble_falls_back_the_same_way(tmp_path, monkeypatch):
    """三步就是全部策略:latest → 自带 → 报错。

    校验和不符、连不上、下回来跑不动 —— 处理方式必须一样,因为**能做的事只有一件**。
    早先的稿子给"拒绝"单开了一条不兜底的路,那是为版本指定服务的;版本指定砍掉之后,
    那条路只剩下复杂度。
    """
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    latest(monkeypatch)
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  %s\n" % I.asset_name().encode(),
        I.asset_name(): b"not the real ttyd"})

    picked, how = I.install_ttyd()
    assert how == "bundled"
    assert open(picked, "rb").read(4) != b"not ", "把没验过的东西装上了"


def test_no_download_and_no_bundled_build_is_an_error(tmp_path, monkeypatch):
    """最后一步:两条路都断了就报错,不装半个东西。"""
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    monkeypatch.setattr(I, "download_ttyd", lambda report: (_ for _ in ()).throw(
        I.DownloadFailed("connection refused")))
    monkeypatch.setattr(I, "install_bundled", lambda report: None)

    said = []
    assert I.install_ttyd(report=lambda l, t: said.append((l, t))) == (None, None)
    assert any(level == "fail" for level, _ in said)


def test_an_already_usable_ttyd_is_left_alone(tmp_path, monkeypatch):
    """幂等:装好了就什么都不做(works/07 §7)。"""
    on_path = tmp_path / "bin"
    on_path.mkdir()
    good = fake_ttyd(on_path / "ttyd")
    monkeypatch.setenv("PATH", str(on_path))
    point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(I, "_fetch", lambda *a, **k: pytest.fail("已经有了还去下载"))

    picked, how = I.install_ttyd()
    assert (picked, how) == (good, "path")


def test_refresh_downloads_anyway(tmp_path, monkeypatch):
    on_path = tmp_path / "bin"
    on_path.mkdir()
    fake_ttyd(on_path / "ttyd")
    monkeypatch.setenv("PATH", str(on_path))
    point_at(monkeypatch, tmp_path)
    tried = []
    monkeypatch.setattr(I, "download_ttyd",
                        lambda report: tried.append(1) or "/x/ttyd")

    assert I.install_ttyd(refresh=True)[1] == "download"
    assert tried


def test_the_bundled_copy_does_not_count_as_already_installed(tmp_path, monkeypatch):
    """要是自带的算"已装好",这条命令存在的理由(陈旧)就永远修不掉。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    point_at(monkeypatch, tmp_path)
    tried = []
    monkeypatch.setattr(I, "download_ttyd",
                        lambda report: tried.append(1) or "/x/ttyd")

    I.install_ttyd()
    assert tried, "只有自带的时候没去联网"


def test_a_verified_download_lands_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    latest(monkeypatch)
    blob = b"#!/bin/sh\necho \"ttyd version 1.7.7\"\n"
    digest = hashlib.sha256(blob).hexdigest().encode()
    stub_network(monkeypatch, {
        "SHA256SUMS": b"%s  %s\n" % (digest, I.asset_name().encode()),
        I.asset_name(): blob})

    picked = I.download_ttyd()
    assert picked == os.path.join(I.bin_dir(), "ttyd")
    assert os.access(picked, os.X_OK)


def test_there_is_no_way_to_ask_for_a_version(tmp_path):
    """版本指定砍掉了 —— 它带来的每一条分支都是为一个没人提的需求服务的。"""
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    flags = {s for a in sub.choices["install"]._actions for s in a.option_strings}
    assert "--ttyd-version" not in flags
    assert not hasattr(I, "Refused"), "没有版本指定,就不需要「拒绝」这个概念了"


# -- tmux:探测,不提权 -----------------------------------------------------


def test_tmux_is_never_installed_without_root(monkeypatch):
    """一个 pip 装来的库自作主张提权,是不能接受的(works/07 §2)。"""
    monkeypatch.setattr(I.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(I, "package_manager",
                        lambda: (["apt-get", "install", "-y", "tmux"], "apt install tmux"))
    ran = []
    monkeypatch.setattr(I.subprocess, "run", lambda *a, **k: ran.append(a))

    said = []
    assert I.install_tmux(lambda level, text: said.append((level, text))) is None
    assert not ran, "没 root 却真去跑包管理器了"
    assert any("sudo apt install tmux" in text for _, text in said)


def test_the_hint_names_this_machines_package_manager(monkeypatch):
    monkeypatch.setattr(I.shutil, "which", lambda name: "/usr/bin/apk" if name == "apk" else None)
    monkeypatch.setattr(I.os, "geteuid", lambda: 1000)
    assert I.tmux_install_hint() == "sudo apk add tmux"

    monkeypatch.setattr(I.os, "geteuid", lambda: 0)
    assert I.tmux_install_hint() == "apk add tmux"


def test_the_missing_tmux_error_carries_that_command(tmp_path, monkeypatch):
    """tmux 找不到的那一刻,正是那条命令最值钱的时候。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TMUXD_TMUX_BIN", raising=False)
    point_at(monkeypatch, tmp_path)

    with pytest.raises(Exception) as exc:
        M.find_binary()
    message = str(exc.value)
    assert "install" in message
    assert "required" in message, "得说清 tmux 是必需的"


# -- 命令本身 ---------------------------------------------------------------


def test_install_records_what_it_found(tmp_path, monkeypatch, capsys):
    target = point_at(monkeypatch, tmp_path)
    tmux = fake_tmux(tmp_path / "tmux")
    ttyd = fake_ttyd(tmp_path / "ttyd")

    assert main(["install", "--tmux-bin", tmux, "--ttyd-bin", ttyd]) == 0
    assert json.loads(target.read_text()) == {"tmux": tmux, "ttyd": ttyd}


def test_install_never_touches_the_conf_file(tmp_path, monkeypatch):
    """`~/.tmuxd.conf` 是人写的 CLI 配置,机器只写自己的 json(works/07 §4.1)。"""
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("# 我自己写的\nset -g port 9999\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    point_at(monkeypatch, tmp_path)

    main(["install", "--tmux-bin", fake_tmux(tmp_path / "tmux"),
          "--ttyd-bin", fake_ttyd(tmp_path / "ttyd")])
    assert conf.read_text() == "# 我自己写的\nset -g port 9999\n"


def test_install_needs_no_server(tmp_path, monkeypatch):
    """你跑它,正是因为别的还起不来 —— 它不该去连管控口。"""
    point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tmuxd.cli.Api",
        lambda *a, **k: pytest.fail("install 去连 server 了"))

    main(["install", "--tmux-bin", fake_tmux(tmp_path / "tmux"),
          "--ttyd-bin", fake_ttyd(tmp_path / "ttyd")])


def test_install_is_in_help(tmp_path):
    out = subprocess.run(["python", "-m", "tmuxd", "--help"],
                         capture_output=True, text=True)
    assert "install" in out.stdout
