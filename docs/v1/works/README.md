# tmuxd

**tmuxd = tmux 的 server,长出一个 HTTP 口。**

`ttyd tmux new -A -s work` 这条命令,大家都写过。它把两个东西拼在一起:tmux 负责会话活着,
ttyd 负责让你在浏览器里看见。拼是拼上了,但拼出来的东西**没有把手**——
会话是谁开的、开在哪个目录、还活着没有、里面刚才输出了什么,一概问不到;
想让程序驱动它,只能 ssh 进去敲 `tmux send-keys`。

tmuxd 就是把这条命令做成一个服务:**会话由 API 管,由 URI 定位,能被程序驱动。**

| 来自 | 能力 | 在 tmuxd 里 |
| --- | --- | --- |
| **tmux** | 多路复用 + 持久化 + attach/detach | 不复刻,**直接用真的 tmux**(独立 socket) |
| **ttyd** | 暴露成网页,能看、能操作、能分享链接 | `/s/<name>/`,分享默认只读 |
| **tmuxd 自己加的** | 会话管理 API + 程序化 I/O + SDK | `/api/sessions`、`keys` / `capture` / `run` / `stream` |

第三块是终端世界一直缺的那块。tmux 有 `send-keys` 和 `capture-pane`,
但它们只能在**那台机器的 shell 里**敲;没人把它们做成一个能从外面调、能被 SDK 封装的接口。

## 一条硬承诺:tmuxd 不是黑盒

tmuxd 的 session **就是 tmux 的 session**。你 ssh 进那台机器:

```bash
tmux -L tmuxd ls
tmux -L tmuxd attach -t work        # 和网页上看到的是同一个现场
```

tmuxd 不发明会话模型、不发明持久化、不发明协议。它只是给已经存在的那个 tmux server
配了一个 HTTP 门面。**门面挂了,屋子还在**(见 [01 §4](01-server.md))。

## tmux / ttyd 对照

| tmux / ttyd | tmuxd |
| --- | --- |
| tmux server | 底下**就是**一个 tmux server(默认 `-L tmuxd`) |
| tmux session | session |
| tmux window / pane | window / pane,**都保留** |
| `tmux new -s work` | `POST /api/sessions` · `tmuxd new -s work` |
| `tmux new-session -A` | `GET /api/attach?target=work`(无中生有) |
| `tmux attach -t work` | 浏览器打开 `/s/work/` |
| detach(`C-b d`) | 关掉网页 |
| `tmux attach -r` | `?mode=ro` · `tmuxd share`(默认只读) |
| `tmux ls` | `GET /api/sessions` · `tmuxd ls` |
| `tmux send-keys` | `POST /api/sessions/{t}/keys` |
| `tmux capture-pane -p` | `GET /api/sessions/{t}/capture` |
| `tmux pipe-pane` | `WS /api/sessions/{t}/stream` |
| scrollback / `history-limit` | `capture?start=-` · 可选的输出录制 |
| `tmux kill-session` | `DELETE /api/sessions/{t}` |
| `-L name` / `-S path` | 同 —— tmuxd 用它跟你自己的 tmux 隔离 |
| **ttyd** `-p PORT` | `:7681`(**站在 ttyd 原来的位置上**) |
| **ttyd** 默认只读,`-W` 才可写 | `share` 默认只读,`--writable` 才可写 |
| **ttyd** `-b base-path` | `/s/<name>/` |
| **ttyd** `-a` URL 传参 | `attach` 端点的 `target` |

**唯一多出来的概念是 URI 寻址**([02 §2](02-session.md)),而且它是可选的 —— 用名字就够了。

## 60 秒上手

```bash
pip install tmuxd
tmuxd start                        # 起 daemon,打印 token 和 URL
tmuxd new -s work -c ~/proj
open http://localhost:7681/s/work/ # 浏览器里就是终端
```

```python
from tmuxd import Server

t = Server("http://localhost:7681", token="...").session("work")

t.send("npm test", enter=True)
print(t.capture())                        # 看屏幕
r = t.run("git rev-parse HEAD")           # 要退出码就用 run
print(r.exit_code, r.output)
print(t.share(read_only=True))            # 给同事的围观链接
```

一边跑脚本,一边在网页里看着它敲;卡住了你自己接管敲两下,脚本继续。
**人和程序 attach 的是同一个 pane** —— 这是 tmux 白送的,不是我们做的。

### 顺手接管你现在的 tmux

```bash
tmuxd start --tmux-socket default    # 不新建 tmux server,接管你已经在用的那个
tmuxd ls                             # 你手里那些会话,现在都有网页了
```

这大概是它最短的一句价值主张:**给你现有的 tmux 开一个口。**

## 文档

| 文件 | 内容 |
| --- | --- |
| [01-server.md](01-server.md) | 进程模型、两个监听、部署形态、状态存哪、崩了怎么办 |
| [02-session.md](02-session.md) | 会话身份(名字 / URI)、无中生有 attach、只读与分享、对账回收 |
| [03-io.md](03-io.md) | 程序化 I/O:`keys` / `capture` / `run` / `stream`,以及它们的真实坑 |
| [04-api-and-sdk.md](04-api-and-sdk.md) | HTTP API 总表 + Python SDK(同一套东西的两个壳) |
| [05-cli.md](05-cli.md) | 命令行,照 tmux 设计;以及它为什么不取代 tmux |
| [06-consumers.md](06-consumers.md) | 谁该用它:shellbase 的迁移清单、与 webmuxd 的关系 |

## 明确不做

保持它是个工具,不是平台。判断新功能该不该加,问一句:**tmux 会做这个吗?** 不会就别加。

- ❌ **自研终端渲染** —— 网页那半是 ttyd 的活,不重写
- ❌ **布局 / 分屏 UI** —— tmux 自己有 pane,网页上的画布是 [shellbase](06-consumers.md) 的事
- ❌ **多租户 / RBAC / 配额** —— 拿到 token 即拥有这台机器的 shell,边界靠网络和容器
- ❌ **数据库** —— 状态是几个小 JSON,真相在 `tmux ls` 里
- ❌ **跨机器编排 / 会话池 / 调度** —— 要多机就多台机器各跑一个 tmuxd
- ❌ **expect 式脚本 DSL** —— `run` 和 `wait_for` 到此为止,再往上是调用方的事
- ❌ **内置 LLM** —— 它是手和眼([03](03-io.md)),大脑你自己接

## 里程碑

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 门面** | daemon(FastAPI + 子进程 ttyd)+ 会话 CRUD + `/s/<name>/` attach + token 鉴权 | `tmuxd new` 后浏览器里能用终端;`tmuxd` 重启,会话无损 |
| **M2 I/O** | `keys` / `capture` / `run` / `resize` + 错误码 | 脚本能开会话、跑命令、拿到退出码和输出 |
| **M3 SDK + CLI** | Python SDK(同步 + 异步)、CLI 全套、`-H` 远端 | `pip install tmuxd` 后能远程驱动另一台机器的会话 |
| **M4 流与分享** | `WS stream`(pipe-pane)、`WS events`、`share` 一次性 token、只读 attach | 同事拿只读链接围观 Agent 跑;`tmuxd stream` 像 `tail -f` |
| **M5 收编** | URI 寻址 + scheme 即命令、对账与 GC、shellbase 切过来 | shellbase 删掉 `attach.sh` 与本地终端注册表,功能不回退 |
