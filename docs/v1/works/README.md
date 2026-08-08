# tmuxd

**tmuxd = tmux 的 server,长出一个 HTTP 口。**

`ttyd tmux new -A -s work` 这条命令,大家都写过。它把两个东西拼在一起:tmux 负责会话活着,
ttyd 负责让你在浏览器里看见。拼是拼上了,但拼出来的东西**没有把手**——
会话是谁开的、开在哪个目录、还活着没有,一概问不到;想从外面往里投喂一条指令,
只能 ssh 进去敲 `tmux send-keys`。

tmuxd 就是把这条命令做成一个服务:**会话由 API 管,能被程序往里敲。**

| 来自 | 能力 | 在 tmuxd 里 |
| --- | --- | --- |
| **tmux** | 一个 shell 活得比连接久,且能被多个客户端同时看见 | 不复刻,**直接用真的 tmux**(专属 socket) |
| **ttyd** | 暴露成网页,能看、能操作、能分享链接 | `/s/<id>/` |
| **tmuxd 自己加的** | 会话生命周期 API + `send-keys` + SDK | `/api/sessions`、`/api/attach`、`/api/…/keys` |

## 会话模型小到一句话

> **一个 id、一个启动目录、一条启动命令。** 没有第四个字段。

```jsonc
POST /api/sessions   { "id": "work", "cwd": "/home/me/proj", "cmd": "npm run dev" }
```

## 四条收窄

这份设计是**一路减出来的**,减掉的每一样都比留下的更能说明它是什么。

**① 一个会话就是一个终端。** tmux 的多路复用(window / pane)**不用**。
tmuxd 只借 tmux 的一件事:让 shell 活得比连接久、能被多人同时看见。
要多个终端就多开几个会话 —— 这本来就是调用方在做的事([02 §1](02-session.md))。

**② 不读,只写。** 没有 `capture`、没有 `run`、没有输出流、没有录制、没有事件流。
读终端内容这件事要么归**人**(attach 进去看,ttyd 已经做完了),要么归 **ssh**
(程序化拿输出和退出码,那条路本来就更直)。tmuxd 只留一个写入动作:
往会话里 `send-keys`([03 §1](03-api-and-sdk.md))。

**③ 全部可读可写,不做权限。** 没有只读 attach,没有只读分享链接。
拿到 token 就是拿到这台机器的 shell,在这一层做只读只是**假的安全感**。
真要区分谁能看谁能写,那需要身份系统,**往上层去锁**([02 §4](02-session.md))。

**④ 会话池是专属的,不碰你自己的 tmux。** tmuxd 对 tmux 的全部依赖是"二进制在哪",
然后一律 `tmux -L tmuxd` 开一套独立 server。你的 `tmux ls` 和它的 `tmuxd ls`
两份清单永不相交 —— 既让 tmuxd 能对自己那批会话全权负责,更让它**没有能力**
动你那些跑了三天的工作会话([01 §2.1](01-server.md))。

还有一样也减掉了:**寻址协议。** 早先的稿子把 shellbase 的
`claude:///proj?window=main&block=2` 那套 URI 身份搬了下来,现在还回去了 ——
**调用方自己算出一个 id,tmuxd 只认 id**([02 §2.2](02-session.md))。
一个下层为了一个上层的方言长四个概念,是这类分层最典型的烂掉方式。

减到最后剩下的是一句话:**让会话活着,让人看见,让程序往里敲。**

## 一条硬承诺:tmuxd 不是黑盒

tmuxd 的 session **就是** tmux 的 session,不是什么私有格式。
tmuxd 不发明会话模型、不发明持久化、不发明协议 —— 它只是给一个 tmux server
配了一个 HTTP 门面。**门面挂了,屋子还在**(见 [01 §4](01-server.md))。

排障时你当然可以 `tmux -L tmuxd ls` 进去看一眼。但那是**逃生舱,不是用法**:
正常路径下你不该需要它,更不该在那个 socket 里手工开会话。

## tmux / ttyd 对照

