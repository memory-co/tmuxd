# `Tmuxd`

一个实例:持有一个 ttyd 子进程,管一批开在**专属 tmux socket** 上的会话。

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")
```

构造完成时:**ttyd 已经在跑,tmux 一个进程都没有**(server 懒起,第一个会话时才拉起来)。

---

## 构造参数

除 `port` 外全部是关键字参数。

```python
Tmuxd(port=None, *, bind=None, token=None, socket=None, workspace=None,
      shell=None, history_limit=None, tmux_bin=None, ttyd_bin=None,
      state_dir=None, gc_ttl=None, url_host=None, start_ttyd=True)
```

| 参数 | 默认 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| `port` | `7681` | `TMUXD_PORT` | ttyd 端口,也是 `s.url` 里的端口。**`None` = 不起 ttyd** |
| `bind` | `127.0.0.1` | `TMUXD_BIND` | ttyd 绑哪。非回环地址**必须**同时给 `token`,否则 `ValueError` |
| `token` | 无 | `TMUXD_TOKEN` | ttyd basic auth 的密码,用户名固定 `tmuxd` |
| `socket` | `"tmuxd"` | `TMUXD_SOCKET` | 实例名 → tmux socket + 状态子目录。**`"default"` 会报错** |
| `workspace` | 当前 cwd | `TMUXD_WORKSPACE` | 新会话不给 `cwd` 时用它 |
| `shell` | tmux 的默认 | `TMUXD_SHELL` | 不给 `cmd` 时跑什么(写进 `default-shell`) |
| `history_limit` | `10000` | `TMUXD_HISTORY_LIMIT` | 人在网页里能往回滚多远 |
| `tmux_bin` | PATH 里的 `tmux` | `TMUXD_TMUX_BIN` | |
| `ttyd_bin` | PATH 里的 `ttyd` | `TMUXD_TTYD_BIN` | |
| `state_dir` | `~/.tmuxd` | `TMUXD_STATE_DIR` | 实际用的是 `<state_dir>/<socket>/` |
| `gc_ttl` | `604800`(7 天) | `TMUXD_GC_TTL` | `exited` 记录保留多少秒 |
| `url_host` | 由 `bind` 推导 | `TMUXD_URL_HOST` | `s.url` 里的主机名。机器在 NAT / 反代后面时给它 |
| `start_ttyd` | `True` | — | `False` = 只管 tmux,不碰端口 |

优先级:**构造参数 > 环境变量 > 默认值**。

### 构造时会做什么

1. 解析实例名 → tmux socket(`tmuxd` 或 `tmuxd-<name>`)与状态目录;
2. `shutil.which("tmux")` + `tmux -V`,**低于 3.0 直接抛 `TmuxMissing`**
   —— 这两步都不启动任何 tmux 进程;
3. 渲染一份 `tmux.conf` 到状态目录(`history-limit` / `window-size latest` / `status off`);
4. `start_ttyd` 为真且 `port` 不为 `None` 时,**确保端口上有一个 ttyd**(见下)。

### ttyd 的复用规则

ttyd 不持有任何会话状态(它只 exec `attach.sh`),所以复用是安全的:

| 端口上是什么 | 行为 |
| --- | --- |
| 空的 | 起一个,**归我管**(我 `close()` 它就走) |
| 已有一个 tmuxd 起的 ttyd(同 socket) | **接手用**,不重起;**我 `close()` 它不走** |
| 别人的东西 | 抛 `PortInUse`,不猜、不抢 |

这让"Web 后端每次重启 worker 都 `Tmuxd(port=…)` 一下"变成安全操作 ——
否则每次重启要么撞端口,要么把用户正连着的网页踢掉。

```python
t.info()["ttyd"]["owned"]     # 这个 ttyd 是不是我起的
```

### `start_ttyd=False`

只管 tmux,完全不碰端口。`s.url` 照常能算出来(它只是个字符串)。

CLI 的读命令走的就是这条 —— 否则 `tmuxd ls` 会顺手起一个 ttyd 然后立刻带走它。

```python
t = Tmuxd(port=12345, start_ttyd=False)
print(t.session(id="x").url)      # 地址是对的,只是门可能没开
```

---

## 建会话与取会话

### `session(id=None, cwd=None, cmd=None, env=None) -> Session`

**有则接上,无则创建**(`tmux new-session -A` 的语义)。最常用的一个。

```python
s = t.session(id="work", cwd="~/proj", cmd="npm run dev")
s = t.session(id="work")            # 再来一次:接上原来那个
s = t.session()                     # id 不给就生成:"0"、"1"、"2"…
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `id` | 生成 | 不能含 `.` `:`,不能以 `-` 开头,不能为空,≤200 字符 |
| `cwd` | `workspace` | `~` 会展开,相对路径按当前进程的 cwd 解析成绝对路径 |
| `cmd` | `shell` | 一整条命令行字符串,交给 tmux 起 |
| `env` | 无 | `{"K": "V"}`,落到 `tmux new-session -e` |

