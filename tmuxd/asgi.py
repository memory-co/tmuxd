"""The window as an ASGI app: ``t.asgi()``, relayed to ttyd byte for byte.

    app.mount("/tty", t.asgi(authorize=gate))     # t = Tmuxd(base_path="/tty")

This is a gateway part tmuxd keeps ready for the layer above, not core
behaviour: the core still only reports a URL (muxd-spec M11). Nothing here
reads ttyd's protocol. HTTP goes through as it came; a WebSocket gets one task
per direction copying frames, with the ``tty`` subprotocol passed along.

Who may pass is not tmuxd's business -- it has no idea who anyone is. It asks
``authorize(scope)`` and does what that says:

    falsy                    no: HTTP 403, WebSocket closed with 1008
    True                     yes
    [(name, value), ...]     yes, and add these headers to the HTTP response
                             (a Set-Cookie after checking a ticket, say);
                             a WebSocket handshake has no response to add
                             them to, so there they only mean yes

It may be sync or async. It sees every request -- the page, ``/token``,
``/ws`` and the assets -- because each one is a way in.

No ``authorize`` lets everyone in. The socket's 0600 only keeps other local
users off ttyd; this app is what puts it back on a public port, so without a
gate anyone who reaches the host can open ``?arg=<any id>``. Leave it out only
when the host listens on loopback, or something in front already
authenticates every request. The host's own ``Authorization``-header auth does
not cover this: an iframe and a browser WebSocket cannot send that header.

ASGI rather than a FastAPI router: this is a calling convention, not a
framework, so FastAPI, Starlette, Litestar and Quart all mount it. Only WSGI
cannot -- WSGI has no WebSocket, whoever implements it.

ttyd's lifetime is not the app's: ``Tmuxd`` starts it and ``close()`` takes
it down. A mounted sub-app never sees lifespan events anyway.

Needs ``tmuxd[asgi]`` (``websockets``, for the ttyd side of the WebSocket).
"""

import asyncio
import inspect
from urllib.parse import quote

try:
    from websockets.asyncio.client import connect as _ws_connect
    from websockets.asyncio.client import unix_connect as _ws_unix_connect
    from websockets.exceptions import ConnectionClosed
except ImportError as exc:  # pragma: no cover - exercised by the install matrix
    raise ModuleNotFoundError(
        "t.asgi() needs the asgi extra: pip install 'tmuxd[asgi]'") from exc

# Headers that describe one hop, not the message. The upstream leg is its own
# connection (and always Connection: close), so none of these carry over.
_HOP = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade", b"content-length",
}
_PATH_SAFE = "/:@!$&'()*+,;=-._~"
_CHUNK = 64 * 1024


