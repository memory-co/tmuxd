# 03 · 可选的 HTTP 暴露

## 1. 它是壳,不是内核

核心是 Python 库([01 §1](01-library.md))。这一章讲的是**把库暴露出去**的那层壳,
给同一个进程里够不着的调用方用:别的语言、别的机器、别的进程。

```python
t = Tmuxd(port=12345, token="changeme")
t.serve_http(port=12346, token="changeme")   # ← 默认不起,要暴露才调
```

**默认不开。** 这不是保守设置,是这个架构的立场:

> 大多数调用方和 tmuxd 在同一个进程里(编排程序、Web 后端、脚本)。
> 让它们绕一圈 HTTP 去调自己进程里就能做的事,是白交的税 —— 多一次序列化、
> 多一个端口、多一套鉴权、多一类"连不上"的错误。

所以顺序是:**先 `import`,够不着了才 `serve_http`。** 反过来(先起服务再配个 SDK)
会让所有人都付那笔税,包括本来不需要付的。

两个端口的分工要记清楚:

| 端口 | 是谁 | 给谁 | 默认 |
| --- | --- | --- | --- |
| `port=12345` | **ttyd** | **人** —— 浏览器打开 `?arg=<id>` 进终端 | 起 |
| `serve_http(port=12346)` | tmuxd 的 HTTP | **程序** —— 建会话、往里敲 | 不起 |

**不合并成一个端口。** 合并要在 ttyd 前面架反代,而 ttyd 的 URL 是这套设计里
唯一对人暴露的东西([01 §2](01-library.md)),不该为了省一个端口把它藏到代理后面。
两个端口各自绑 `127.0.0.1`,各自要 token,谁都不神秘。

## 2. 再划一次边界:不读,只写

**HTTP 这层能做的事,是库能做的事的子集,而库本身就不提供读。**
没有 `capture`,没有 `run`,没有输出流,没有录制,没有事件流。

**读终端内容这件事,要么归人,要么归 ssh。**

| 你想干嘛 | 用什么 | 为什么不是 tmuxd |
| --- | --- | --- |
| 看这个会话现在什么样 | **人打开 `?arg=<id>`** | ttyd 已经把这件事做完了,而且做得比任何 API 好 |
| 程序化拿一条命令的输出和退出码 | **ssh** | 那里有干净的 stdout/stderr、真的退出码、二进制安全 |
| 程序化往一个人正在看的会话里投喂 | **`send` / `send_key`** | ← 只有这件事,别的地方都办不了 |

把终端当读接口用,每一步都在交税:抓出来的是**屏幕**不是日志,受 pane 宽度硬折行;
全屏程序(vim、htop、Agent 的 TUI)抓出来只是当前一帧;想拿退出码得往命令后面拼标记,
还得先判断里面是不是一个闲着的 shell。这些税**都是为了把终端伪装成 API 而付的**,
而真要 API,ssh 那条路本来就更直。

不读还顺带省掉三样麻烦:不必挂 `pipe-pane`、不必解析 ANSI、
**不必往磁盘上落一份会记下密码和 token 的明文录制**。

`send` 之所以留下,是因为它不需要理解终端里的内容 —— 只是把字符送进去。
这正是"程序驱动一个**有人在看**的会话"的最小动作。

## 3. 约定

**Base**:`http://<host>:<serve_http 的端口>/api`。

**认证**:`Authorization: Bearer <token>`。和 ttyd 那个 token 可以不同 ——
它们是两个口、两拨用户(一个是人,一个是程序)。

**格式**:请求响应一律 JSON。时间戳一律 ISO 8601 UTC(`2026-08-08T09:00:00Z`)。

**错误体**:扁平一层,可选 `details`:

```jsonc
{ "error": "session_exists",
  "message": "id \"id5\" 已经有会话了",
  "details": { "id": "id5" } }
```

**幂等**:`POST` 接受 `Idempotency-Key`,10 分钟窗口内重放返回原结果。
往里敲尤其要用 —— **网络重试导致重复敲一遍命令是真实事故**,
尤其当那条命令是 `terraform apply`。这也是 HTTP 这层比直接调库多出来的唯一一类问题:
库调用不会"重试半条"。

**并发**:会话级操作按 id 加锁(库那层就锁了,跨进程用文件锁,[01 §6](01-library.md))。
往里敲**不排队** —— 字符串行进去是 tmux 本来的行为,拦它反而不对。

**权限**:只有一档,全部可读可写。没有只读 token —— 要锁往上层去锁([02 §4](02-session.md))。