**已存在的会话不会因为这次给的 `cwd` / `cmd` 不同而被重建 —— id 说了算。**
要换命令就先 `kill` 再建,显式的。

**命令不存在不会抛异常**:会话建起来后立刻退出,`status` 是 `exited`。
这和你在自己终端里敲错命令是一回事。

抛:`BadId`、`TmuxGone`。

### `create(id=None, cwd=None, cmd=None, env=None) -> Session`

同上,但 id 已存在时抛 `SessionExists` 而不是接上。

用在"我确信这是个新东西"的地方 —— 撞了说明有 bug,该让它响。

### `get(id) -> Session`

**只接不建**,不存在抛 `NoSuchSession`。

```python
try:
    t.get("work").send("继续", enter=True)
except NoSuchSession:
    ...
```

`session()` 和 `get()` 是两个动词而不是一个布尔参数,因为
"我以为会接上结果开了个新的"是最难查的那类 bug。

### `has(id) -> bool`

存在返回 `True`。id 非法也返回 `False`(不抛)。

```python
if not t.has("work"):
    t.session(id="work", cwd="~/proj")
```

### `sessions() -> list[Session]`

全部会话,**每次都现场跑一次 `tmux ls`**,不读缓存。

```python
for s in t.sessions():
    print(s.id, s.status, s.clients, s.current_command)
```

这个方法同时做三件事:

- **对账**:记录里有、tmux 里没有的标 `exited`;tmux 里有、记录里没有的标 `external`;
- **回收**:`exited` 且超过 `gc_ttl` 的记录删掉。**只删 JSON 文件,永远不 kill 活会话**;
- 顺带刷新 `clients` / `current_command` 这些实时字段。

> **对账只在这里发生 —— 没有后台线程,没有定时器。**
> 一个被 `import` 进来的库不该背着调用方每 60 秒醒一次、写一次盘。
> 常驻进程想更勤快就自己按需要调。

**tmux server 还没起来时返回 `[]`,不是抛异常** —— 这一条是实现上最容易写错的地方:
tmux 在 server 不存在时以 exit 1 报 `error connecting to ...`,库把它读成空列表。

### `url_for(sid) -> str | None`

不需要会话存在,纯算字符串。`port=None` 时返回 `None`。

```python
t.url_for("work")        # http://127.0.0.1:12345/?arg=work
```

---

## 观测

### `info() -> dict`

```python
{
  "version": "1.0.0",
  "socket": "default",
  "state_dir": "/home/me/.tmuxd/default",
  "ttyd": {"version": "1.7.7", "port": 12345, "bind": "127.0.0.1",
           "pid": 41822, "owned": True, "listening": True},
  "tmux": {"bin": "/usr/bin/tmux", "version": "3.3a",
           "socket": "tmuxd", "running": True},
  "sessions": {"total": 4, "alive": 3, "exited": 1, "external": 0},
}
```

`ttyd` 在 `port=None` 时是 `None`;`start_ttyd=False` 时照样会去探端口,
所以 `listening` 始终可信。

`sessions.external` **正常应该是 0**,不是 0 说明有人绕过库直接
`tmux -L tmuxd new-session` 了。

---

## 生命周期

### `serve_http(port, *, bind=None, token=None) -> HttpShell`

把库暴露成 HTTP。**默认不开**,要暴露才调。

```python
shell = t.serve_http(12346, token="api-token")
...
shell.stop()
```

在后台线程里跑,立刻返回。`bind` 不给就跟 `Tmuxd` 的 `bind` 一样。
端点见 [works/03-http.md §4](../works/03-http.md)。

### `close()`

**收掉这个实例起的东西:HTTP 壳,以及归自己管的那个 ttyd。**

```python
t.close()
```

- **不碰任何会话**;
- 接手来的 ttyd(`owned=False`)不会被停掉 —— 那不是你的孩子。

### `__enter__` / `__exit__`

```python
with Tmuxd(port=12345) as t:
    t.session(id="job-1", cmd="./deploy.sh")
# ttyd 没了,job-1 还在跑
```

上下文管理器管的是**你起的那个门面**,不是屋里的人。

### `kill_tmux_server()`

**销毁这个池里的全部会话。** 显式调用才会发生,库自己永远不调。

```python
t.kill_tmux_server()      # 等价于 tmux -L tmuxd kill-server
```

你自己的 tmux 在另一个 socket 上,碰不到。

---

## 属性

构造后可读,基本都是构造参数解析后的最终值:

| 属性 | 例 |
| --- | --- |
| `port` / `bind` / `token` | `12345` / `"127.0.0.1"` / `"changeme"` |
| `socket_name` | `"default"` —— 实例名 |
| `tmux_socket` | `"tmuxd"` / `"tmuxd-ci"` —— 真正传给 `tmux -L` 的 |
| `tmux_bin` / `tmux_version` | `"/usr/bin/tmux"` / `"3.3a"` |
| `state_dir` | `"/home/me/.tmuxd/default"` |
| `workspace` | 新会话的默认 cwd(已解析成绝对路径) |
| `gc_ttl` | 秒 |
