# 05 · 谁在用它

tmuxd 是**被剥离出来的东西**,不是凭空长出来的:shellbase 里那套
"ttyd + tmux + attach.sh + 终端注册表"已经跑通了,只是被焊死在一个 Web 工作台里。
把它拆成一个能 `import` 的库,是因为**它对别的调用方也成立**。

这一章说三件事:shellbase 怎么切过来、和 webmuxd 什么关系、以及故意留给调用方的东西。

## 1. shellbase:迁移清单

shellbase(`docs/v1/works/design.md`)是一个 Web 工作台:
可自由分割的画布,每个块装一个应用(终端 / Agent / 文件 / 浏览器)。
它的终端子系统 = tmuxd 的全部功能 + 一点它自己的布局语义。

切过来之后,它的后端少掉一整块:

| shellbase 现在的东西 | 切过来之后 |
| --- | --- |
| `bin/attach.sh`(25 行,含 state 校验) | **删掉**,tmuxd 自带([01 §9](01-library.md)) |
| `server/shellbase/terminals.py`(330 行:注册表 / 302 attach / 对账 / Agent 会话) | 缩成对 tmuxd **库**的几行调用 —— 同一个 uvicorn 进程里,不走 HTTP |
| 拉起并守护 ttyd 子进程(`cli.py` 的一部分) | **删掉**,归 tmuxd 管([01 §3](01-library.md)) |
| `/tty/` 反代 + WS 子协议协商(`gateway.py` 的一部分) | **保留**,但反代目标换成 tmuxd 起的那个 ttyd —— 它仍然要在自己的鉴权后面藏住 ttyd(§1.1) |
| `state/terminals/*.json` + 对账循环(backend.md §7) | **删掉**,tmuxd 的 state 就是这个([02 §7](02-session.md)) |
| `SHELLBASE_TMUX_SOCKET` / `SHELLBASE_TMUX_CONF` | **删掉** —— tmuxd 的会话池是专属的,socket 由实例名推导([01 §4](01-library.md)) |
| 布局 `windows/*.json`、URL bar、文件 API、协作广播 | **原样留着** —— 这些不是终端服务的事 |

具体到接口:

**URI 那层留在 shellbase,不下沉。** 它本来就有一套"规范化 URI → 内部会话名"的确定性映射
(uri.md §4);切过来之后这套映射**一个字都不用改**,只是产物换了个去处 ——
从"自己 `tmux new-session` 用的会话名"变成"传给 tmuxd 的 id"。

```
shellbase:  claude:///workspace/proj?window=main&block=2
                     │  自己规范化、自己派生(它本来就在做这件事)
                     ▼
              id = "main--claude-workspace-proj-2"   cwd = "/workspace/proj"   cmd = "claude"
                     │
                     ▼
tmuxd:        只收这三个字段,不问它们怎么来的([02 §2.2](02-session.md))
```

具体到接口:

```python
# shellbase 的 terminals.py,切过来之后大致就这么点
from tmuxd import Tmuxd
tmuxd = Tmuxd(port=TTYD_PORT, token=TTYD_TOKEN)        # 应用启动时一次

@router.get("/api/terminals/attach")                    # 端点还是它自己的,鉴权也还是它自己的
def attach(uri: str):
    sid, cwd, cmd = derive(uri)                         # ← 它原本就有的那套映射,一个字不改
    s = tmuxd.session(id=sid, cwd=cwd, cmd=cmd)         # 有则接上,无则创建
    return RedirectResponse(f"/tty/?arg={s.id}")        # 反代到 ttyd,和现在一样

@router.delete("/api/terminals")
def close(uri: str):
    tmuxd.get(derive(uri)[0]).kill()
```

**shellbase 对外的接口一个都不用改** —— `/api/terminals/attach?uri=` 还在那儿,
前端 iframe 照旧。变的只是这几个函数体内部:原本自己拼 `tmux` 命令行、自己维护注册表、
自己守 ttyd,现在都是库调用。

### 1.1 ttyd 还是要藏在 shellbase 后面

tmuxd 的 ttyd 鉴权是**进程级**的([01 §7](01-library.md)):拿到 token 的人可以把
`?arg=` 换成任何 id。shellbase 是多人用的,它当然不能把这个端口直接给用户。

所以 shellbase 继续做它已经在做的事:**ttyd 绑回环,只有 shellbase 的网关能碰**,
用户拿到的永远是 `/tty/`(经过 `AuthGate`)。tmuxd 少做的那一层权限,
正好由上面这一层补上 —— 这就是"锁在上层"的具体样子([02 §4.2](02-session.md))。

**三处策略必须留在 shellbase,不能下沉:**

1. **URI 的语义与派生规则**(uri.md §4)——`window` / `block` 是什么、
   scheme 怎么映射成命令、别名表怎么查,全是布局层的方言。
   一个下层为一个上层的方言长概念,是这类分层最典型的烂掉方式([02 §2.2](02-session.md));
