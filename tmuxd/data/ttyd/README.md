# Bundled ttyd

Upstream release binaries, unmodified, from
<https://github.com/tsl0922/ttyd/releases/tag/1.7.7>.
Statically linked against musl, so they run on any Linux of the same
architecture without further dependencies.

    ttyd.x86_64   most servers and desktops
    ttyd.arm      32-bit ARM

`SHA256SUMS` is upstream's, trimmed to the two files shipped here; the
checksums were verified before they were committed.

**These are a fallback.** A ttyd already on `PATH` wins, because that one can be
fixed by `apt upgrade` and this one can only be fixed by us shipping a release
(docs/v1/works/06-dependencies.md §3.1, §4).

**No macOS build.** Upstream ships ten musl ELFs and one win32.exe and nothing
for Darwin, which wants Mach-O. On macOS: `brew install ttyd`.

ttyd is MIT licensed; see LICENSE in this directory.
