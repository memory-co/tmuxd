# 03 · API 与 SDK

**SDK 是 HTTP API 的一层薄封装,不是第二套实现。** API 有什么,SDK 就有什么,名字一一对应。
CLI([04](04-cli.md))同理 —— 三个壳,一个内核。

本文给的是设计层面的总表与约定;逐端点的请求/响应/错误细节进 `docs/v1/api/`。

## 1. 先划边界:不读,只写

**tmuxd 不提供任何读取终端内容的接口。** 没有 `capture`,没有 `run`,没有输出流,没有录制。
往会话里**敲**是有的,而且只有这一个动作。

这条边界值得展开,因为它决定了 tmuxd 是什么:

**读终端内容这件事,要么归人,要么归 ssh。**

| 你想干嘛 | 用什么 | 为什么不是 tmuxd |
| --- | --- | --- |
| 看这个会话现在什么样 | **人 attach 进去看** | ttyd 已经把这件事做完了,而且做得比任何 API 好 |
| 程序化拿一条命令的输出和退出码 | **ssh** | 那里有干净的 stdout/stderr、真的退出码、二进制安全 |
| 程序化往一个人正在看的会话里投喂 | **tmuxd 的 `keys`** | ← 只有这件事,别的地方都办不了 |

把终端当读接口用,每一步都在交税:抓出来的是**屏幕**不是日志,受 pane 宽度硬折行;
全屏程序(vim、htop、Agent 的 TUI)抓出来只是当前一帧;想拿退出码得往命令后面拼标记,
还得先判断 pane 里是不是一个闲着的 shell。这些税**都是为了把终端伪装成 API 而付的**,
而真要 API,ssh 那条路本来就更直。

不读还顺带省掉三样麻烦:不必挂 `pipe-pane`、不必解析 ANSI、
**不必往磁盘上落一份会记下密码和 token 的明文录制**。

`keys` 之所以留下,是因为它不需要理解终端里的内容 —— 只是把字符送进去。
这正是"程序驱动一个**有人在看**的会话"的最小动作:编排程序把任务投进去,
人在旁边的网页里看着它跑,卡住了自己接管。

## 2. 约定

**Base**:`http://<host>:7681/api`。终端网页(`/s/<id>/`)和 API 同一个 origin,不用管跨域。

**认证**:`Authorization: Bearer $TMUXD_TOKEN`,或登录后的 HttpOnly Cookie。
本机 CLI 走 unix socket 时不需要 token([01 §7](01-server.md))。

**格式**:请求响应一律 JSON。时间戳一律 ISO 8601 UTC(`2026-08-08T09:00:00Z`)。

**错误体**:扁平一层,可选 `details`:

```jsonc
{ "error": "session_exists",
  "message": "id \"work\" 已经有会话了",
  "details": { "id": "work" } }
```

**幂等**:`POST` 接受 `Idempotency-Key`,10 分钟窗口内重放返回原结果。
`keys` 尤其要用 —— **网络重试导致重复敲一遍命令是真实事故**,
尤其当那条命令是 `terraform apply`。

**并发**:会话级操作(create / delete / rename)按 id 加锁。
`keys` **不排队** —— 字符往终端里串行进去是 tmux 本来的行为,拦它反而不对。
锁的粒度只有会话一种,因为会话就是终端([02 §1](02-session.md))。

**权限**:只有一档,全部可读可写。没有只读 token、没有 `read_only` 错误码
—— 要锁往上层去锁([02 §4](02-session.md))。

**无分页**:v1 会话列表量级是个位数到几十,不设计分页。

## 3. 怎么指一个会话:就是 id

**一个字符串,没有语法。**

```
/api/sessions/work            id = work
/api/sessions/main--claude-2  id = main--claude-2(调用方自己算出来的,tmuxd 不解释)
```

没有 `work:1`、没有 `work:1.2`、没有 `%7` —— tmux 那套 `session[:window[.pane]]`
目标语法在这里不存在,因为 window 和 pane 不做([02 §1](02-session.md))。
也没有 URI 寻址:那层协议留在 shellbase,tmuxd 只认 id([02 §2.2](02-session.md))。

id 直接当 tmux 会话名用,受 tmux 约束(不能含 `.` `:`),不合法就 `400 bad_id`,
**不静默改写**。

**CLI 没给 `-t` 又有多个会话时:报错,不猜。** tmux 会挑最近的,tmuxd 不 ——
往错误的终端里敲一条命令,代价比敲错终端大。

## 4. 端点总表

一共十一个,列得完。

### 会话 —— 见 [02](02-session.md)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/sessions` | 列表(现场探活 + 对账) |
| `POST` | `/api/sessions` | 新建:`{id, cwd, cmd, env}` —— **就这几个字段**([02 §2](02-session.md)) |
| `GET` | `/api/sessions/{id}` | 单个,含 `clients`、`current_command` |
| `DELETE` | `/api/sessions/{id}` | `kill-session`,响应带被踢掉的 `clients` 数 |
| `POST` | `/api/sessions/{id}/rename` | 换 id |