2. **`block` 号的分配与补全**——落位时算最小空闲序号,同样是布局层的职责。
   tmuxd 只保证"两个不同的 id 是两个不同的现场";
3. **"关闭即销毁"**(backend.md §4.2)——关掉块要杀会话,这是 shellbase 知道的事,
   tmuxd 不知道([02 §6](02-session.md))。所以是 shellbase 在关块时显式发 `DELETE`。

反过来,shellbase 会**白拿**几样它现在没有的:

- **从外面往会话里敲**([03 §10](03-server.md))。shellbase 的 api/terminals.md 里明写着
  "不做的事:终端输入/输出",理由是"程序化需求用 tmux 自己的 `send-keys` 在终端里解决"。
  那个理由在 shellbase 的处境下是对的(用户就在容器里,手边就有 shell);
  但一旦终端变成独立服务,**调用方可能根本不在那台机器上**,`send-keys` 就够不着了。
  所以 tmuxd 补上了这一个动作 —— 也是本次剥离**唯一**的能力扩张;
- **单个终端的分享链接**(`share`)。shellbase 的协作是 window 粒度的(collab.md §3),
  没法单独把一个终端交出去;tmuxd 的 `share` 签的是限定到单会话、带过期的 token,
  补上了这条。注意它**不是只读** —— 只读要在 shellbase 那层判([02 §4](02-session.md));
- **ttyd 的守护逻辑不用自己写**。现在 shellbase 自己拉 ttyd、自己绑生死、自己挑端口;
  切过来之后这些归库([01 §3](01-library.md)),而且多进程 worker 各自
  `Tmuxd(port=…)` 时会**复用同一个 ttyd** 而不是互相撞端口([01 §3.1](01-library.md))
  —— 这是 shellbase 现在没有、以后会需要的。

## 2. webmuxd:姊妹产品

webmuxd(`docs/v1/works/README.md`)的定盘星是:
**"webmuxd ≈ tmux + ttyd,只是 pane 里渲染的不是 tty 字符,是浏览器像素。"**

tmuxd 就是那句话去掉后半段 —— **字符版的本体**。两边刻意保持同构:

| | tmuxd | webmuxd |
| --- | --- | --- |
| session 里是什么 | 一个 tmux 会话 | 一整套 kasm + Chrome + sessiond |
| 往里敲 | `s.send()` / `s.send_key()` | `POST /api/act`(点击/输入) |
| 读出来 | **不提供** —— 归人(attach)或 ssh | `GET /api/observe`(元素表 + 标注截图) |
| 人怎么看 | ttyd 的网页(`?arg=<id>`,原生地址) | KasmVNC 的画面 |
| 分享 | **不做** —— 给 URL 就是给全部 | 默认只读(抄 ttyd) |
| 历史 | tmux 自己的 scrollback,人往回滚 | 操作日志 |
| **需要 runtime 抽象吗** | **不需要** —— 只有一种拉法,而且不跟着进程死 | 需要 —— container / process / remote 三种 |
| **做 pane / tab 分屏吗** | 不做 —— 一个会话就是一个终端 | 不做 —— 一块 VNC 屏只显示一个 tab |
| 默认形态 | **一个 import 进去的库** | 一个 `docker run` 起来的容器 |

两处刻意的分歧值得记一笔:

- **分享这件事,一边有一边没有。** webmuxd 抄 ttyd 的"默认只读"是对的 ——
  那边一条链接能操作你带着登录态的浏览器,而"看"本身就有价值,而且它有自己的
  server 层可以按会话签 token。tmuxd 连那一层都没有(进终端的地址就是 ttyd 原生的),
  所以它诚实地不提供 share:要按会话授权,在上面套代理([02 §4.3](02-session.md));
- **多路复用的位置不一样,但结论一样。** 两边都不做分屏 ——
  webmuxd 是因为做不到(一块 VNC 屏只显示一个 tab),tmuxd 是因为不该做
  (调用方本来就在做)。殊途同归:**一个 session 就是一块屏。**

还有第三处更根本的:**webmuxd 是服务,tmuxd 是库。** 那边的 session 是一整套容器,
只能是服务;这边的 session 就是本机一个 tmux 会话,几行 subprocess 就够,
做成服务反而逼着所有人绕一圈 HTTP([01 §1](01-library.md))。

以及第四处:**webmuxd 读,tmuxd 不读。** 那边必须读 ——
`observe()` 是喂给多模态模型的观测层,没有它 webmuxd 就只是个远程浏览器。
这边不必读 —— 终端的"读"人自己会,而程序要的那种读(干净的 stdout、退出码)
ssh 给得更好([03 §7](03-server.md))。**同一个问题,两边的答案不同,
是因为对面没有一个叫 ssh 的现成答案。**