| tmux / ttyd | tmuxd |
| --- | --- |
| tmux server | 底下**就是**一个 tmux server(专属,默认 `-L tmuxd`) |
| tmux session | session,**一个会话 = 一个终端** |
| tmux window / pane | — **不做**,见 [02 §1](02-session.md) |
| `tmux new -s work` | `POST /api/sessions` · `tmuxd new -s work` |
| `tmux new-session -A` | `GET /api/attach?id=work`(无中生有) |
| `tmux attach -t work` | 浏览器打开 `/s/work/` |
| detach(`C-b d`) | 关掉网页 |
| `tmux attach -r` | — **不做**,一律可写 |
| `tmux ls` | `GET /api/sessions` · `tmuxd ls` |
| 会话名 | `id` —— 调用方给,tmuxd 不解释([02 §2](02-session.md)) |
| `tmux send-keys` | `POST /api/sessions/{id}/keys` —— **唯一的写入动作** |
| `tmux capture-pane` | — **不做**,要看就 attach,要抓输出用 ssh |
| `tmux pipe-pane` | — **不做** |
| scrollback / `history-limit` | 人在网页里往回滚,tmux 自己的事 |
| `tmux kill-session` | `DELETE /api/sessions/{id}` |
| `-L name` / `-S path` | 换 **tmuxd 实例**,连带换它的 tmux socket(一个概念,不是两个) |
| **ttyd** `-p PORT` | `:7681`(**站在 ttyd 原来的位置上**) |
| **ttyd** 默认只读,`-W` 才可写 | 一律 `-W`,权限交给上层 |
| **ttyd** `-b base-path` | `/s/<id>/` |
| **ttyd** `-a` URL 传参 | `attach` 端点的 `id` |

**没有多出来的概念。** 一张表看完,tmuxd 就是"tmux 的一个子集,加一个 HTTP 口"。

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

t.send("npm test", enter=True)     # 往里敲,就这一个动作
print(t.share())                   # 限这个会话、限一小时的链接,发给同事接手
```

程序把活派下去,人点开链接看着它跑,卡住了自己接管敲两下。
**人和程序敲的是同一个终端** —— 这是 tmux 白送的,不是我们做的。

### 它跑起来之后,你的 tmux 什么都没变

```bash
tmux ls          # 你自己的会话,一个不多一个不少
tmuxd ls         # tmuxd 的会话池,另一套,互不相干
```

一句话的价值主张:**给一台机器加一个终端服务口,而不动这台机器上原有的任何东西。**

## 文档

| 文件 | 内容 |
| --- | --- |
| [01-server.md](01-server.md) | 进程模型、专属 socket、部署形态、状态存哪、崩了怎么办 |
| [02-session.md](02-session.md) | 一个会话一个终端、身份就是 id、attach、分享、对账回收 |
| [03-api-and-sdk.md](03-api-and-sdk.md) | 为什么不读、端点总表、`keys`、错误码、Python SDK |
| [04-cli.md](04-cli.md) | 命令行,照 tmux 设计;以及它为什么不取代 tmux |
| [05-consumers.md](05-consumers.md) | 谁该用它:shellbase 的迁移清单、与 webmuxd 的关系 |

## 明确不做

保持它是个工具,不是平台。判断新功能该不该加,问一句:**tmux 会做这个吗?** 不会就别加。
而且还要再问一句:**这件事非得在这一层做吗?** 能往上放、能交给 ssh 的,就别自己扛。

- ❌ **读终端内容**(capture / run / 输出流 / 录制)—— 归人或归 ssh([03 §1](03-api-and-sdk.md))
- ❌ **事件流** —— 没人会盯着一个 tmux 的事件;要状态就 `GET /api/sessions`([03 §7](03-api-and-sdk.md))
- ❌ **window / pane** —— tmux 在这里只当共享 terminal 用,多路复用那部分不要([02 §1](02-session.md))
- ❌ **只读 / 权限分级 / 谁能写谁不能写** —— 这一层全部可读可写,锁在上层([02 §4](02-session.md))
- ❌ **接管用户已有的 tmux** —— 只探测二进制,一律 `-L` 开专属池([01 §2.1](01-server.md))
- ❌ **URI / 寻址协议** —— 调用方自己算 id,那层方言留在懂它的地方([02 §2.2](02-session.md))
- ❌ **自研终端渲染** —— 网页那半是 ttyd 的活,不重写
- ❌ **布局 / 分屏 UI** —— 网页上的画布是 [shellbase](05-consumers.md) 的事
- ❌ **多租户 / RBAC / 配额** —— 拿到 token 即拥有这台机器的 shell,边界靠网络和容器
- ❌ **数据库** —— 状态是几个小 JSON,真相在 `tmux ls` 里
- ❌ **跨机器编排 / 会话池 / 调度** —— 要多机就多台机器各跑一个 tmuxd

## 里程碑

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 门面** | daemon(FastAPI + 子进程 ttyd)+ 会话 CRUD + `/s/<id>/` attach + token 鉴权 | `tmuxd new` 后浏览器里能用终端;`tmuxd` 重启,会话无损 |
| **M2 写入 + CLI** | `keys` 端点(`text` / `keys` 两种形态)+ CLI 全套 + 错误码 | 脚本能开会话、往里投喂、把链接交出去 |
| **M3 SDK + 远端** | Python SDK(同步 + 异步)、`-H`、`share` 一次性 token | `pip install tmuxd` 后能远程驱动另一台机器的会话 |
| **M4 收编** | 对账与 GC、shellbase 切过来 | shellbase 删掉 `attach.sh` 与本地终端注册表,功能不回退 |
