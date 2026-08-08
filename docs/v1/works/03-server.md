# 03 · server:CLI 的后端

## 1. 两条链路

tmuxd 有两种用法,**它们对 server 的需求正好相反**:

```
① 嵌进你自己的进程                    ② 命令行
   from tmuxd import Tmuxd                tmuxd new -t work
   t = Tmuxd(port=12345)                  tmuxd send -t work "..."
   t.session(id="work")                   tmuxd ls
        │                                      │
   你的进程持有实例                        每条命令都是一个新进程
   ttyd 是你的子进程                       什么都持不住
   ▼                                            ▼
   不需要 server                          **必须有个 server**
```

**① Python SDK:不需要 server。** 你的进程本来就活着,实例在你手里,ttyd 是你的子进程。
要暴露给外面,你自己那个 Web 应用已经在跑了 —— 把 tmuxd 挂进去就行(§5),
没必要再起第二个 HTTP 服务。

**② CLI:必须有 server。** 这是这条链路最本质的约束,值得说透:

> `tmuxd ls` 是一个**活几十毫秒就退出的进程**。它不能持有 ttyd(ttyd 会跟着它死),
> 也不能持有会话状态。所以它只能**去问一个持得住的东西** —— 那就是 server。

早先的稿子把 HTTP 说成"可选的暴露",于是 CLI 变成了"每条命令自己构造一个 `Tmuxd`",
而那立刻撞上一堆怪事:一条 `ls` 会顺手起一个 ttyd 又立刻带走它;
`tmuxd start` 和 `tmuxd new` 之间没有任何真正的连接。
**根子在于把 CLI 和 server 拆开看了 —— 它们是一体的。**

| | ① SDK | ② CLI |
| --- | --- | --- |
| 谁持有实例 | 你的进程 | `tmuxd serve` 起的 server |
| 要 server 吗 | **不要** | **要,而且是前提** |
| 装什么 | `pip install tmuxd` | `pip install tmuxd[server]` |
| 开几个口 | ttyd 一个 | ttyd + **管控口**,两个(§2) |
| 暴露给外面 | 挂进你自己的 app(§5) | 管控口默认只绑回环(§3) |

## 2. 两个端口,两拨用户

```
:7681   ttyd      ← 人。浏览器打开 ?arg=<id> 进终端
:7682   管控口     ← 程序。CLI 打它,别的语言也可以
```

**这两个口的用户完全不同,所以不合并。**

- **ttyd 那个是给人的** —— 它是这套设计里**唯一对人暴露的地址**
  ([02 §3](02-session.md)),`s.url` 直接贴给同事就能用;
- **管控口是给程序的** —— JSON 进 JSON 出,CLI 的每条命令就是一次调用。

合并意味着在 ttyd 前面架反代,把那个可以直接分享的地址藏到代理后面 ——
为省一个端口付这个代价不值([01 §2](01-library.md))。

**默认端口 7681 / 7682 挨着**,好记:小的那个给人,大的那个给程序。

## 3. 管控口是本机口

**默认绑 `127.0.0.1`,而且它本来就该是本机的。**

CLI 和 server 跑在同一台机器上 —— CLI 要驱动别的机器,答案是 `ssh box tmuxd …`
([04 §5.1](04-cli.md)),不是把管控口开到网上。

即便如此,**回环也不等于安全**:同一台机器上的任何用户都能连回环端口。所以:

- **管控口要 token**(`Authorization: Bearer`),和 ttyd 那个 token 可以不同 ——
  它们是两拨用户;
- 想要文件权限那种粒度,让 uvicorn 监听 **unix socket**(`--uds`)而不是端口。
  这条更接近 tmux 和 docker 的做法,但**不是默认** —— 默认给端口,因为它更好解释、
  也更容易在容器里穿出去。

**绑到 `0.0.0.0` 而没给 token 时拒绝启动**,和 ttyd 那边同一条规矩:
那是把一台机器的 shell 放到网上。

## 4. 为什么是 FastAPI + uvicorn

早先的稿子用标准库 `http.server` 写这层壳,理由是"包要零依赖"。
**现在这条理由不成立了** —— server 已经是一个**要显式安装的可选件**(§6),
用户既然已经在装东西,就该给他一个像样的东西:

- **请求校验从类型标注来**,不用手写"这个字段是不是 dict";
- **自动出 OpenAPI** —— 管控口本来就是给别的语言调的,一份 schema 省掉一整篇文档;
- **uvicorn 是正经的 ASGI server** —— keep-alive、并发、优雅关闭、信号处理。
  拿 `http.server` 手写这些,正是这个项目一直在躲的"重造已有的东西";
- **和 shellbase 一致**(FastAPI + uvicorn),同一个家族里少一套心智。

代价是两个依赖,而它们**只落在选择了 CLI + server 的人身上**。

> 这是一次**明确的反转**:上一版说"薄到用标准库就够,一个 Web 框架帮不上忙"。
> 那句话在"零依赖"的前提下是对的;前提没了,结论也就没了。

## 5. 嵌进你自己的应用

链路 ① 的人不需要 `tmuxd serve` —— **他们已经有一个 HTTP 服务在跑了。**
所以库不提供 `serve_http()`,而是给一个可挂载的 router:

```python
from fastapi import FastAPI
from tmuxd import Tmuxd
from tmuxd.server import router          # 需要 tmuxd[server]

app = FastAPI()
t = Tmuxd(port=12345, token="…")
app.include_router(router(t), prefix="/tmuxd")
```

这样鉴权、日志、CORS、限流全都走**你自己那套**,而不是 tmuxd 再发明一套。
**一个库不该在你的进程里偷偷起第二个 HTTP server。**

## 6. 依赖:默认不装

```bash
pip install tmuxd              # 库。零运行时依赖
pip install tmuxd[server]      # + fastapi + uvicorn,`tmuxd serve` 才能跑
```

**CLI 和 server 是一体的**,所以 `[server]` 这个 extra 同时是"我要用 CLI"的意思。
基础安装保持零依赖,是因为链路 ① 的人**一行 Web 框架都用不上**,
不该为别人的用法背包袱。

`import tmuxd` 永远不 import FastAPI;`tmuxd.server` 才会,而它在用到时才加载。
没装 extra 就跑 `tmuxd serve`,报错要直说:

```
✗ tmuxd serve 需要 server 依赖:pip install "tmuxd[server]"
  (只用 Python SDK 的话不需要它 —— 见 docs/v1/sdk)
```

## 7. 再划一次边界:不读,只写

**server 能做的事,是库能做的事的子集,而库本身就不提供读。**
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

## 8. 约定

**Base**:`http://127.0.0.1:<管控口>/api`。

**认证**:`Authorization: Bearer <token>`。

**格式**:请求响应一律 JSON。时间戳一律 ISO 8601 UTC(`2026-08-08T09:00:00Z`)。

**错误体**:扁平一层,可选 `details`:

```jsonc
{ "error": "session_exists",
  "message": "id \"work\" 已经有会话了",
  "details": { "id": "work" } }
```

**幂等**:`POST` 接受 `Idempotency-Key`,10 分钟窗口内重放返回原结果。
往里敲尤其要用 —— **网络重试导致重复敲一遍命令是真实事故**,
尤其当那条命令是 `terraform apply`。

**并发**:会话级操作按 id 加锁(库那层就锁了,[01 §6](01-library.md))。
往里敲**不排队** —— 字符串行进去是 tmux 本来的行为,拦它反而不对。

**权限**:只有一档,全部可读可写。没有只读 token —— 要锁往上层去锁
([02 §4](02-session.md))。

**无分页**:会话量级是个位数到几十,不设计分页。

## 9. 端点总表

**七个,一一对应库上的方法。** 这层不发明任何库里没有的东西。

| 方法 | 路径 | 库里对应 |
| --- | --- | --- |
| `GET` | `/api/sessions` | `t.sessions()` |
| `POST` | `/api/sessions` | `t.session(id, cwd, cmd, env)` —— 有则接上,无则创建 |
| `GET` | `/api/sessions/{id}` | `t.get(id)` |
| `DELETE` | `/api/sessions/{id}` | `s.kill()` |
| `POST` | `/api/sessions/{id}/keys` | `s.send(...)` / `s.send_key(...)`,见 §10 |
| `GET` | `/api/info` | `t.info()` —— 版本、两个端口、tmux 二进制与版本、会话统计 |
| `GET` | `/api/health` | 免鉴权 |

```jsonc
// POST /api/sessions
{ "id": "id5", "cwd": "/home/me/proj", "cmd": "claude" }
→ 201
{ "id": "id5", "cwd": "/home/me/proj", "cmd": "claude",
  "status": "alive", "clients": 0, "current_command": "claude",
  "url": "http://127.0.0.1:12345/?arg=id5",     // ← ttyd 的地址,不是管控口
  "created_at": "2026-08-08T09:00:00Z" }
```

