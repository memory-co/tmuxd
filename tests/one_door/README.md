# one_door — ttyd 走 unix socket,窗从宿主的端口、宿主的门进来

## 这个场景在测什么

嵌进 Web 后端时,窗不该在宿主的门外另开一个端口、另带一套密码
([works/08](../../docs/v1/works/08-one-door.md))。所以:

1. **不给端口就是 socket**,而且 socket 只有属主能连(0600 —— lws 自己建成 0660,得 tmuxd 改);
   **给了 `port=` 仍然是 TCP**,老写法意思不变;自相矛盾的参数直接 `ValueError`,不替人挑一个;
2. **URL 是相对的** `<base_path>/?arg=<id>` —— 没有 host 可报,窗在宿主挂它的地方;
3. **复用规则照搬到 socket 上**:自己人接手、陌生人 `PortInUse`、前缀不同的不接手、
   SIGKILL 留下的 socket 遗骸自动清掉;
4. **`t.asgi()` 真的把终端搬过来了**:页面、`/token`、跳转,以及 WebSocket 那一路
   (`tty` 子协议、输出回来、输入进到 tmux 里);ttyd 没了是 502 不是挂住;
5. **门是宿主的**:钩子说不就 403 / 1008;钩子看得到**每一条**进门的路(页面、`/token`、`/ws`);
   能按 `arg` 把一张票限死在一扇窗上;能在放行时带上 Set-Cookie;同步异步都行。

## 不在这测什么

- TCP 端口上的复用 —— 在 [`port_reuse/`](../port_reuse/)。
- ttyd 的 basic auth 和 `attach.sh` 不建会话 —— 在 [`the_entrance/`](../the_entrance/)。
- 宿主那一侧的票据、cookie、登出作废 —— 那是宿主的逻辑,这里只证明钩子给得出所需的一切。

## fixture 来源

- `root`:`tmp_path_factory.mktemp("d")` —— **短路径**。`sun_path` 只有 108 字节,
  pytest 的 `tmp_path` 加上池名会超(超了的报错本身也有一条用例)
- `pool` / `door`:本场景自己的 —— `door` 是 `Tmuxd(base_path="/tty")`,socket 模式
- `host(t, authorize)`:一个 Starlette 宿主 app,自己一条 `/api/hello`,外加挂上去的窗;
  用 Starlette 的 `TestClient` 打,包括 WebSocket
- `pool_name()` / `kill_pool()` / `wait_for()` / `wait_until()`(`tests/conftest.py`)
- 需要 `websockets` 和 `starlette`(`pip install -e ".[dev]"` 都带了),缺了整个场景跳过