class TtydProxy:
    def __init__(self, tmuxd, authorize=None):
        ttyd = tmuxd._ttyd
        self.socket_path = ttyd.socket_path
        self.host = "127.0.0.1" if ttyd.bind in (None, "0.0.0.0", "::", "") else ttyd.bind
        self.port = ttyd.port
        self.base_path = tmuxd.base_path or ""
        self.authorize = authorize

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            await _lifespan(receive, send)
            return

        verdict = True
        if self.authorize is not None:
            verdict = self.authorize(scope)
            if inspect.isawaitable(verdict):
                verdict = await verdict

        if kind == "http":
            if not verdict:
                await _plain(send, 403, b"forbidden")
                return
            extra = [] if verdict is True else [
                (_b(k).lower(), _b(v)) for k, v in verdict]
            await self._http(scope, receive, send, extra)
        elif kind == "websocket":
            if not verdict:
                # Closing before accepting is how ASGI says "403" to the
                # handshake; 1008 is policy violation.
                await receive()
                await send({"type": "websocket.close", "code": 1008})
                return
            await self._websocket(scope, receive, send)

    # -- where it goes -------------------------------------------------------

    def _target(self, scope):
        """The path ttyd expects: its base_path plus what follows the mount.

        Starlette keeps ``path`` whole and grows ``root_path`` by the mount
        prefix; older routers strip ``path`` instead. Either way, what is left
        after ``root_path`` is ours.
        """
        path, root = scope.get("path", "/"), scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        target = quote(self.base_path + path, safe=_PATH_SAFE) or "/"
        if scope.get("query_string"):
            target += "?" + scope["query_string"].decode("latin-1")
        return target

    async def _open(self):
        if self.socket_path:
            return await asyncio.open_unix_connection(self.socket_path)
        return await asyncio.open_connection(self.host, self.port)

    # -- HTTP -------------------------------------------------------------------

    async def _http(self, scope, receive, send, extra):
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        try:
            reader, writer = await self._open()
        except OSError:
            await _plain(send, 502, b"ttyd is not answering")
            return

        try:
            request_line = "%s %s HTTP/1.1\r\n" % (scope["method"], self._target(scope))
            headers = [(k, v) for k, v in scope.get("headers", [])
                       if k.lower() not in _HOP]
            if not any(k.lower() == b"host" for k, _ in headers):
                headers.append((b"host", b"localhost"))
            if body:
                headers.append((b"content-length", str(len(body)).encode()))
            headers.append((b"connection", b"close"))
            writer.write(request_line.encode("latin-1"))
            writer.write(b"".join(k + b": " + v + b"\r\n" for k, v in headers))
            writer.write(b"\r\n" + body)
            await writer.drain()

            status, headers = await _read_head(reader)
            if status is None:
                await _plain(send, 502, b"ttyd sent no response")
                return
            length = chunked = None
            out = []
            for k, v in headers:
                lower = k.lower()
                if lower == b"content-length":
                    length = int(v)
                elif lower == b"transfer-encoding":
                    chunked = b"chunked" in v.lower()
                if lower not in _HOP:
                    out.append((lower, v))
            if length is not None and not chunked:
                out.append((b"content-length", str(length).encode()))
            await send({"type": "http.response.start", "status": status,
                        "headers": out + extra})

            if scope["method"] == "HEAD" or status in (204, 304) or 100 <= status < 200:
                pieces = _nothing()
            elif chunked:
                pieces = _read_chunked(reader)
            elif length is not None:
                pieces = _read_exactly(reader, length)
            else:
                pieces = _read_to_eof(reader)
            async for piece in pieces:
                await send({"type": "http.response.body", "body": piece,
                            "more_body": True})
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})
        finally:
            writer.close()

    # -- WebSocket ----------------------------------------------------------------

    async def _websocket(self, scope, receive, send):
        message = await receive()
        if message["type"] != "websocket.connect":
            return

        uri = "ws://localhost" + self._target(scope)
        options = dict(
            subprotocols=scope.get("subprotocols") or None,
            compression=None,        # frames go through as they are
            max_size=None,           # a full-screen redraw is not an attack
            ping_interval=None,      # the browser keeps its own leg alive
            user_agent_header=None,
        )
        try:
            if self.socket_path:
                upstream = await _ws_unix_connect(self.socket_path, uri, **options)
            else:
                upstream = await _ws_connect(
                    "ws://%s:%d%s" % (self.host, self.port, self._target(scope)),
                    **options)
        except Exception:   # refused, timed out, or ttyd said no to the handshake
            await send({"type": "websocket.close", "code": 1011})
            return

        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

        async def browser_to_ttyd():
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message["type"] != "websocket.receive":
                    continue
                data = message.get("bytes")
                if data is None:
                    data = message.get("text")
                if data is not None:
                    await upstream.send(data)

        async def ttyd_to_browser():
            try:
                async for data in upstream:
                    if isinstance(data, bytes):
                        await send({"type": "websocket.send", "bytes": data})
                    else:
                        await send({"type": "websocket.send", "text": data})
            except ConnectionClosed:
                pass
            code = upstream.close_code
            # 1005/1006 are "no code" / "abnormal" -- reserved, never sent.
            if code in (None, 1005, 1006):
                code = 1000
            try:
                await send({"type": "websocket.close", "code": code})
            except Exception:
                pass    # the browser left first

        tasks = [asyncio.ensure_future(browser_to_ttyd()),
                 asyncio.ensure_future(ttyd_to_browser())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await upstream.close()


# -- helpers -------------------------------------------------------------------


def _b(value):
    return value if isinstance(value, bytes) else str(value).encode("latin-1")


async def _plain(send, status, text):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(text)).encode())]})
    await send({"type": "http.response.body", "body": text})


async def _lifespan(receive, send):
    """Served standalone, answer lifespan so the server does not wait on us."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _read_head(reader):
    line = await reader.readline()
    parts = line.split(None, 2)
    if len(parts) < 2 or not parts[1].isdigit():
        return None, []
    headers = []
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.partition(b":")
        headers.append((name.strip(), value.strip()))
    return int(parts[1]), headers


async def _nothing():
    return
    yield  # pragma: no cover


async def _read_exactly(reader, length):
    while length > 0:
        piece = await reader.read(min(_CHUNK, length))
        if not piece:
            return
        length -= len(piece)
        yield piece


async def _read_to_eof(reader):
    while True:
        piece = await reader.read(_CHUNK)
        if not piece:
            return
        yield piece


async def _read_chunked(reader):
    while True:
        size_line = await reader.readline()
        if not size_line:
            return
        size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        if size == 0:
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass        # trailers: not forwarded
            return
        async for piece in _read_exactly(reader, size):
            yield piece
        await reader.readline()