**会话是唯一的生命周期单位** —— 没有 `windows` / `panes` / `split` / `kill-pane`
这些端点([02 §1](02-session.md))。创建、attach、销毁,三个动词到头。

### attach 与分享 —— 见 [02 §3-4](02-session.md)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/attach?id=&cwd=&cmd=&create=` | **唯一 attach 入口**,无中生有 + `302 /tty/?arg=…` |
| `POST` | `/api/sessions/{id}/share` | 签一次性 token,`{ttl_s=3600}`。**收窄的是范围和时效,不是读写** |
| `GET` | `/s/{id}/` | 终端网页(内部就是跳 attach 端点) |

### 写入 —— 唯一的一个

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/sessions/{id}/keys` | `{text, enter}` 或 `{keys:[…]}`,见 §5 |

### server

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/server` | 版本、监听、tmux 二进制与版本、会话统计 |
| `POST` | `/api/server/shutdown` | 等价 `tmuxd stop`。**不动 tmux server,会话全活着** |
| `GET` | `/api/health` | 健康检查,免鉴权 |
| `POST` | `/api/auth/login` · `GET /api/auth/verify` · `POST /api/auth/logout` | token ↔ Cookie |
| `GET` | `/api/openapi.json` | 由 schema 生成 |

**一条 WebSocket 都没有。** 唯一走 WS 的是 ttyd 自己那条终端通道,而它不属于本 API
—— 由 attach 端点 302 引导过去([02 §3](02-session.md))。

```jsonc
// GET /api/server
{ "version": "1.0",
  "listen": "127.0.0.1:7681",
  "socket": "/run/user/1000/tmuxd/default.sock",
  "tmux": { "bin": "/usr/bin/tmux", "version": "3.3a", "socket": "tmuxd", "server_pid": 41822 },
  "ttyd": { "version": "1.7.7", "port": 39411 },
  "sessions": { "total": 4, "alive": 3, "exited": 1, "external": 0 },
  "started_at": "2026-08-08T08:00:00Z", "uptime_s": 8241 }
```

**`POST /api/server/shutdown` 不杀会话**,这条必须在文档里说死 —— 它跟 tmux 的
`kill-server` 名字像、行为完全不同([01 §2](01-server.md))。想杀 tmux server 得显式:
`tmuxd kill-server --tmux`,而且 CLI 会要确认。

## 5. 往里敲:`keys`

**两个字段,分得很开,这是有原因的:**

```jsonc
POST /api/sessions/work/keys
{ "text": "npm test", "enter": true }         // 字面量,一个字符都不解释

POST /api/sessions/work/keys
{ "keys": ["C-c", "q", "Enter"] }             // tmux 的按键名
```

| 字段 | 对应 tmux | 语义 |
| --- | --- | --- |
| `text` | `send-keys -l` | **字面量**,原样打进去 |
| `keys` | `send-keys` | tmux 键名(`Enter` `Escape` `C-c` `M-x` `Up`…) |
| `enter` | 追一个 `Enter` | `text` 的语法糖 |

`send-keys` 不加 `-l` 时会把参数当**键名**解析,于是 `send-keys "Enter the code"` 里的
`Enter` 变成回车键 —— 这是 tmux 用户人人踩过一次的坑。tmuxd 把它挡在 API 形状上:
**要发文本就用 `text`(默认走 `-l`),要发按键就用 `keys`,不给你混着写的机会。**

同一次请求里两个字段都给时,先 `text` 后 `keys`(所以 `{"text":"y","keys":["Enter"]}`
和 `{"text":"y","enter":true}` 等价)。

发进去就返回,**不等、不确认、不知道结果** —— 因为这一层不读(§1)。
`{"ok": true}` 的意思仅仅是"字符已经交给 tmux 了"。
想知道命令跑得怎么样,让人看一眼,或者换 ssh。

### 这里没有"安全"可言,也不假装有

有人会想在这一层做命令白名单。不要做。**能 attach 这个会话的人本来就能敲任何东西**,
在 API 上拦 `rm -rf` 只会给人虚假的安全感,顺便挡住正当用法。
安全边界在 token 和网络那一层([01 §7](01-server.md)),不在这里。

同一个道理也解释了为什么 `keys` 没有只读模式:
**这一层全部可读可写,要锁往上层去锁**([02 §4](02-session.md))。

## 6. 错误

| code | HTTP | 意思 | 调用方该干嘛 |
| --- | --- | --- | --- |
| `no_such_session` | 404 | 这个 id 没有会话 | 先 attach(无中生有)或 create |
| `session_exists` | 409 | 这个 id 已经有会话了 | 换 id,或改用 attach 接上 |
| `bad_id` | 400 | id 含 `.` `:` 或为空 | 换个 id,tmuxd 不替你改写 |
| `unauthorized` | 401 | token 不对 | 检查配置 |
| `tmux_gone` | 503 | tmuxd 的 tmux server 没了 | **告警,别重试** |
| `bad_request` | 400 | 参数不对 | 改代码 |

