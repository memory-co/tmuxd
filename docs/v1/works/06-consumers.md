# 06 · 谁在用它

tmuxd 是**被剥离出来的东西**,不是凭空长出来的:shellbase 里那套
"ttyd + tmux + attach.sh + 终端注册表"已经跑通了,只是被焊死在一个 Web 工作台里。
把它拆出来单独站着,是因为**它对别的调用方也成立**。

这一章说三件事:shellbase 怎么切过来、和 webmuxd 什么关系、以及故意留给调用方的东西。

## 1. shellbase:迁移清单

shellbase(`docs/v1/works/design.md`)是一个 Web 工作台:
可自由分割的画布,每个块装一个应用(终端 / Agent / 文件 / 浏览器)。
它的终端子系统 = tmuxd 的全部功能 + 一点它自己的布局语义。

切过来之后,它的后端少掉一整块:

| shellbase 现在的东西 | 切过来之后 |
| --- | --- |
| `bin/attach.sh`(25 行,含 state 校验) | **删掉**,tmuxd 自带([02 §3.1](02-session.md)) |
| `server/shellbase/terminals.py`(330 行:注册表 / 302 attach / 对账 / Agent 会话) | 缩成对 tmuxd 的 HTTP 调用 |
| 拉起并守护 ttyd 子进程(`cli.py` 的一部分) | **删掉**,归 tmuxd 管([01 §2](01-server.md)) |
| `/tty/` 反代 + WS 子协议协商(`gateway.py` 的一部分) | 改成反代到 tmuxd(或干脆让 iframe 直连 tmuxd) |
| `state/terminals/*.json` + 对账循环(backend.md §7) | **删掉**,tmuxd 的 state 就是这个([02 §7](02-session.md)) |
| `SHELLBASE_TMUX_SOCKET` / `SHELLBASE_TMUX_CONF` | 变成 tmuxd 的配置 |
| 布局 `windows/*.json`、URL bar、文件 API、协作广播 | **原样留着** —— 这些不是终端服务的事 |

具体到接口:

```
旧:iframe.src = /api/terminals/attach?uri=<完整 URI>          → 302 /tty/?arg=<内部名>
新:iframe.src = /tmuxd/api/attach?target=<同一条完整 URI>      → 302 …/tty/?arg=<派生名>

旧:DELETE /api/terminals?uri=<完整 URI>
新:DELETE /tmuxd/api/sessions/<派生名>        # 或让 shellbase 保留一层按 uri 的薄转发

旧:GET /api/terminals?window=<id>
新:GET /tmuxd/api/sessions,shellbase 自己按 uri 的 window 参数过滤
```

**关键:shellbase 的完整 URI 原样往下传。** tmuxd 不解释 `window` / `block`,
只承诺"规范化后不同即不同会话"([02 §2.1](02-session.md))——
所以 shellbase 的归属不变量、`block` 隔离、重入语义**一个字都不用改**,
它们本来就是建立在"同一条 URI = 同一个现场"之上的。

**两处策略必须留在 shellbase,不能下沉:**

1. **"关闭即销毁"**(backend.md §4.2)——关掉块要杀会话,这是 shellbase 知道的事,
   tmuxd 不知道([02 §6](02-session.md))。所以是 shellbase 在关块时显式发 `DELETE`;
2. **`window` / `block` 的分配与补全**(uri.md §4)——落位时算最小空闲 `block` 号,
   是布局层的职责。tmuxd 只在拿到两条不同 URI 时给出两个不同会话。

反过来,shellbase 会**白拿**几样它现在没有的:

- **程序化 I/O**([03](03-io.md))。shellbase 的 api/terminals.md 里明写着
  "不做的事:终端输入/输出",理由是"程序化需求用 tmux 自己的 `send-keys`/`capture-pane`
  在终端里解决"。那个理由在 shellbase 的处境下是对的(用户就在容器里,手边就有 shell);
  但一旦终端变成独立服务,**调用方可能根本不在那台机器上**,`send-keys` 就够不着了。
  所以 tmuxd 必须补上这一层 —— 这是本次剥离带来的**唯一一处能力扩张**,
  也是 shellbase 顺带获得的:它以后想做"给 Agent 块发一条指令"或"把 Agent 的输出喂给模型",
  接口已经在了;
- **只读围观链接**(`share`)。shellbase 的协作是 window 粒度的(collab.md §3),
  没法单独分享一个终端;tmuxd 的 `share` 补上了这条,shellbase 想用随时可用;
