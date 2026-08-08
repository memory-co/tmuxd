"""finding_ttyd — 三级查找. See README.md."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import stat
import subprocess
import sys

import pytest

from tests.conftest import free_port, kill_pool, needs_tmux, pool_name
from tmuxd import Tmuxd, TtydMissing
from tmuxd import ttyd as T


def fake_ttyd(path, version="1.7.7", exit_code=0):
    """一个会报版本号的假 ttyd —— 要测的是查找顺序和版本判定,不是 ttyd 本身。"""
    path.write_text('#!/bin/sh\necho "ttyd version %s"\nexit %d\n' % (version, exit_code))
    path.chmod(0o755)
    return str(path)


# -- 包里带了什么 -----------------------------------------------------------


# 二进制不进 git(每次上游发版都会换,而 git 永远留着旧的),
# 所以"真相"在清单里:版本号 + 校验和 + 架构到 wheel tag 的映射。
ASSETS = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ttyd_assets.json")
    .read_text())


def test_the_manifest_pins_a_version_and_a_checksum_for_every_target():
    assert ASSETS["ttyd_version"]
    assert ASSETS["targets"], "清单里一个目标都没有"
    for target in ASSETS["targets"]:
        assert len(target["sha256"]) == 64, target["asset"]
        assert target["tags"], target["asset"]


def test_every_manifest_target_maps_to_an_arch_the_code_knows():
    """清单里有的架构,查找那边必须认得 —— 否则 wheel 发出去了却选不中。"""
    known = set(T._ARCH.values())
    for target in ASSETS["targets"]:
        assert target["asset"] in known, target["asset"]


def test_manylinux_and_musllinux_share_one_artifact():
    """上游是静态 musl 构建,不依赖任一 libc,所以同一个 wheel 可以两个 tag 都声明。"""
    for target in ASSETS["targets"]:
        libcs = {tag.split("_")[0] for tag in target["tags"]}
        assert libcs == {"manylinux", "musllinux"}, target["tags"]


@pytest.mark.skipif(not os.path.exists(os.path.join(T.BUNDLED_DIR, "LICENSE")),
                    reason="run scripts/fetch_ttyd.py first")
def test_the_licence_ships_alongside_the_binary():
    """ttyd 是 MIT,再分发必须带上它的 LICENSE —— 谁分发谁负责。"""
    licence = open(os.path.join(T.BUNDLED_DIR, "LICENSE"), encoding="utf-8").read()
    assert "MIT" in licence


def test_whatever_binary_is_present_matches_upstream():
    """开发树里那份(fetch_ttyd.py 拉的)必须和上游校验和一致。

    CI 的 release 流程也按同一份清单校验,所以这条和发版走的是同一个判据。
    """
    present = [n for n in os.listdir(T.BUNDLED_DIR) if n.startswith("ttyd.")]
    if not present:
        pytest.skip("no binary in the tree; run scripts/fetch_ttyd.py")

    expected = {t["asset"]: t["sha256"] for t in ASSETS["targets"]}
    for name in present:
        blob = open(os.path.join(T.BUNDLED_DIR, name), "rb").read()
        assert hashlib.sha256(blob).hexdigest() == expected[name], name


def test_the_selector_knows_this_platform():
    """选得中,是"装了平台 wheel 就能用"的前提。

    树里没有二进制时(刚 checkout 出来就是这样)只能验到映射这一半 ——
    验不了的事不假装验了。
    """
    machine = platform.machine().lower()
    if not sys.platform.startswith("linux"):
        assert T.bundled_binary() is None      # macOS:上游没有 Darwin 产物
        return

    if machine not in T._ARCH:
        assert T.bundled_binary() is None      # 冷门架构:报错里会说清楚
        return

    expected = os.path.join(T.BUNDLED_DIR, T._ARCH[machine])
    if os.path.exists(expected):
        assert T.bundled_binary() == expected
    else:
        assert T.bundled_binary() is None      # 没 fetch 过,不该凭空指一个


# -- 三级顺序 ---------------------------------------------------------------


def test_path_wins_over_the_bundled_copy(tmp_path, monkeypatch):
    """系统装的那个能被 apt upgrade 修,自带的不能。所以 PATH 优先。"""
    found = fake_ttyd(tmp_path / "ttyd")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    assert T.find_binary(state_dir=str(tmp_path / "state")) == found


def test_the_bundled_copy_answers_when_path_has_none(tmp_path, monkeypatch):
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    picked = T.find_binary(state_dir=str(tmp_path / "state"))
    assert picked == str(tmp_path / "state" / "bin" / "ttyd")


def test_an_old_ttyd_on_path_steps_aside(tmp_path, monkeypatch):
    """降级,不是报错 —— 这正是 PATH 优先能成立的前提。"""
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    fake_ttyd(tmp_path / "ttyd", version="1.4.0")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    told = []
    picked = T.find_binary(state_dir=str(tmp_path / "state"), on_fallback=told.append)
    assert picked.endswith("/bin/ttyd")
    assert told, "退到自带的时候没有说一声"


def test_an_explicit_binary_is_never_swapped_out(tmp_path, monkeypatch):
    """你点名了一个,它坏了就该报错 —— 换一个会让你以为跑的是你指定的那份。"""
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    stale = fake_ttyd(tmp_path / "old-ttyd", version="1.2.0")

    with pytest.raises(TtydMissing) as exc:
        T.find_binary(stale, state_dir=str(tmp_path / "state"))
    assert "never swapped out" in str(exc.value)


def test_env_var_counts_as_explicit(tmp_path, monkeypatch):
    good = fake_ttyd(tmp_path / "mine")
    monkeypatch.setenv("TMUXD_TTYD_BIN", good)
    assert T.find_binary(state_dir=str(tmp_path / "state")) == good


# -- 可执行位:装完之后才会炸的那种 -----------------------------------------


def test_the_bundled_copy_is_made_executable(tmp_path, monkeypatch):
    """package data 过 wheel 之后可执行位会丢,而且 site-packages 可能只读。

    不复制+chmod 的话,开发树里"看起来能用",pip 装完才炸。
    """
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    picked = T.find_binary(state_dir=str(tmp_path / "state"))
    assert os.stat(picked).st_mode & stat.S_IXUSR
    assert os.path.dirname(picked) != T.BUNDLED_DIR, "不该就地执行 package data"

    out = subprocess.run([picked, "--version"], capture_output=True, text=True)
    assert "ttyd" in (out.stdout + out.stderr)


# -- 报错要能照着做 ---------------------------------------------------------


def test_the_error_says_what_to_do(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)
    monkeypatch.setattr(T, "bundled_binary", lambda: None)   # 假装是 macOS

    with pytest.raises(TtydMissing) as exc:
        T.find_binary(state_dir=str(tmp_path / "state"))
    message = str(exc.value)
    assert "brew install ttyd" in message
    assert "releases" in message
    assert "required" in message, "得说清 ttyd 是必需的,不是可选的"
    for name in T.bundled_names():
        assert name in message, "得告诉人包里到底带了哪些架构"


# -- 真的用起来 -------------------------------------------------------------


@needs_tmux
def test_a_tmuxd_can_run_on_the_bundled_ttyd(tmp_path, request, monkeypatch):
    """端到端:把 PATH 清空,整个实例靠自带那份跑起来。"""
    if not T.bundled_binary():
        pytest.skip("no bundled build for this platform")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", "%s:%s" % (empty, os.path.dirname(_tmux_bin())))
    monkeypatch.delenv("TMUXD_TTYD_BIN", raising=False)

    name = pool_name(request, prefix="bundled")
    t = Tmuxd(port=free_port(), socket=name, state_dir=str(tmp_path))
    try:
        assert t.ttyd_is_bundled
        assert t.info()["ttyd"]["source"] == "bundled"
        s = t.session(id="on-bundled", cmd="cat")
        assert s.alive and s.url.endswith("/?arg=on-bundled")
    finally:
        t.kill_tmux_server()
        t.close()
        kill_pool(name)


def _tmux_bin():
    import shutil

    return shutil.which("tmux") or "/usr/bin/tmux"
