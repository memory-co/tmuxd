# `Tmuxd` —— 实例

一个实例 = **一个 ttyd + 一个专属 tmux 会话池**。

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")
```

构造完成时:**ttyd 已经在听,tmux 一个进程都没有**(server 懒起,第一个会话时才拉起来)。

> **`tmuxd = tmux + ttyd`,缺一个都不成立。** 没有"只管 tmux 不要网页入口"这种模式 ——
> 让 shell 活得比连接久是 tmux 的活,让人从浏览器进去是 ttyd 的活,少了后者
> 这东西就退化成一个 tmux 封装。所以**构造就意味着一个完整的 tmuxd 在跑**:
> tmux 探得到,ttyd 在听;任一不满足,构造直接失败。

**建会话、取会话、往里敲** 全部在 [session.md](session.md)。这一篇只讲实例本身。

---

## 构造参数

除 `port` 外全部是关键字参数。

```python
Tmuxd(port=7681, *, bind=None, token=None, socket=None, workspace=None,
      shell=None, history_limit=None, tmux_bin=None, ttyd_bin=None,
      state_dir=None, gc_ttl=None, url_host=None)
```

| 参数 | 默认 | 环境变量 | 说明 |
| --- | --- | --- | --- |
| `port` | `7681` | `TMUXD_PORT` | ttyd 端口,也是 `s.url` 里的那个 |
| `bind` | `127.0.0.1` | `TMUXD_BIND` | ttyd 绑哪。非回环地址**必须**同时给 `token`,否则 `ValueError` |
| `token` | 无 | `TMUXD_TOKEN` | ttyd basic auth 的密码,用户名固定 `tmuxd` |
| `socket` | `"tmuxd"` | `TMUXD_SOCKET` | 实例名 → tmux socket + 状态子目录。**`"default"` 会报错** |
| `workspace` | 当前 cwd | `TMUXD_WORKSPACE` | 新会话不给 `cwd` 时用它 |
| `shell` | tmux 的默认 | `TMUXD_SHELL` | 不给 `cmd` 时跑什么(写进 `default-shell`) |
| `history_limit` | `10000` | `TMUXD_HISTORY_LIMIT` | 人在网页里能往回滚多远 |
| `tmux_bin` | PATH 里的 `tmux` | `TMUXD_TMUX_BIN` | |
| `ttyd_bin` | PATH → 包里自带的 | `TMUXD_TTYD_BIN` | 查找顺序见 [works/06 §3](../works/06-dependencies.md) |
| `state_dir` | `~/.tmuxd` | `TMUXD_STATE_DIR` | 实际用的是 `<state_dir>/<socket>/` |
| `gc_ttl` | `604800`(7 天) | `TMUXD_GC_TTL` | `exited` 记录保留多少秒 |
| `url_host` | 由 `bind` 推导 | `TMUXD_URL_HOST` | `s.url` 里的主机名。机器在 NAT / 反代后面时给它 |

优先级:**构造参数 > 环境变量 > 默认值**。

### 构造时会做什么

1. 解析实例名 → tmux socket(`tmuxd` 或 `tmuxd-<name>`)与状态目录;
2. 找到 tmux 并 `tmux -V`,**低于 3.0 抛 `TmuxMissing`** —— 这两步都不启动任何 tmux 进程;
3. 渲染一份 `tmux.conf` 到状态目录(`history-limit` / `window-size latest` / `status off`);
4. **确保端口上有一个 ttyd** —— 起一个,或接手一个已经在跑的(见下)。

任何一步失败都在**这里**抛,不拖到有人打开浏览器才炸。

---

## ttyd 的归属

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

**绑生死用 `PR_SET_PDEATHSIG`**:你的进程一消失,内核直接给 ttyd 发 SIGTERM。
写在 `finally` 里不行 —— SIGKILL 之下没有一行 Python 会执行。

---

## 专属会话池

会话池永远开在自己的 socket 上,**和你自己的 tmux 永不相交**:

| 实例 | tmux socket |
| --- | --- |
| `Tmuxd(...)` | `tmux -L tmuxd` |
| `Tmuxd(socket="ci")` | `tmux -L tmuxd-ci` |
| **你自己的 tmux** | `tmux`(default socket)—— 看不见,也不去看 |

`socket="default"` 直接 `ValueError`。理由是双向的:tmuxd 要能对自己那批会话全权负责,
**更要紧的是它不该有能力动你那个跑了三天的工作会话**
([works/01 §4](../works/01-library.md))。

**tmux server 是懒起的** —— 构造完那边一个进程都没有,第一个会话出现时才拉起来。

---

## `info() -> dict`

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

`sessions.external` **正常应该是 0**,不是 0 说明有人绕过库直接
`tmux -L tmuxd new-session` 了([session.md](session.md#external))。

---

## `serve_http(port, *, bind=None, token=None) -> HttpShell`

把库暴露成 HTTP。**默认不开**,要暴露才调。

```python
shell = t.serve_http(12346, token="api-token")
...
shell.stop()
```

在后台线程里跑,立刻返回。`bind` 不给就跟 `Tmuxd` 的一样。
七个端点见 [works/03-http.md §4](../works/03-http.md)。

**两个端口,两拨用户**:`port` 是 ttyd,给**人**开浏览器;这个是 API,给**程序**。

---

## `close()`

**收掉这个实例起的东西:HTTP 壳,以及归自己管的那个 ttyd。**

- **不碰任何会话**;
- 接手来的 ttyd(`owned=False`)不会被停掉 —— 那不是你的孩子。

```python
with Tmuxd(port=12345) as t:
    t.session(id="job-1", cmd="./deploy.sh")
# ttyd 没了,job-1 还在跑
```

上下文管理器管的是**你起的那个门面**,不是屋里的人。要销毁会话只有 `s.kill()`。

---

## `kill_tmux_server()`

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
