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
from tmuxd.errors import TmuxMissing, TtydMissing


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


def only_on_path(monkeypatch, tmp_path, **binaries):
    """一个只有指定二进制的 PATH。"""
    bin_dir = tmp_path / "onpath"
    bin_dir.mkdir(exist_ok=True)
    made = {}
    for name, version in binaries.items():
        make = fake_tmux if name == "tmux" else fake_ttyd
        made[name] = make(bin_dir / name, version)
    monkeypatch.setenv("PATH", str(bin_dir))
    return made


# -- 不跑它的时候,一切照旧 -------------------------------------------------


def test_no_file_means_nothing_changes(tmp_path, monkeypatch):
    """这条命令是辅助不是步骤:没有 json,查找就该和以前一模一样。"""
    point_at(monkeypatch, tmp_path)          # 指向一个不存在的文件
    found = only_on_path(monkeypatch, tmp_path, ttyd="1.7.7")["ttyd"]
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    assert toolchain.read() == {}
    assert T.find_binary(state_dir=str(tmp_path / "state")) == found


def test_a_corrupt_file_reads_as_absent(tmp_path, monkeypatch):
    """读不懂就当没有 —— 一个坏文件不该是崩溃。"""
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


# -- 文件说什么就是什么 -----------------------------------------------------


def test_the_recorded_path_beats_path(tmp_path, monkeypatch):
    """写在文件里就是**指定**,所以它排在 PATH 前面。"""
    recorded = fake_ttyd(tmp_path / "recorded")
    only_on_path(monkeypatch, tmp_path, ttyd="1.7.7")
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=recorded)

    assert T.find_binary(state_dir=str(tmp_path / "state")) == recorded


def test_explicit_still_beats_the_recorded_path(tmp_path, monkeypatch):
    mine = fake_ttyd(tmp_path / "mine")
    point_at(monkeypatch, tmp_path, ttyd=fake_ttyd(tmp_path / "recorded"))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    assert T.find_binary(mine, state_dir=str(tmp_path / "state")) == mine


def test_a_broken_entry_raises_instead_of_falling_back(tmp_path, monkeypatch):
    """**不绕过去。**

    这个文件现在是人写的(install 没有 --ttyd-bin 了,指定一个二进制就靠编辑它)。
    悄悄改用 PATH 上那个,等于跑着一个和文件里写的不是同一个东西,而文件还在那儿
    声称是它 —— 比直接停下来糟。
    """
    good = only_on_path(monkeypatch, tmp_path, ttyd="1.7.7")["ttyd"]
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=str(tmp_path / "gone"))

    with pytest.raises(TtydMissing) as exc:
        T.find_binary(state_dir=str(tmp_path / "state"))
    assert str(tmp_path / "gone") in str(exc.value)
    assert good not in str(exc.value), "不该暗示它已经替你换了一个"


def test_a_too_old_recorded_ttyd_also_raises(tmp_path, monkeypatch):
    only_on_path(monkeypatch, tmp_path, ttyd="1.7.7")
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    point_at(monkeypatch, tmp_path, ttyd=fake_ttyd(tmp_path / "old", version="1.4.0"))

    with pytest.raises(TtydMissing):
        T.find_binary(state_dir=str(tmp_path / "state"))


def test_tmux_reads_the_same_file_with_the_same_rule(tmp_path, monkeypatch):
    recorded = fake_tmux(tmp_path / "recorded-tmux")
    point_at(monkeypatch, tmp_path, tmux=recorded)
    monkeypatch.delenv("TMUXD_TMUX_BIN", raising=False)
    assert M.find_binary() == recorded

    point_at(monkeypatch, tmp_path, tmux=str(tmp_path / "gone-tmux"))
    only_on_path(monkeypatch, tmp_path, tmux="3.3a")
    with pytest.raises(TmuxMissing) as exc:
        M.find_binary()
    assert "tmuxd install" in str(exc.value), "得告诉人怎么修"


# -- 下载:latest → 自带 → 报错 ---------------------------------------------


def stub_network(monkeypatch, payloads):
    """把 urlopen 换掉。要测的是那三步,不是 HTTP。"""
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
                return Response(blob)
        raise OSError("404 %s" % url)

    monkeypatch.setattr(I.urllib.request, "urlopen", fake_urlopen)


