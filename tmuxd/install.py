"""``tmuxd install`` -- an aid, never a step (works/07-install.md).

A ready machine never runs this. ``pip install tmuxd`` on Linux, with tmux on
PATH, is already done: ``Tmuxd(...)`` works and does **not** check whether you
have been here. This module exists for the machines that are *not* ready.

Two dependencies, and upstream gives us very different material for them:

* **ttyd** publishes static musl ELFs, so this can do the whole thing --
  download, verify, drop in ``~/.tmuxd/bin/ttyd``, record it.
* **tmux** publishes a source tarball and nothing else (3.7b's only release
  asset is ``tmux-3.7b.tar.gz``). Installing it means the system package
  manager, which means root. When already root that is fine -- containers are
  the main case, and containers are this thing's home. Otherwise the command
  is *printed*: a library installed with pip does not reach for sudo.

Network beats the bundled copy, and not because the network is better. The
bundled build is frozen at wheel-build time -- Mbed TLS welded in, unfixable
by ``apt upgrade``, fixable only by a release of tmuxd (works/06 §4). Going
to upstream is how you stop waiting for us.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from . import toolchain
from . import tmux as _tmux
from . import ttyd as _ttyd

ASSETS_PATH = os.path.join(_ttyd.BUNDLED_DIR, "assets.json")

# Upstream started publishing SHA256SUMS with this release. Older ones cannot
# be verified, so they are refused rather than downloaded on trust (07 §8.1).
FIRST_VERSION_WITH_CHECKSUMS = (1, 7, 5)

RELEASES = "https://github.com/tsl0922/ttyd/releases"


def assets():
    with open(ASSETS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# -- where things go -------------------------------------------------------


def bin_dir():
    """``~/.tmuxd/bin`` -- next to, not inside, any one instance's state dir.

    An installed ttyd is a fact about the machine, so it must not live under
    ``~/.tmuxd/<socket>/``: it would be invisible to every other socket.
    """
    root = os.path.expanduser(os.environ.get("TMUXD_STATE_DIR") or "~/.tmuxd")
    return os.path.join(root, "bin")


# -- probing ---------------------------------------------------------------


def _run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stdout or proc.stderr).strip()


def tmux_version(binary):
    raw = _run([binary, "-V"])
    return raw.replace("tmux ", "") if raw else None


def ttyd_version(binary):
    raw = _run([binary, "--version"])
    return raw.replace("ttyd version ", "") if raw else None


def tmux_is_usable(path):
    raw = _run([path, "-V"])
    return bool(raw) and _tmux._parse_version(raw) >= _tmux.MIN_VERSION


# -- tmux: detect, and only install when already root -----------------------

# Ordered: the first one present is the one this machine actually uses.
PACKAGE_MANAGERS = [
    ("apt-get", ["apt-get", "install", "-y", "tmux"], "apt install tmux"),
    ("dnf", ["dnf", "install", "-y", "tmux"], "dnf install tmux"),
    ("yum", ["yum", "install", "-y", "tmux"], "yum install tmux"),
    ("zypper", ["zypper", "install", "-y", "tmux"], "zypper install tmux"),
    ("pacman", ["pacman", "-S", "--noconfirm", "tmux"], "pacman -S tmux"),
    ("apk", ["apk", "add", "tmux"], "apk add tmux"),
    ("brew", ["brew", "install", "tmux"], "brew install tmux"),
    ("pkg", ["pkg", "install", "-y", "tmux"], "pkg install tmux"),
]


def package_manager():
    """``(argv, human_command)`` for this machine, or ``None``."""
    for name, argv, human in PACKAGE_MANAGERS:
        if shutil.which(name):
            return argv, human
    return None


def tmux_install_hint():
    """One line telling the user how to get tmux here.

    Used by :class:`~tmuxd.errors.TmuxMissing` too -- the moment tmux turns
    out to be absent is the moment that command is worth having.
    """
    found = package_manager()
    if not found:
        return "install tmux with your package manager (>= %d.%d)" % _tmux.MIN_VERSION
    _, human = found
    prefix = "" if os.geteuid() == 0 else "sudo "
    return prefix + human


def install_tmux(report):
    """Run the package manager, but only when already root.

    Returns the binary path, or None. ``report(level, text)`` is how this
    speaks -- printing belongs to the CLI, not here.
    """
    found = package_manager()
    if not found:
        report("fail", "no package manager found; install tmux (>= %d.%d) by hand"
               % _tmux.MIN_VERSION)
        return None

    argv, human = found
    if os.geteuid() != 0:
        report("fail", "this machine wants: sudo %s" % human)
        report("note", "tmuxd will not escalate for you; run that, then "
                       "`tmuxd install` again")
        return None

    report("work", "running: %s" % " ".join(argv))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        report("fail", "package manager failed: %s" % exc)
        return None
    if proc.returncode:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        report("fail", "package manager exited %d%s"
               % (proc.returncode, (": " + tail[-1]) if tail else ""))
        return None

    return shutil.which("tmux")


# -- ttyd: download, verified ----------------------------------------------


class DownloadFailed(Exception):
    """Network, 404, or a checksum that did not match. All fall back."""


class Refused(DownloadFailed):
    """We *could* have fetched it and chose not to. Never falls back.

    Falling back here would hand over a different version than the one asked
    for, under a message about the network -- when the network was fine and
    the answer was policy. "I will not download something I cannot verify"
    has to end the command, not quietly substitute something else.
    """


def asset_name(machine=None):
    """Upstream names its assets ``ttyd.<arch>``.

    Architectures we ship a wheel for are in ``_ARCH``; anything else is
    guessed from ``platform.machine()`` and a 404 is allowed to be the answer.
    Guessing beats refusing here: s390x and i686 both exist upstream, and this
    command is precisely the exit for the machines no wheel covers.
    """
    machine = (machine or platform.machine()).lower()
    return _ttyd._ARCH.get(machine) or "ttyd.%s" % machine


def _fetch(url, timeout=120):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DownloadFailed("%s (%s)" % (url, getattr(exc, "reason", exc)))


def _version_tuple(text):
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0, 0])[:3]


def expected_checksum(version, asset, manifest):
    """From the manifest when it is the pinned version, else from upstream.

    The manifest is stronger: it is in the repository, reviewed, and the
    binary that shipped in the wheel was checked against it. Upstream's
    ``SHA256SUMS`` is the fallback for a version the user asked for by name.
    """
    if version == manifest["ttyd_version"]:
        for target in manifest["targets"]:
            if target["asset"] == asset:
                return target["sha256"]

    if _version_tuple(version) < FIRST_VERSION_WITH_CHECKSUMS:
        raise Refused(
            "ttyd %s predates upstream's SHA256SUMS (first published in "
            "%d.%d.%d), so the download cannot be verified -- refusing. "
            "There is no option to download it unverified."
            % ((version,) + FIRST_VERSION_WITH_CHECKSUMS))

    sums = _fetch("%s/download/%s/SHA256SUMS" % (RELEASES, version)).decode(
        "utf-8", "replace")
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            return parts[0]
    raise DownloadFailed("upstream's SHA256SUMS for %s does not list %s"
                         % (version, asset))


def download_ttyd(version=None, report=lambda *a: None):
    """Fetch, verify, install into ``bin_dir()``. Returns the path.

    Raises :class:`DownloadFailed` for anything -- no network, no such asset,
    checksum mismatch. Every one of those is a fall-back-to-bundled, and a
    mismatch is never installable: a check with a ``--force`` is not a check.
    """
    manifest = assets()
    version = str(version or manifest["ttyd_version"])
    asset = asset_name()
    want = expected_checksum(version, asset, manifest)

    url = "%s/download/%s/%s" % (RELEASES, version, asset)
    report("work", "downloading %s" % url)
    blob = _fetch(url)
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise Refused(
            "checksum mismatch, discarded\n"
            "         expected %s\n         got      %s\n"
            "         this could be a hijacked download or a changed upstream "
            "asset -- it will not be installed." % (want, got))

    target_dir = bin_dir()
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "ttyd")
    tmp = "%s.tmp.%d" % (target, os.getpid())
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.chmod(tmp, 0o755)
    os.replace(tmp, target)

    if not _ttyd.is_usable(target):
        os.unlink(target)
        raise DownloadFailed(
            "%s downloaded and verified, but will not run here (wrong "
            "architecture?)" % asset)
    return target


def install_bundled(report):
    """The copy that shipped in the wheel, made runnable. None if there is none."""
    source = _ttyd.bundled_binary()
    if not source:
        return None
    target_dir = bin_dir()
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "ttyd")
    shutil.copyfile(source, target)
    os.chmod(target, 0o755)
    if not _ttyd.is_usable(target):
        os.unlink(target)
        report("fail", "the bundled build will not run on this machine")
        return None
    return target


def install_ttyd(version=None, refresh=False, report=lambda *a: None):
    """Network first, the bundled copy second (works/07 §3).

    ``refresh`` skips the "already have one" shortcut. Note what counts as
    already having one: explicit, recorded, or on PATH -- **not** the bundled
    copy. If the bundled build were treated as done, the staleness this
    command exists to fix would never get fixed.
    """
    if not refresh:
        recorded = toolchain.read().get("ttyd")
        if recorded and _ttyd.is_usable(recorded):
            return recorded, "recorded"
        on_path = shutil.which("ttyd")
        if on_path and _ttyd.is_usable(on_path):
            return on_path, "path"

    try:
        return download_ttyd(version, report), "download"
    except Refused:
        # Policy, not weather. Substituting another build here would answer a
        # question the user did not ask.
        raise
    except DownloadFailed as exc:
        report("warn", "download failed: %s" % exc)

    fallback = install_bundled(report)
    if fallback:
        report("note", "using the build bundled in the wheel; "
                       "`tmuxd install --refresh` to try upstream again")
        return fallback, "bundled"

    report("fail",
           "no download and no bundled build for %s/%s\n"
           "         by hand: fetch %s from %s,\n"
           "                  then `tmuxd install --ttyd-bin /path/to/ttyd`"
           % (sys.platform, platform.machine(), asset_name(), RELEASES))
    return None, None
