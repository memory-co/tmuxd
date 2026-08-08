#!/usr/bin/env python3
"""Build the sdist and one wheel per platform.

Nothing here is compiled. The per-platform difference is a single static
binary, so the whole matrix is built on one runner: take the plain wheel,
drop the right ttyd in, retag, repack. No cross-compilers, no QEMU, no
cibuildwheel.

    python scripts/build_wheels.py            # everything, into dist/
    python scripts/build_wheels.py --pure     # just the sdist and any-wheel

What comes out:

    tmuxd-X-py3-none-any.whl                          no ttyd  <- macOS et al
    tmuxd-X-py3-none-manylinux_..._x86_64.mus...whl   ttyd.x86_64
    tmuxd-X-py3-none-manylinux_..._aarch64.mu...whl   ttyd.aarch64
    tmuxd-X-py3-none-manylinux_..._armv7l.mus...whl   ttyd.arm
    tmuxd-X.tar.gz

The any-wheel is deliberate, not a leftover. pip prefers the most specific
wheel it can use, so a platform we ship a binary for gets one, and everything
else -- macOS, s390x, a Raspberry Pi we did not think of -- still installs and
uses a ttyd from PATH. Publishing only platform wheels would turn "you need to
install ttyd" into "there is no wheel for you".

manylinux and musllinux share one file: upstream's builds are statically
linked against musl, so they depend on no libc at all, and one wheel can
carry both tags.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = json.loads((ROOT / "scripts" / "ttyd_assets.json").read_text())
DATA = "tmuxd/data/ttyd"


def log(message):
    print("==> %s" % message, flush=True)


# -- fetching --------------------------------------------------------------


def fetch(url, expect_sha256=None):
    log("fetch %s" % url)
    blob = urllib.request.urlopen(url, timeout=120).read()
    if expect_sha256:
        got = hashlib.sha256(blob).hexdigest()
        if got != expect_sha256:
            raise SystemExit(
                "checksum mismatch for %s\n  expected %s\n  got      %s"
                % (url, expect_sha256, got))
    return blob


def fetch_all():
    """Every asset plus upstream's licence, verified against the manifest."""
    out = {"LICENSE": fetch(ASSETS["license_url"])}
    for target in ASSETS["targets"]:
        out[target["asset"]] = fetch(
            "%s/%s" % (ASSETS["base_url"], target["asset"]), target["sha256"])
    return out


# -- wheel surgery ---------------------------------------------------------


def _urlsafe_b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def retag(base_wheel, tags, extra_files, dest):
    """Copy a wheel, add files, replace its platform tags, fix RECORD."""
    with zipfile.ZipFile(base_wheel) as zf:
        names = zf.namelist()
        blobs = {name: zf.read(name) for name in names}

    dist_info = next(n.split("/")[0] for n in names if n.endswith(".dist-info/WHEEL"))
    blobs.update(extra_files)

    # WHEEL carries the tags; the filename must agree with it.
    wheel_path = "%s/WHEEL" % dist_info
    kept = [line for line in blobs[wheel_path].decode().splitlines()
            if not line.startswith("Tag:")]
    kept += ["Tag: py3-none-%s" % tag for tag in tags]
    blobs[wheel_path] = ("\n".join(kept) + "\n").encode()

    # RECORD lists every other file with its hash and size, so it is written last.
    record_path = "%s/RECORD" % dist_info
    rows = []
    for name in sorted(n for n in blobs if n != record_path):
        blob = blobs[name]
        rows.append([name, "sha256=" + _urlsafe_b64(hashlib.sha256(blob).digest()),
                     str(len(blob))])
    rows.append([record_path, "", ""])
    buffer = __import__("io").StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    blobs[record_path] = buffer.getvalue().encode()

    name, version = dist_info[: -len(".dist-info")].split("-")
    out = dest / ("%s-%s-py3-none-%s.whl" % (name, version, ".".join(tags)))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(blobs):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            # Executable bit inside the archive is best-effort: installers do not
            # have to preserve it, which is why the library copies the binary out
            # and chmods it before use (works/06 §3.4).
            info.external_attr = (0o755 if path.endswith(("x86_64", "aarch64", "arm"))
                                  else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, blobs[path])
    return out


# -- driver ----------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pure", action="store_true",
                        help="only the sdist and the any-wheel (no downloads)")
    parser.add_argument("--dest", default="dist")
    args = parser.parse_args()

    dest = ROOT / args.dest
    if dest.exists():
        shutil.rmtree(dest)
    for stale in ROOT.glob("*.egg-info"):
        shutil.rmtree(stale)

    # The tree must not carry binaries, or the any-wheel would stop being pure.
    for stray in (ROOT / DATA).glob("ttyd.*"):
        raise SystemExit("%s is in the tree; the any-wheel must not bundle a binary"
                         % stray)

    log("build sdist + any-wheel")
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(dest)],
                   cwd=ROOT, check=True)
    base = next(dest.glob("*-py3-none-any.whl"))

    if args.pure:
        log("pure build only: %s" % ", ".join(p.name for p in sorted(dest.iterdir())))
        return

    payload = fetch_all()
    for target in ASSETS["targets"]:
        asset = target["asset"]
        extra = {
            "%s/%s" % (DATA, asset): payload[asset],
            "%s/LICENSE" % DATA: payload["LICENSE"],
            "%s/SHA256SUMS" % DATA:
                ("%s  %s\n" % (target["sha256"], asset)).encode(),
        }
        out = retag(base, target["tags"], extra, dest)
        log("%s  <- %s (%.1f MB)" % (out.name, asset, out.stat().st_size / 1e6))

    log("artifacts:")
    for path in sorted(dest.iterdir()):
        print("    %-58s %6.1f MB" % (path.name, path.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