def test_it_asks_upstream_what_latest_is(monkeypatch):
    """从 /releases/latest 重定向到哪儿读版本号 —— 不用 JSON API,没有限流。"""
    class Response:
        def geturl(self):
            return "https://github.com/tsl0922/ttyd/releases/tag/1.7.9"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(I.urllib.request, "urlopen",
                        lambda url, timeout=None: Response())
    assert I.latest_version() == "1.7.9"


def test_the_checksum_comes_from_that_releases_own_sums(monkeypatch):
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  ttyd.x86_64\ncafebabe  ttyd.aarch64\n"})
    assert I.expected_checksum("1.7.6", "ttyd.x86_64") == "deadbeef"


def test_a_checksum_mismatch_installs_nothing(tmp_path, monkeypatch):
    """不给 --force。能被绕过的校验等于没有校验。"""
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(I, "latest_version", lambda: "1.7.7")
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  %s\n" % I.asset_name().encode(),
        I.asset_name(): b"not the real ttyd"})

    with pytest.raises(I.DownloadFailed) as exc:
        I.download_ttyd()
    assert "checksum mismatch" in str(exc.value)
    assert not os.path.exists(os.path.join(I.bin_dir(), "ttyd")), "坏文件留在盘上了"


def test_a_verified_download_lands_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(I, "latest_version", lambda: "1.7.7")
    blob = b'#!/bin/sh\necho "ttyd version 1.7.7"\n'
    stub_network(monkeypatch, {
        "SHA256SUMS": b"%s  %s\n" % (hashlib.sha256(blob).hexdigest().encode(),
                                     I.asset_name().encode()),
        I.asset_name(): blob})

    picked = I.download_ttyd()
    assert picked == os.path.join(I.bin_dir(), "ttyd")
    assert os.access(picked, os.X_OK)


def test_every_kind_of_trouble_ends_at_the_bundled_build(tmp_path, monkeypatch):
    """三步就是全部策略。连不上、校验和不符、下回来跑不动 —— 处理方式一样,
    因为**能做的事只有一件**。"""
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    monkeypatch.setattr(I, "latest_version", lambda: "1.7.7")
    stub_network(monkeypatch, {
        "SHA256SUMS": b"deadbeef  %s\n" % I.asset_name().encode(),
        I.asset_name(): b"not the real ttyd"})

    said = []
    picked, how = I.install_ttyd(lambda level, text: said.append((level, text)))
    assert how == "bundled"
    assert open(picked, "rb").read(4) != b"not ", "把没验过的东西装上了"
    assert any(level == "warn" for level, _ in said), "降级了却没说一声"


def test_no_download_and_no_bundled_build_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUXD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    monkeypatch.setattr(I, "download_ttyd", lambda report: (_ for _ in ()).throw(
        I.DownloadFailed("connection refused")))
    monkeypatch.setattr(I, "install_bundled", lambda report: None)

    said = []
    assert I.install_ttyd(lambda l, t: said.append((l, t))) == (None, None)
    assert any(level == "fail" for level, _ in said)


def test_a_ttyd_on_path_is_left_alone(tmp_path, monkeypatch):
    """幂等:有能用的就不下载。"""
    good = only_on_path(monkeypatch, tmp_path, ttyd="1.7.7")["ttyd"]
    monkeypatch.setattr(I, "_fetch", lambda *a, **k: pytest.fail("已经有了还去下载"))
    assert I.install_ttyd() == (good, "path")


def test_the_bundled_copy_does_not_count_as_already_installed(tmp_path, monkeypatch):
    """要是自带的算「已装好」,这条命令存在的理由(陈旧)就永远修不掉。"""
    only_on_path(monkeypatch, tmp_path)          # 空 PATH
    tried = []
    monkeypatch.setattr(I, "download_ttyd",
                        lambda report: tried.append(1) or "/x/ttyd")

    I.install_ttyd()
    assert tried, "只有自带的时候没去联网"


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
    monkeypatch.setattr(I.shutil, "which",
                        lambda name: "/usr/bin/apk" if name == "apk" else None)
    monkeypatch.setattr(I.os, "geteuid", lambda: 1000)
    assert I.tmux_install_hint() == "sudo apk add tmux"

    monkeypatch.setattr(I.os, "geteuid", lambda: 0)
    assert I.tmux_install_hint() == "apk add tmux"