前四个是**调用方能自愈**的;`tmux_gone` 是这台机器出事了,该告警而不是重试。
SDK 里对应两个异常基类:`SessionError` 和 `ServerError` —— 和 webmuxd 的
`ActionError` / `PlatformError` 是同一个二分。

**没有 `timeout`、没有 `not_a_shell`、没有 `read_only`、没有 `cmd_not_found`**
—— 对应的功能都不在了。命令跑不起来不是一种错误码,是那个会话在 `ls` 里显示 `exited`
([02 §2](02-session.md))。
错误码表短是好事:它等于在说这一层能出的岔子就这几种。

## 7. 没有事件流

早先的稿子里有个 `WS /api/events` 推 `session.created` / `session.exited` 这类通知。**删掉了。**

理由和 §1 是同一个:**没人会盯着一个 tmux 的事件。** 你关心的从来不是
"3 号会话在 14:22 掉了",而是"我现在要用的那个还在不在"—— 那是一次
`GET /api/sessions`,不是一条长连接。

而且事件流不是免费的:它意味着一条要维护的 WS、一套断线重连、一堆
"漏了一条怎么办"的边界情况,换来的只是省掉几次轮询。
在这个量级上(几十个会话、状态一秒钟变不了几次),**轮询就是正确答案**,
不是将就。

于是 tmuxd 这边一条 WebSocket 都不需要维护 —— ttyd 那条终端通道除外,
而那条不是我们写的。

## 8. Python SDK

```bash
pip install tmuxd            # CLI、daemon、SDK 同一个包
```

```python
from tmuxd import Server

srv = Server("http://box:7681", token="...")      # 或 Server.local() 走 unix socket

t = srv.session("work")                            # 无中生有:有则接上,无则创建
t = srv.session("work", cwd="~/proj", cmd="npm run dev")
t = srv.open("work")                               # 只接不建,不存在抛 NoSuchSession

for s in srv.sessions():
    print(s.id, s.status, s.clients, s.current_command)
```

```python
t.send("npm test", enter=True)          # POST keys(text)
t.keys("C-c")                           # POST keys(keys)

print(t.attach_url())                   # 自己看
print(t.share(ttl=3600))                # 给别人:限这一个会话、限一小时

t.kill()
```

**会话上就这五个方法。** 没有 `capture()`、没有 `run()`、没有 `stream()` ——
SDK 不提供 API 没有的东西(§1)。

设计约定:

- **名字跟着 API 走**,API 叫 `keys` SDK 就叫 `keys`,不搞"更友好的"改名 ——
  出问题时能直接把 SDK 调用翻译成 curl;
- **`session()` 是无中生有,`open()` 是只接不建。** 两个动词分开,不用布尔参数,
  因为"我以为会接上结果开了个新的"是最难查的那类 bug;
- **异常二分**:`SessionError`(`NoSuchSession` / `SessionExists` / `BadId`)
  是调用方能自愈的;`ServerError`(`TmuxGone` / `Unauthorized` / `Unreachable`)是环境出事了。
  和 §6 的错误码一一对应;
- **异步同形**:`from tmuxd import AsyncServer`,方法名与参数完全一致,只是 `await`。
  同一份代码生成两套壳,不手写两遍;
- **上下文管理器只管连接,不管会话**:`with Server(...) as srv` 关的是 HTTP 连接池,
  **不会**顺手杀会话。要杀就显式 `t.kill()` —— 呼应 [02 §6](02-session.md)
  "tmuxd 不替调用方决定何时销毁"。

### 给 Agent 宿主用

最典型的一类调用方:一个编排程序,要在若干个目录里各跑一个 CLI Agent,
把任务投进去,然后**让人看着它干活**。

```python
srv = Server("http://box:7681", token=T)

def sid(proj):                                     # id 怎么算是**你的**事,tmuxd 不管
    return f"{job_id}--{proj.strip('/').replace('/', '-')}"

for proj in projects:
    t = srv.session(sid(proj), cwd=proj, cmd="claude")
    t.send(task_prompt, enter=True)
    print(f"{proj}: {t.share()}")                  # 把链接贴进工单

...
for proj in projects:
    t = srv.open(sid(proj))                        # 重入:同一个 id 接回同一现场
    t.send("/status", enter=True)                  # 继续投喂
```

`sid()` 那三行就是**整个"寻址协议"** —— 它长在调用方里,因为只有调用方知道
"同一个东西"指的是什么([02 §2.2](02-session.md))。tmuxd 收到的永远只是一个字符串。

注意这段代码**从头到尾没有读过一个字符**。它做的是"把活派下去、把入口交出来",
剩下的交给点开链接的那个人 —— 他能看全部,也能直接接手敲。
**这就是 tmuxd 的形状:程序派活,人看着办。**

## 9. 版本

路径里没有版本号,靠 `GET /api/server` 的 `version`。
字段只增不删不改语义;要破坏兼容时才上 `/api/v2`。