**无分页**:会话量级是个位数到几十,不设计分页。

## 4. 端点总表

**七个,一一对应库上的方法。** 这层不发明任何库里没有的东西。

| 方法 | 路径 | 库里对应 |
| --- | --- | --- |
| `GET` | `/api/sessions` | `t.sessions()` |
| `POST` | `/api/sessions` | `t.session(id, cwd, cmd, env)` —— 无中生有 |
| `GET` | `/api/sessions/{id}` | `t.get(id)` |
| `DELETE` | `/api/sessions/{id}` | `s.kill()` |
| `POST` | `/api/sessions/{id}/keys` | `s.send(...)` / `s.send_key(...)`,见 §5 |
| `GET` | `/api/info` | `t.info()` —— 版本、ttyd 端口、tmux 二进制与版本、会话统计 |
| `GET` | `/api/health` | 免鉴权 |

```jsonc
// POST /api/sessions
{ "id": "id5", "cwd": "/home/me/proj", "cmd": "claude" }
→ 201
{ "id": "id5", "cwd": "/home/me/proj", "cmd": "claude",
  "status": "alive", "clients": 0, "current_command": "claude",
  "url": "http://localhost:12345/?arg=id5",     // ← ttyd 的地址,不是这个口的
  "created_at": "2026-08-08T09:00:00Z" }
```

