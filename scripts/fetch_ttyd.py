#!/usr/bin/env python3
"""Put a ttyd binary in the source tree, for working on the bundling locally.

    python scripts/fetch_ttyd.py

Release wheels do not come from here -- ``build_wheels.py`` fetches per target.
This exists so the bundled-fallback path can be exercised on a dev machine, and
so ``tests/finding_ttyd`` has something real to check.

**The binaries are not committed.** A git repository keeps every blob forever,
and these are 1.3 MB each and get replaced whenever upstream cuts a release;
the manifest (version plus checksums) is the part worth versioning.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import platform
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = json.loads((ROOT / "tmuxd" / "data" / "ttyd" / "assets.json").read_text())
DATA = ROOT / "tmuxd" / "data" / "ttyd"

MACHINE = {
    "x86_64": "ttyd.x86_64", "amd64": "ttyd.x86_64",
    "aarch64": "ttyd.aarch64", "arm64": "ttyd.aarch64",
    "armv7l": "ttyd.arm", "armv6l": "ttyd.arm", "arm": "ttyd.arm",
}


def main():
    if not sys.platform.startswith("linux"):
        raise SystemExit(
            "upstream ships no macOS build (ten musl ELFs and one win32.exe, "
            "nothing for Darwin) -- use `brew install ttyd`")

    wanted = MACHINE.get(platform.machine().lower())
    if not wanted:
        raise SystemExit("no upstream asset for %s" % platform.machine())

    target = next(t for t in ASSETS["targets"] if t["asset"] == wanted)
    DATA.mkdir(parents=True, exist_ok=True)

    blob = urllib.request.urlopen(
        "%s/%s" % (ASSETS["base_url"], wanted), timeout=120).read()
    got = hashlib.sha256(blob).hexdigest()
    if got != target["sha256"]:
        raise SystemExit("checksum mismatch\n  expected %s\n  got      %s"
                         % (target["sha256"], got))

    (DATA / wanted).write_bytes(blob)
    (DATA / wanted).chmod(0o755)
    (DATA / "SHA256SUMS").write_text("%s  %s\n" % (target["sha256"], wanted))
    (DATA / "LICENSE").write_bytes(
        urllib.request.urlopen(ASSETS["license_url"], timeout=60).read())

    print("%s  (ttyd %s, sha256 verified)" % (DATA / wanted, ASSETS["ttyd_version"]))


if __name__ == "__main__":
    main()
