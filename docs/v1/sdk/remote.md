# `RemoteTmuxd`

驱动**另一台机器上**的 tmuxd。方法名和 `Tmuxd` 刻意保持一致,
所以代码从本地搬到远程只改构造那一行。

```python
from tmuxd import RemoteTmuxd

t = RemoteTmuxd("http://box:12346", token="api-token")
s = t.session(id="id5", cwd="/srv/app", cmd="claude")
s.send("继续", enter=True)
print(s.url)          # http://box:12345/?arg=id5  ← ttyd 的端口,不是 API 的
```

`RemoteTmuxd` 按需加载,`import tmuxd` 不会把它拖进来。

---

## 前提:对面得开着 HTTP

HTTP 壳**默认不开**。对面那台机器要么在代码里开:

```python
t = Tmuxd(port=12345, token="changeme")
t.serve_http(12346, token="api-token")
```

要么用 CLI:

```bash
tmuxd serve --port 12345 --token changeme --http-port 12346
```

**两个端口,两拨用户**:`12345` 是 ttyd,给**人**开浏览器;`12346` 是 API,给**程序**。
`s.url` 返回的永远是前者。

---

## 它不是 `Tmuxd` 的等价物

方法名一样,但**不假装完全一样** —— 进程生命周期这件事,远程那头替你管不了。

| | `Tmuxd`(本地) | `RemoteTmuxd` |
| --- | --- | --- |
| `session` / `create` / `get` / `has` / `sessions` | ✅ | ✅ |
| `info` / `url_for` | ✅ | ✅ |
| `send` / `send_key` / `rename` / `kill` | ✅ | ✅ |
| **管 ttyd 的生死** | ✅ 起它、绑它、复用它 | ❌ 那是对面进程的事 |
| `serve_http` | ✅ | ❌ |
| `kill_tmux_server` | ✅ | ❌ |
| `close()` / `with` | ✅ 收掉 ttyd | ❌ 没有可收的东西 |
| 出错的方式 | 本地异常 | 多一类:`Unreachable`、`Unauthorized` |

CLI 的 `-H` 模式也是这个道理:`serve` / `start` / `stop` 在那下面直接报错。

---

## 构造

```python
RemoteTmuxd(base_url, token=None, timeout=10.0)
```

| 参数 | 说明 |
| --- | --- |
| `base_url` | `http://host:port`,尾部斜杠会去掉 |
| `token` | 作 `Authorization: Bearer <token>` 发出去 |
| `timeout` | 每次请求的秒数,默认 10 |

**没有连接池、没有重试、没有断线重连** —— 它就是 `urllib` 上的一层薄封装。
需要那些就在外面包。

---

## 方法

和 [`Tmuxd`](tmuxd.md) 同名同参:

```python
t.session(id=None, cwd=None, cmd=None, env=None)   # POST /api/sessions
t.create(...)                                       # 同上(远端语义相同)
t.get(id)                                           # GET  /api/sessions/{id}
t.has(id)                                           # 同上,catch 掉 404
t.sessions()                                        # GET  /api/sessions
t.info()                                            # GET  /api/info
t.url_for(id)                                       # 取 get(id).url
```

返回的是 `RemoteSession`,接口和 [`Session`](session.md) 一致:

```python
s.id, s.cwd, s.cmd, s.created_at, s.last_attached, s.external
s.status, s.alive, s.clients, s.current_command      # 每次访问打一次 HTTP
s.url
s.send(text, enter=False)
s.send_key(*keys)
s.rename(new_id)
s.kill()
s.to_dict()
```

> **实时属性每次访问都是一次 HTTP 往返。** 本地版是一次子进程调用,已经不便宜;
> 远程版更贵。在循环里要连着读多个字段就用 `to_dict()` 一次拿全。
> 顺带一提:`s.url` 用的是**最近一次响应里的快照**,不额外发请求。

---

## 异常是同一套

远端返回的错误体带 `code`,客户端按 `code` 还原成**同一个异常类**:

```python
from tmuxd import NoSuchSession, BadId, Unauthorized, Unreachable

try:
    t.get("ghost")
except NoSuchSession:      # 和本地一模一样的 except
    ...
```

多出来的两个:

| 异常 | 什么时候 |
| --- | --- |
| `Unreachable` | 连不上、超时、或者对面返回了不是 JSON 的东西 |
| `Unauthorized` | token 不对 |

两个都是 `PlatformError`,重试之前先看看配置([errors.md](errors.md))。

---

## 一段本地/远程通吃的代码

```python
import os
from tmuxd import Tmuxd, RemoteTmuxd, NoSuchSession

def connect():
    host = os.environ.get("TMUXD_HOST")
    if host:
        return RemoteTmuxd(host, token=os.environ["TMUXD_TOKEN"])
    return Tmuxd(port=12345, token=os.environ.get("TMUXD_TOKEN"))

t = connect()
for proj in projects:
    s = t.session(id=job_id + "--" + proj.strip("/").replace("/", "-"),
                  cwd=proj, cmd="claude")
    s.send(prompt, enter=True)
    print(proj, s.url)
```

**除了 `connect()` 那几行,下面的代码不需要知道自己在跟谁说话。**
这就是方法名保持一致的全部理由。

唯一要注意的:本地那条路上 `t` 持有一个 ttyd,进程退出时它会走;
远程那条路上没有这回事。要让本地的门面也活得久,用 `tmuxd serve`
([CLI · daemon](../cli/daemon.md))。
