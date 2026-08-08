# 04 · API 与 SDK

**SDK 是 HTTP API 的一层薄封装,不是第二套实现。** API 有什么,SDK 就有什么,名字一一对应。
CLI([05](05-cli.md))同理 —— 三个壳,一个内核。

本文给的是设计层面的总表与约定;逐端点的请求/响应/错误细节进 `docs/v1/api/`。

## 1. 约定

**Base**:`http://<host>:7681/api`。终端网页(`/s/<name>/`)和 API 同一个 origin,不用管跨域。

**认证**:`Authorization: Bearer $TMUXD_TOKEN`,或登录后的 HttpOnly Cookie。
本机 CLI 走 unix socket 时不需要 token([01 §7](01-server.md))。

**格式**:请求响应一律 JSON;`capture` 的纯文本模式和录制下载除外。
时间戳一律 ISO 8601 UTC(`2026-08-08T09:00:00Z`)。

**错误体**:扁平一层,可选 `details`:

```jsonc
{ "error": "not_a_shell",
  "message": "pane 里跑的是 vim,run 不适用",
  "details": { "current_command": "vim" } }
```

码表见 [03 §7](03-io.md)。

**幂等**:`POST` 接受 `Idempotency-Key`,10 分钟窗口内重放返回原结果。
`keys` 和 `run` 尤其要用。

**并发**:
- **同一个 pane 的 `run` 串行**(内部排队,不交错);
- `keys` **不排队** —— 字符往 pane 里串行进去是 tmux 本来的行为,拦它反而不对;
- 会话级操作(create/delete/rename)按会话名加锁。

**无分页**:v1 所有列表(会话、window、pane)量级都是个位数到几十,不设计分页。

## 2. target 语法

一条规则贯穿 API、SDK、CLI:

```
work                    会话 work 的当前 window 的当前 pane
work:1                  第 1 个 window
work:1.2                第 1 个 window 的第 2 个 pane
work:build              按 window 名
%7                      pane id,直接寻址
claude:///p?window=…    URI(见 02 §2),先规范化再派生会话名
```

前四种是 tmux 原生写法,原样支持。**URL 里 target 出现在路径段时必须 URL-encode**
(`work:1.2` → `work%3A1.2`),SDK 和 CLI 负责编码,调用方不用管。

省略 window/pane 时落在当前活动的那个 —— tmux 的规矩。

**没给 target 又有多个会话时:报错,不猜。** tmux 会挑最近的,tmuxd 不 ——
往错误的 pane 里敲一条命令,代价比敲错终端大。

## 3. 端点总表

### 会话 —— 见 [02](02-session.md)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/sessions` | 列表(现场探活 + 对账) |
| `POST` | `/api/sessions` | 新建(`name` / `uri` / `cwd` / `cmd` / `env` / `cols` / `rows`) |
| `GET` | `/api/sessions/{t}` | 单个,含 `clients`、`current_command` |
| `DELETE` | `/api/sessions/{t}` | `kill-session`,响应带被踢掉的 `clients` 数 |
| `POST` | `/api/sessions/{t}/rename` | 改名(URI 派生的会话拒绝改名,`409`) |
| `GET` | `/api/sessions/{t}/windows` | window 列表 |
| `GET` | `/api/sessions/{t}/panes` | pane 列表(id / 尺寸 / `current_command` / cwd) |
| `POST` | `/api/sessions/{t}/split` | 分屏(`-h` / `-v`),回新 pane id |
| `DELETE` | `/api/sessions/{t}` `?window=` `?pane=` | `kill-window` / `kill-pane` |

### attach 与分享 —— 见 [02 §3-4](02-session.md)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/attach?target=&mode=&create=` | **唯一 attach 入口**,无中生有 + `302 /tty/?arg=…` |
| `POST` | `/api/sessions/{t}/share` | 签一次性 token,`{read_only=true, ttl_s=3600}` |
| `GET` | `/s/{name}/` | 终端网页(内部就是跳 attach 端点) |

### I/O —— 见 [03](03-io.md)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/sessions/{t}/keys` | `{text, enter}` 或 `{keys:[…]}` |
| `GET` | `/api/sessions/{t}/capture` | `?start=&end=&join=&ansi=&wait_for=&timeout=` |
| `POST` | `/api/sessions/{t}/run` | `{cmd, timeout, force}` → `{exit_code, output, duration_ms}` |
| `POST` | `/api/sessions/{t}/resize` | `{cols, rows}` |
| `WS` | `/api/sessions/{t}/stream` | 原始字节流,`?since=<cursor>` |
| `GET` | `/api/sessions/{t}/record` | 下载录制(需 `TMUXD_RECORD=on`) |

### server

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/server` | 版本、监听、tmux socket、tmux 版本、会话统计 |
| `POST` | `/api/server/shutdown` | 等价 `tmuxd stop`。**不动 tmux server,会话全活着** |
| `GET` | `/api/health` | 健康检查,免鉴权 |
| `POST` | `/api/auth/login` · `GET /api/auth/verify` · `POST /api/auth/logout` | token ↔ Cookie |
| `WS` | `/api/events` | 事件流(§4) |
| `GET` | `/api/openapi.json` | 由 schema 生成 |

```jsonc
// GET /api/server
{ "version": "1.0",
  "listen": "127.0.0.1:7681",
  "socket": "/run/user/1000/tmuxd/default.sock",
  "tmux": { "socket": "tmuxd", "version": "3.3a", "server_pid": 41822 },
  "ttyd": { "version": "1.7.7", "port": 39411 },
  "sessions": { "total": 4, "alive": 3, "exited": 1, "external": 1 },
  "record": false,
  "started_at": "2026-08-08T08:00:00Z", "uptime_s": 8241 }