- **门面重启会话无损**。现在 ttyd 是 shellbase 的子进程,shellbase 升级重启会踢掉所有终端连接;
  切过来之后 tmuxd 独立跑,shellbase 重启不影响任何会话。

## 2. webmuxd:姊妹产品

webmuxd(`docs/v1/works/README.md`)的定盘星是:
**"webmuxd ≈ tmux + ttyd,只是 pane 里渲染的不是 tty 字符,是浏览器像素。"**

tmuxd 就是那句话去掉后半段 —— **字符版的本体**。两边刻意保持同构:

| | tmuxd | webmuxd |
| --- | --- | --- |
| session 里是什么 | 一个 tmux 会话 | 一整套 kasm + Chrome + sessiond |
| 往里敲 | `POST /keys`(`send-keys`) | `POST /api/act`(点击/输入) |
| 读出来 | `GET /capture`(`capture-pane`) | `GET /api/observe`(元素表 + 标注截图) |
| 人怎么看 | ttyd 的网页 | KasmVNC 的画面 |
| 分享 | 默认只读(抄 ttyd) | 默认只读(抄 ttyd) |
| 历史 | scrollback / 录制 | 操作日志 |
| **需要 runtime 抽象吗** | **不需要** —— 只有一种拉法,而且不跟着 daemon 死 | 需要 —— container / process / remote 三种 |
| **做 pane 吗** | **做** —— 底下是真 tmux | 不做 —— 一块 VNC 屏只显示一个 tab |
| 默认部署形态 | pip 装在机器上 | 容器 |

错误码的二分(能自愈的 vs 该告警的)、`-t` 目标语法、`share` 的默认只读、
"不给 `-t` 又有多个会话就报错不猜"——这些约定两边一致,是为了让**同一个调用方**
同时驱动一个终端和一个浏览器时,手感是一套。

一个自然的组合:webmuxd 负责"看网页、点按钮",tmuxd 负责"跑命令、看输出",
上面一个编排程序把两只手接起来。谁都不必知道对方存在。

## 3. 直接用它的场景

不经过任何平台,tmuxd 自己就成立:

**① 给现有的 tmux 开个口。** 最短的一条:

```bash
tmuxd start --tmux-socket default
```

你已经在跑的那些会话立刻有了网页、有了 API、能分享给同事。零迁移。

**② Agent 宿主。** 在若干个仓库目录里各跑一个 CLI Agent,程序化投喂任务、
程序化读输出,同时给每个会话一条只读链接贴进工单 —— 人随时能围观、能接管。
代码见 [04 §5](04-api-and-sdk.md)。

**③ 远程运维 / CI。** 需要"在那台机器的那个会话里"跑命令(有环境、有现场、有人盯着),
而不是起一个干净的 ssh。`tmuxd -H … run --exit-code` 就是这个用途。
不需要这个"现场"属性的批处理,**请用 ssh**([03 §4](03-io.md))。

**④ 结对 / 教学。** `tmuxd share` 出一条只读链接,对方浏览器打开就能看你敲命令;
要他接手就发 `--writable` 的那条。tmux 的 pair programming,不用给对方开账号。

## 4. 故意留给调用方的东西

划清界线,避免 tmuxd 长成平台。以下都**不做**,而且都有明确的归属:

| 不做 | 归谁 |
| --- | --- |
| 布局 / 分屏画布 / 标签页 | 调用方(shellbase 的 window + 网格剖分) |
| "关闭块就杀会话"这类回收策略 | 调用方([02 §6](02-session.md)) |
| URI 里 `window` / `block` 的语义 | 调用方([02 §2.1](02-session.md)) |
| 文件管理 / 编辑器 / 浏览器面板 | 调用方(shellbase 的 files / browser) |
| 全局环境变量表、凭据自助配置 | 调用方(shellbase 的 env.md) |
| 多用户、按人授权谁只读谁可写 | 上游的认证系统;tmuxd 只有一个 token |
| 跨机器编排、会话池、调度 | 编排程序;每台机器各跑一个 tmuxd |
| 输出的结构化理解 | 调用方或模型([03 §6](03-io.md)) |

> 判断标准始终是那一句:**tmux 会做这个吗?** 不会就别加。
> tmuxd 多出来的东西只有三样 —— HTTP 门面、URI 寻址、程序化 I/O ——
> 每一样都得能说清"为什么 tmux 没有它就够用,而做成服务之后就不够了"。