`url` 那行是这层最有用的返回值:**远程调用方建完会话,拿到的是一个能直接发给人的地址。**
注意它指向的是 ttyd 的端口(`12345`),不是 HTTP API 的端口 ——
两个口,两拨用户([§1](#1-它是壳不是内核))。

```jsonc
// GET /api/info
{ "version": "1.0",
  "ttyd": { "version": "1.7.7", "port": 12345, "bind": "127.0.0.1", "owned": true },
  "tmux": { "bin": "/usr/bin/tmux", "version": "3.3a", "socket": "tmuxd", "server_pid": 41822 },
  "sessions": { "total": 4, "alive": 3, "exited": 1, "external": 0 } }
```

`ttyd.owned` = 这个 ttyd 是不是本进程起的([01 §3.1](01-library.md))——
运维时要知道"我退出会不会把网页入口带走"。

**一条 WebSocket 都没有。** 唯一走 WS 的是 ttyd 自己那条终端通道,它不属于本 API。

**没有 attach 端点。** 进终端的地址是 ttyd 原生的 `?arg=<id>`,不需要这层参与
—— 早先的稿子在这里放过一个 302 跳转,那是多余的一跳([01 §2](01-library.md))。

## 5. 往里敲

**两个字段,分得很开,这是有原因的:**

```jsonc
POST /api/sessions/id5/keys
{ "text": "npm test", "enter": true }         // 字面量,一个字符都不解释

POST /api/sessions/id5/keys
{ "keys": ["C-c", "q", "Enter"] }             // tmux 的按键名
```

| 字段 | 库里 | 对应 tmux | 语义 |
| --- | --- | --- | --- |
| `text` | `s.send(text, enter=)` | `send-keys -l` | **字面量**,原样打进去 |
| `keys` | `s.send_key(*keys)` | `send-keys` | tmux 键名(`Enter` `Escape` `C-c` `M-x` `Up`…) |

`send-keys` 不加 `-l` 时会把参数当**键名**解析,于是 `send-keys "Enter the code"` 里的
`Enter` 变成回车键 —— 这是 tmux 用户人人踩过一次的坑。tmuxd 把它挡在接口形状上:
**要发文本就用 `text` / `send()`,要发按键就用 `keys` / `send_key()`,
不给你混着写的机会。**

两个字段都给时先 `text` 后 `keys`(所以 `{"text":"y","keys":["Enter"]}`
和 `{"text":"y","enter":true}` 等价)。

发进去就返回,**不等、不确认、不知道结果** —— 因为这一层不读(§2)。
`{"ok": true}` 的意思仅仅是"字符已经交给 tmux 了"。
想知道命令跑得怎么样,让人打开那个 URL 看一眼,或者换 ssh。

### 这里没有"安全"可言,也不假装有

有人会想在这一层做命令白名单。不要做。**能打开那个终端的人本来就能敲任何东西**,
在 API 上拦 `rm -rf` 只会给人虚假的安全感,顺便挡住正当用法。
安全边界在 token 和网络那一层([01 §7](01-library.md)),不在这里。

## 6. 错误

| code | HTTP | 库里的异常 | 意思 |
| --- | --- | --- | --- |
| `no_such_session` | 404 | `NoSuchSession` | 这个 id 没有会话 |
| `session_exists` | 409 | `SessionExists` | 这个 id 已经有会话了 |
| `bad_id` | 400 | `BadId` | id 含 `.` `:` 或为空 |
| `port_in_use` | 409 | `PortInUse` | 端口上是别人的东西([01 §3.1](01-library.md)) |
| `unauthorized` | 401 | `Unauthorized` | token 不对 |
| `tmux_gone` | 503 | `TmuxGone` | tmux server 没了。**告警,别重试** |
| `not_found` | 404 | — | 没这条路由(只有 HTTP 这层有) |
| `bad_request` | 400 | `ValueError` | 请求体不是 JSON 对象,或参数不对 |

前三个是**调用方能自愈**的;`tmux_gone` 是这台机器出事了。
库里两个异常基类 —— `SessionError`(你要的东西不对,能改)和
`PlatformError`(环境不对,该告警)—— 和上表一一对应。
**HTTP 的错误码是库异常的投影,不是另立一套**:`errors.py` 里每个类带一个 `code`,
HTTP 壳原样序列化。调用方拿到 `code` 想怎么映射回自己的异常都行 ——
但那是调用方的事,tmuxd 不替它做(§8)。

**没有 `timeout`、没有 `not_a_shell`、没有 `read_only`、没有 `cmd_not_found`**
—— 对应的功能都不在了。命令跑不起来不是一种错误码,是那个会话在列表里显示 `exited`
([02 §2](02-session.md))。错误码表短是好事:它等于在说这一层能出的岔子就这几种。

## 7. 没有事件流

早先的稿子里有个 `WS /api/events` 推 `session.created` / `session.exited` 这类通知。**删掉了。**

理由和 §2 是同一个:**没人会盯着一个 tmux 的事件。** 你关心的从来不是
"3 号会话在 14:22 掉了",而是"我现在要用的那个还在不在"—— 那是一次
`GET /api/sessions`,不是一条长连接。

而且事件流不是免费的:它意味着一条要维护的 WS、一套断线重连、一堆
"漏了一条怎么办"的边界情况,换来的只是省掉几次轮询。
在这个量级上(几十个会话、状态一秒钟变不了几次),**轮询就是正确答案**,不是将就。

于是这层壳只是一个普通的 JSON HTTP 服务,没有长连接要维护 ——
薄到**用标准库的 `http.server` 就写完了**,整个包因此零运行时依赖。
一个 Web 框架在这里帮不上什么忙,却会成为每个 `pip install tmuxd` 的人的负担。

## 8. 不带客户端

早先的稿子里有个 `RemoteTmuxd` —— 一个方法名和 `Tmuxd` 对齐、走 HTTP 打到别的机器的
Python 客户端。**删掉了。**

理由和这一层的其他决定是同一条:**已经有人把这件事做好了,别再做一遍。**

- **要在 Python 里打远端,`requests` / `httpx` 就够。** 七个端点、JSON 进 JSON 出、
  没有长连接、没有流 —— 一个客户端库在这里帮不上什么忙。
- **要在命令行上打远端,用 `ssh`。**
  ```bash
  ssh box tmuxd send -t work "npm test" --enter
  ```
  这比开一个 HTTP 口更好:不用多开端口、不用管 token、复用 ssh 的鉴权和审计。
  **HTTP 那层是给"够不着 shell"的调用方留的**(别的语言、别的容器、CI runner),
  而"我有 ssh"的人本来就不需要它。
- **最要紧的一条:它是个像 `Tmuxd` 但不是 `Tmuxd` 的东西。** 方法名一样,
  但管不了 ttyd 的生死、没有 `close()`、没有 `serve_http()`、多两类错误。
  **一个看起来一样其实不一样的东西,比一个明显不同的东西更容易让人写错。**
  这个项目在别处一直在躲这种坑(不做只读、不做事件流、不做 window/pane),
  这里没理由自己挖一个。

CLI 的 `-H` 远端模式一并去掉 —— 它本来就只是套在 `RemoteTmuxd` 外面的壳,
而 `ssh box tmuxd …` 覆盖了它的全部用途([04 §5](04-cli.md))。

**HTTP 壳本身照旧。** 去掉的是"我们也提供一个客户端",不是"可以被远程调用"。

## 9. 版本

路径里没有版本号,靠 `GET /api/info` 的 `version`。
字段只增不删不改语义;要破坏兼容时才上 `/api/v2`。
库那边同理:`Tmuxd`、`Session` 两个类的方法只增不删。