```

**`POST /api/server/shutdown` 不杀会话**,这条必须在文档里说死 —— 它跟 tmux 的
`kill-server` 名字像、行为完全不同([01 §2](01-server.md))。想杀 tmux server 得显式:
`tmuxd kill-server --tmux`,而且 CLI 会要确认。

## 4. 事件

```
WS /api/events
```

| type | 什么时候 |
| --- | --- |
| `session.created` | 新会话(含无中生有的) |
| `session.attached` / `session.detached` | 客户端来了 / 走了,带 `clients` |
| `session.exited` | 对账发现 tmux 里没了 |
| `session.killed` | 显式 `DELETE` |
| `session.renamed` | |
| `pane.resized` | 尺寸变了,渲染方据此重排 |
| `server.shutdown` | 门面要关了(**会话不受影响**,事件里写明) |

事件是**通知,不是真相**:漏了一条不影响正确性,调用方任何时候都能 `GET /api/sessions` 重新对齐。
不做事件重放和保序保证 —— 那需要一个持久队列,而 tmuxd 说好了不带数据库。

会话内的**输出**不走这条流,走 `/stream`([03 §5](03-io.md))。两条流分开:
一条是元数据(低频、结构化),一条是字节(高频、原始)。

## 5. Python SDK

```bash
pip install tmuxd            # CLI、daemon、SDK 同一个包
```

```python
from tmuxd import Server

srv = Server("http://box:7681", token="...")      # 或 Server.local() 走 unix socket

t = srv.session("work")                            # 无中生有:有则接上,无则创建
t = srv.session("work", cwd="~/proj", cmd="npm run dev")
t = srv.session(uri="claude:///home/me/proj?window=main&block=1")
t = srv.open("work")                               # 只接不建,不存在抛 NoSuchSession

for s in srv.sessions():
    print(s.name, s.status, s.clients, s.current_command)
```

```python
t.send("npm test", enter=True)          # POST keys(text)
t.keys("C-c")                           # POST keys(keys)
print(t.capture())                      # 当前屏
print(t.capture(start="-"))             # 全 scrollback
t.wait_for(r"Done in \d+", timeout=30)  # 等输出

r = t.run("git rev-parse HEAD")         # 要退出码就用 run
if r.exit_code != 0:
    raise RuntimeError(r.output)

t.resize(cols=200, rows=50)
print(t.attach_url())                   # 自己看
print(t.share(read_only=True, ttl=3600))# 给别人

for chunk in t.stream():                # 原始字节,断线自动带 since 续
    sys.stdout.buffer.write(chunk)

t.kill()
```

设计约定:

- **名字跟着 API 走**,API 叫 `capture` SDK 就叫 `capture`,不搞"更友好的"改名 ——
  出问题时能直接把 SDK 调用翻译成 curl;
- **`session()` 是无中生有,`open()` 是只接不建。** 两个动词分开,不用布尔参数,
  因为"我以为会接上结果开了个新的"是最难查的那类 bug;
- **异常二分**:`SessionError`(`NoSuchSession` / `NotAShell` / `Timeout` / `ReadOnly`)
  是调用方能自愈的;`ServerError`(`TmuxGone` / `Unauthorized` / `Unreachable`)是环境出事了。
  和 [03 §7](03-io.md) 的错误码一一对应;
- **异步同形**:`from tmuxd import AsyncServer`,方法名与参数完全一致,只是 `await`。
  同一份代码生成两套壳,不手写两遍;
- **上下文管理器只管连接,不管会话**:`with Server(...) as srv` 关的是 HTTP 连接池,
  **不会**顺手杀会话。要杀就显式 `t.kill()` —— 呼应 [02 §6](02-session.md)
  "tmuxd 不替调用方决定何时销毁"。

### 给 Agent 宿主用

最常见的一类调用方:一个编排程序,要在若干个目录里各跑一个 CLI Agent,
既要能程序化观察,又要能让人随时接管。

```python
srv = Server("http://box:7681", token=T)

for proj in projects:
    t = srv.session(uri=f"claude://{proj}?job={job_id}")   # 任意 query 都参与身份
    t.send(task_prompt, enter=True)
    print(f"{proj}: {t.share(read_only=True)}")            # 把围观链接贴进工单

...
for proj in projects:
    t = srv.open(uri=f"claude://{proj}?job={job_id}")      # 重入:同一条 URI 接回同一现场
    print(t.capture(start=-200))                            # 看它干到哪了
```

**人拿着那个只读链接,看到的和 `capture()` 读到的是同一个 pane。**
这是 tmux 白送的性质,也是整套东西最值钱的地方。

## 6. 版本

路径里没有版本号,靠 `GET /api/server` 的 `version`。
字段只增不删不改语义;要破坏兼容时才上 `/api/v2`。