def test_the_missing_tmux_error_carries_that_command(tmp_path, monkeypatch):
    """tmux 找不到的那一刻,正是那条命令最值钱的时候。"""
    only_on_path(monkeypatch, tmp_path)          # 空 PATH
    monkeypatch.delenv("TMUXD_TMUX_BIN", raising=False)
    point_at(monkeypatch, tmp_path)

    with pytest.raises(TmuxMissing) as exc:
        M.find_binary()
    assert "install" in str(exc.value)
    assert "required" in str(exc.value), "得说清 tmux 是必需的"


# -- 命令本身 ---------------------------------------------------------------


def test_install_records_what_it_found(tmp_path, monkeypatch):
    target = point_at(monkeypatch, tmp_path)
    found = only_on_path(monkeypatch, tmp_path, tmux="3.3a", ttyd="1.7.7")

    assert main(["install"]) == 0
    assert json.loads(target.read_text()) == {"tmux": found["tmux"],
                                              "ttyd": found["ttyd"]}


def test_install_leaves_an_existing_file_alone(tmp_path, monkeypatch):
    """已经配好了就只检查,不安装、不改写 —— 幂等。"""
    mine = {"tmux": fake_tmux(tmp_path / "my-tmux"),
            "ttyd": fake_ttyd(tmp_path / "my-ttyd")}
    target = point_at(monkeypatch, tmp_path, **mine)
    only_on_path(monkeypatch, tmp_path, tmux="3.3a", ttyd="1.7.7")   # 另有一套
    monkeypatch.setattr(I, "download_ttyd", lambda report: pytest.fail("不该下载"))

    assert main(["install"]) == 0
    assert json.loads(target.read_text()) == mine, "把用户配的改掉了"


def test_a_broken_entry_is_reported_not_overwritten(tmp_path, monkeypatch):
    """填错了就告诉他,让他自己改 —— 替他改掉等于替他做主。"""
    mine = {"tmux": str(tmp_path / "nope"), "ttyd": fake_ttyd(tmp_path / "my-ttyd")}
    target = point_at(monkeypatch, tmp_path, **mine)
    only_on_path(monkeypatch, tmp_path, tmux="3.3a", ttyd="1.7.7")

    assert main(["install"]) == 1
    assert json.loads(target.read_text()) == mine, "把用户填错的那行改掉了"


def test_install_never_touches_the_conf_file(tmp_path, monkeypatch):
    """`~/.tmuxd.conf` 是人写的 CLI 配置,机器只写自己的 json(works/07 §4.1)。"""
    conf = tmp_path / "tmuxd.conf"
    conf.write_text("# 我自己写的\nset -g port 9999\n")
    monkeypatch.setenv("TMUXD_CONFIG", str(conf))
    point_at(monkeypatch, tmp_path)
    only_on_path(monkeypatch, tmp_path, tmux="3.3a", ttyd="1.7.7")

    main(["install"])
    assert conf.read_text() == "# 我自己写的\nset -g port 9999\n"


def test_install_needs_no_server(tmp_path, monkeypatch):
    """你跑它,正是因为别的还起不来 —— 它不该去连管控口。"""
    point_at(monkeypatch, tmp_path)
    only_on_path(monkeypatch, tmp_path, tmux="3.3a", ttyd="1.7.7")
    monkeypatch.setattr("tmuxd.cli.Api",
                        lambda *a, **k: pytest.fail("install 去连 server 了"))
    main(["install"])


def test_install_has_no_flags(tmp_path):
    """指定二进制靠编辑 json,不靠参数 —— 两条路做同一件事就是多出来的复杂度。"""
    from tmuxd.cli import build_parser

    sub = [a for a in build_parser()._actions if getattr(a, "choices", None)][0]
    flags = {s for a in sub.choices["install"]._actions for s in a.option_strings}
    assert flags <= {"-h", "--help"}, flags


def test_install_is_in_help():
    out = subprocess.run(["python", "-m", "tmuxd", "--help"],
                         capture_output=True, text=True)
    assert "install" in out.stdout
