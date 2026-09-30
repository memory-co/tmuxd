"""ttyd_output -- ttyd must never write into a pipe nobody reads. See README.md."""
from __future__ import annotations

import os
import urllib.request

import pytest

from tests.conftest import needs_tmux, needs_ttyd

pytestmark = [needs_tmux, needs_ttyd]


def _get(port: int, timeout: float = 2.0) -> int:
    req = urllib.request.Request("http://127.0.0.1:%d/" % port)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:          # 401 from basic auth is still an answer
        return e.code


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_ttyd_output_goes_to_a_log_file_not_a_pipe(instance):
    pid = instance._ttyd.pid
    for fd in (1, 2):
        target = os.readlink("/proc/%d/fd/%d" % (pid, fd))
        assert not target.startswith("pipe:"), target
        assert target.endswith(".log") and target.startswith(instance.state_dir), target


def test_ttyd_still_answers_after_logging_more_than_a_pipe_holds(instance):
    # ~200 bytes of ttyd log per request (1.7.x), so 600 requests is ~120 KiB --
    # about twice what a pipe buffers. With a pipe, ttyd stops answering part-way.
    port = instance._ttyd.port
    for i in range(600):
        assert _get(port) in (200, 401), "ttyd stopped answering after %d requests" % i