错误码的二分(能自愈的 vs 该告警的)、幂等键、"不给 `-s` 又有多个会话就报错不猜"
——这些约定两边一致,是为了让**同一个调用方**同时驱动一个终端和一个浏览器时,手感是一套。

一个自然的组合:webmuxd 负责"看网页、点按钮",tmuxd 负责"往终端里投喂",
上面一个编排程序把两只手接起来。谁都不必知道对方存在。

## 3. 直接用它的场景

不经过任何平台,tmuxd 自己就成立:

**① 给一台机器加一个终端服务口。** 最短的一条:

```bash
pip install tmuxd && tmuxd start
```

这台机器立刻有了会话 API、有了网页终端、能把某个会话分享出去 ——
而且**它原有的一切都没变**:你自己的 `tmux ls` 一个不多一个不少,
tmuxd 的会话池在自己的 socket 上([01 §4](01-library.md))。
装一个服务不该动到你手里正在跑的东西。

**② Agent 宿主。** 在若干个仓库目录里各跑一个 CLI Agent,程序化投喂任务,
同时给每个会话一条分享链接贴进工单 —— 人随时能看、能接管。
**这是它最贴合的场景**:派活是程序的事,判断干得怎么样是人的事,两件事各归各位。

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token=TOKEN)

def sid(proj):                                  # id 怎么算是**你的**事,tmuxd 不管
    return f"{job_id}--{proj.strip('/').replace('/', '-')}"

for proj in projects:
    s = t.session(id=sid(proj), cwd=proj, cmd="claude")
    s.send(task_prompt, enter=True)
    print(f"{proj}: {s.url}")                   # 把入口贴进工单

...                                             # 几小时后,另一个进程里
t = Tmuxd(port=12345, token=TOKEN)              # 复用已经在跑的那个 ttyd(01 §3.1)
for proj in projects:
    t.get(sid(proj)).send("/status", enter=True)  # 重入:同一个 id 接回同一现场
```

**这段代码从头到尾没有读过一个字符。** 它做的是"把活派下去、把入口交出去",
剩下的交给点开链接的那个人 —— 他能看全部,也能直接接手敲。
`sid()` 那两行就是整个"寻址协议",它长在调用方里([02 §2.2](02-session.md))。

**③ 长跑任务的门面。** 一个要跑几小时、中间可能需要人插手的任务
(迁移脚本、构建、交互式部署),开在 tmuxd 的会话里而不是 ssh 里 ——
断线不丢现场,谁都能点开链接看一眼、接手敲两下。
**纯批处理不需要这个,请用 ssh**([03 §7](03-server.md))。

**④ 结对 / 教学。** 把 `s.url` 和 token 发给对方,浏览器打开就进了同一个终端 ——
看得见,也能直接接手。tmux 的 pair programming,不用给对方开账号。
但要清楚这等于把**这批会话全部**交出去了:要"只给这一个"或"只能看不能敲",
得在 ttyd 前面套一层你自己的代理([02 §4.3](02-session.md))。

## 4. 故意留给调用方的东西

划清界线,避免 tmuxd 长成平台。以下都**不做**,而且都有明确的归属:

| 不做 | 归谁 |
| --- | --- |
| **只读 / 谁能写谁不能写 / 按会话分享** | 有身份的那一层:shellbase 的用户体系、ttyd 前面的反代、或你的编排程序([02 §4](02-session.md)) |
| **多个终端的组织方式**(window / pane / 标签) | 调用方 —— 要几个终端就开几个会话([02 §1](02-session.md)) |
| 布局 / 分屏画布 / 标签页 | 调用方(shellbase 的 window + 网格剖分) |
| "关闭块就杀会话"这类回收策略 | 调用方([02 §6](02-session.md)) |
| **URI / 寻址协议**(怎么从自己的世界算出一个 id) | 调用方([02 §2.2](02-session.md)) |
| 文件管理 / 编辑器 / 浏览器面板 | 调用方(shellbase 的 files / browser) |
| 全局环境变量表、凭据自助配置 | 调用方(shellbase 的 env.md) |
| 多用户、按人授权谁只读谁可写 | 上游的认证系统;tmuxd 只有一个 token |
| 跨机器编排、会话池、调度 | 编排程序;每台机器各跑一个 tmuxd |
| **读终端内容**(抓屏、拿退出码、输出流) | 人(打开 URL)或 ssh([03 §7](03-server.md)) |

> 判断标准始终是那一句:**tmux 会做这个吗?** 不会就别加。
> 而 tmuxd 比 "tmux + ttyd 手工拼起来"多出来的东西,减到最后只剩一样:
> **一个能 `import` 的把手** —— 会话有 id、有记录、能被程序建和敲、URL 拿了就能发。
> 别的每一样都得能说清"为什么手工拼的时候不需要它",说不清的都已经删掉了。