`url` 那行是这层最有用的返回值:**远程调用方建完会话,拿到的是一个能直接发给人的地址。**
注意它指向 **ttyd 的端口**,不是管控口 —— 两个口,两拨用户(§2)。

**一条 WebSocket 都没有。** 唯一走 WS 的是 ttyd 自己那条终端通道,它不属于本 API。

**没有 attach 端点。** 进终端的地址是 ttyd 原生的 `?arg=<id>`,不需要这层参与
([02 §3](02-session.md))。

## 10. 往里敲

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
**要发文本就用 `text` / `send()`,要发按键就用 `keys` / `send_key()`。**

发进去就返回,**不等、不确认、不知道结果** —— 因为这一层不读(§7)。
`{"ok": true}` 的意思仅仅是"字符已经交给 tmux 了"。

### 这里没有"安全"可言,也不假装有

有人会想在这一层做命令白名单。不要做。**能打开那个终端的人本来就能敲任何东西**,
在 API 上拦 `rm -rf` 只会给人虚假的安全感,顺便挡住正当用法。
安全边界在 token 和网络那一层([01 §7](01-library.md)),不在这里。

## 11. 错误

| code | HTTP | 库里的异常 | 意思 |
| --- | --- | --- | --- |
| `no_such_session` | 404 | `NoSuchSession` | 这个 id 没有会话 |
| `session_exists` | 409 | `SessionExists` | 这个 id 已经有会话了 |
| `bad_id` | 400 | `BadId` | id 含 `.` `:` 或为空 |
| `port_in_use` | 409 | `PortInUse` | 端口上是别人的东西([01 §3.1](01-library.md)) |
| `unauthorized` | 401 | `Unauthorized` | token 不对 |
| `tmux_gone` | 503 | `TmuxGone` | tmux server 没了。**告警,别重试** |
| `bad_request` | 400 | `ValueError` | 请求体不是 JSON 对象,或参数不对 |

前三个是**调用方能自愈**的;`tmux_gone` 是这台机器出事了。
库里两个异常基类 —— `SessionError`(你要的东西不对,能改)和
`PlatformError`(环境不对,该告警)—— 和上表一一对应。
**HTTP 的错误码是库异常的投影,不是另立一套。**

**没有 `timeout`、没有 `not_a_shell`、没有 `read_only`、没有 `cmd_not_found`**
—— 对应的功能都不在了。命令跑不起来不是一种错误码,是那个会话在列表里显示 `exited`
([02 §2](02-session.md))。

## 12. 没有事件流

早先的稿子里有个 `WS /api/events`。**删掉了。**

**没人会盯着一个 tmux 的事件。** 你关心的从来不是"3 号会话在 14:22 掉了",
而是"我现在要用的那个还在不在"—— 那是一次 `GET /api/sessions`,不是一条长连接。

而且事件流不是免费的:一条要维护的 WS、一套断线重连、一堆"漏了一条怎么办"的边界情况,
换来的只是省掉几次轮询。在这个量级上(几十个会话、状态一秒钟变不了几次),
**轮询就是正确答案**,不是将就。

## 13. 不带客户端

早先的稿子里有个 `RemoteTmuxd` —— 方法名和 `Tmuxd` 对齐、走 HTTP 打到别的机器的
Python 客户端。**删掉了。**

- **要在 Python 里打远端,`requests` / `httpx` 就够。** 七个端点、JSON 进 JSON 出、
  没有长连接、没有流 —— 一个客户端库在这里帮不上什么忙;
- **要在命令行上打远端,用 `ssh box tmuxd …`** —— 不用多开端口、不用管 token、
  复用 ssh 的鉴权和审计;
- **最要紧的一条:它是个像 `Tmuxd` 但不是 `Tmuxd` 的东西。** 方法名一样,
  但管不了 ttyd 的生死、没有 `close()`、多两类错误。
  **一个看起来一样其实不一样的东西,比一个明显不同的东西更容易让人写错。**

CLI 打的是**本机管控口**,那不需要客户端库 —— 一层 `urllib` 就够,
所以**基础安装仍然零依赖**(§6)。

## 14. 版本

路径里没有版本号,靠 `GET /api/info` 的 `version`。
字段只增不删不改语义;要破坏兼容时才上 `/api/v2`。
