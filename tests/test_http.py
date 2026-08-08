import json
import urllib.error
import urllib.request

import pytest

from conftest import free_port, needs_tmux
from tmuxd.remote import RemoteTmuxd

pytestmark = needs_tmux

TOKEN = "s3cret"


@pytest.fixture
def api(instance):
    port = free_port()
    shell = instance.serve_http(port, token=TOKEN)
    yield instance, "http://127.0.0.1:%d" % port
    shell.stop()


def call(base, method, path, body=None, token=TOKEN, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


# -- it is off until asked -------------------------------------------------


def test_http_is_not_running_by_default(instance):
    assert instance._http is None


def test_health_needs_no_token(api):
    _, base = api
    assert call(base, "GET", "/api/health", token=None) == (200, {"ok": True})


def test_everything_else_does(api):
    _, base = api
    status, body = call(base, "GET", "/api/sessions", token=None)
    assert status == 401
    assert body["error"] == "unauthorized"


# -- the eight endpoints ---------------------------------------------------


def test_create_list_get_delete(api):
    t, base = api

    status, created = call(base, "POST", "/api/sessions",
                           {"id": "id5", "cwd": t.workspace, "cmd": "cat"})
    assert status == 201
    assert created["id"] == "id5"
    assert created["cmd"] == "cat"
    assert created["status"] == "alive"

    status, listing = call(base, "GET", "/api/sessions")
    assert status == 200
    assert [s["id"] for s in listing["sessions"]] == ["id5"]

    status, one = call(base, "GET", "/api/sessions/id5")
    assert (status, one["id"]) == (200, "id5")

    status, gone = call(base, "DELETE", "/api/sessions/id5")
    assert (status, gone["killed"]) == (200, True)
    assert t.has("id5") is False


def test_create_is_get_or_create(api):
    _, base = api
    call(base, "POST", "/api/sessions", {"id": "twice", "cmd": "cat"})
    status, again = call(base, "POST", "/api/sessions", {"id": "twice", "cmd": "sh"})
    assert status == 201
    assert again["cmd"] == "cat"        # the id decides, not the payload


def test_keys_endpoint_writes(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "w", "cmd": "cat"})
    assert call(base, "POST", "/api/sessions/w/keys",
                {"text": "hello", "enter": True}) == (200, {"ok": True})

    from test_sessions import wait_for

    assert wait_for(t, "w", "hello")


def test_rename_endpoint(api):
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "a", "cmd": "cat"})
    status, body = call(base, "POST", "/api/sessions/a/rename", {"id": "b"})
    assert (status, body["id"]) == (200, "b")
    assert t.has("b") is True


def test_info_endpoint(api):
    _, base = api
    status, info = call(base, "GET", "/api/info")
    assert status == 200
    assert info["tmux"]["version"]
    assert "sessions" in info


def test_url_points_at_ttyd_not_at_this_port(api, tmp_path):
    """Two ports, two audiences: the API answers programs, the URL it hands
    back is for a person."""
    t, base = api
    t.port = 12345                                  # pretend ttyd is configured
    _, created = call(base, "POST", "/api/sessions", {"id": "u1"})
    assert created["url"] == "http://127.0.0.1:12345/?arg=u1"
    assert "12345" in created["url"] and base.split(":")[-1] not in created["url"]


# -- errors are the library's exceptions, projected ------------------------


def test_missing_session_is_404_with_the_library_code(api):
    _, base = api
    status, body = call(base, "GET", "/api/sessions/nope")
    assert status == 404
    assert body["error"] == "no_such_session"


def test_bad_id_is_400(api):
    _, base = api
    status, body = call(base, "POST", "/api/sessions", {"id": "a:b"})
    assert status == 400
    assert body["error"] == "bad_id"


def test_unknown_route_is_404(api):
    _, base = api
    status, body = call(base, "GET", "/api/nothing")
    assert (status, body["error"]) == (404, "not_found")


def test_broken_json_is_400(api):
    _, base = api
    req = urllib.request.Request(base + "/api/sessions", data=b"{oops", method="POST")
    req.add_header("Authorization", "Bearer " + TOKEN)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


# -- idempotency -----------------------------------------------------------


def test_replaying_a_key_does_not_type_twice(api):
    """A retried POST must not run `terraform apply` a second time."""
    t, base = api
    call(base, "POST", "/api/sessions", {"id": "idem", "cmd": "cat"})
    headers = {"Idempotency-Key": "abc-123"}
    for _ in range(3):
        call(base, "POST", "/api/sessions/idem/keys", {"text": "X"}, headers=headers)

    from test_sessions import capture, wait_for

    assert wait_for(t, "idem", "X")
    assert capture(t, "idem").count("X") == 1


# -- the remote client -----------------------------------------------------


def test_remote_client_round_trip(api):
    t, base = api
    remote = RemoteTmuxd(base, token=TOKEN)

    s = remote.session(id="r1", cwd=t.workspace, cmd="cat")
    assert s.id == "r1"
    assert s.alive is True

    s.send("remote hello", enter=True)

    from test_sessions import wait_for

    assert wait_for(t, "r1", "remote hello")

    assert [x.id for x in remote.sessions()] == ["r1"]
    assert remote.has("r1") is True
    assert remote.info()["tmux"]["version"]

    s.kill()
    assert remote.has("r1") is False


def test_remote_errors_arrive_as_the_same_exceptions(api):
    from tmuxd import BadId, NoSuchSession

    _, base = api
    remote = RemoteTmuxd(base, token=TOKEN)
    with pytest.raises(NoSuchSession):
        remote.get("absent")
    with pytest.raises(BadId):
        remote.session(id="bad:id")


def test_remote_unreachable_is_its_own_error():
    from tmuxd import Unreachable

    remote = RemoteTmuxd("http://127.0.0.1:%d" % free_port(), token=TOKEN)
    with pytest.raises(Unreachable):
        remote.sessions()


def test_remote_bad_token(api):
    from tmuxd import Unauthorized

    _, base = api
    with pytest.raises(Unauthorized):
        RemoteTmuxd(base, token="wrong").sessions()
