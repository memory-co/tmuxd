# 02 · session

## 1. 一个会话就是一个终端

tmuxd 只借 tmux 的**一件事**:

> 让一个 shell **活得比连接久**,并且**能被多个客户端同时看见**。

tmux 的另一半能力 —— window / pane 那套多路复用 —— **不用,也不暴露**。

| tmux | tmuxd |
| --- | --- |
| session | **session,就是一个终端** |
| window | — 不做 |
| pane | — 不做 |

一个会话 `new-session -d` 出来天然就是一个 window、一个 pane,tmuxd 不再动它:
不提供分屏、不提供切窗口、不提供按 window/pane 寻址。

### 为什么砍掉

**因为多路复用这件事,调用方本来就在做。** shellbase 有一整块可自由分割的画布,
你自己的脚本有一个会话列表 —— 要多个终端就多开几个会话。
在 tmuxd 里再做一层 window/pane,是把同一件事做两遍,而且两遍的模型还不一样。

砍掉之后收益是实打实的,不是洁癖:

- **target 退化成一个名字。** 没有 `work:1.2`、没有 `%7`、没有 tmux 那套目标语法要学
  ——调用方只需要知道会话叫什么;
- **`keys` 不用选 pane。** 少一个参数,也少一整类"打到别的 pane 里去了"的 bug;
- **`#{pane_current_command}` 直接就是会话的状态**,不用先问"哪个 pane 是活动的";
- **和 webmuxd 对称了。** 那边不做 pane(一块 VNC 屏同时只显示一个 tab),这边也不做,
  两个产品的模型是同一个形状。

### 人自己分屏了怎么办

不禁。tmux 的按键还在,你 attach 进去按 `C-b %` 照样分屏 —— 那是你在用 tmux,不是在用 tmuxd。

规则很简单:**tmuxd 的所有操作一律把 `-t <session>` 交给 tmux 自己解析**,
也就是落在**当前活动 window 的当前活动 pane** 上。这是 tmux 的默认行为,天然正确,
tmuxd 一行特判都不用写。

会话池是 tmuxd 专属的([01 §4](01-library.md)),所以"多 window/pane 的会话"只能是
attach 进去的人自己分出来的。规则一样:能 attach、能 send,只认活动的那个。
tmuxd 不假装看不见它们,也不假装能管它们。

## 2. 身份:一个 id

**一个会话就三个字段:**

| 字段 | 说明 |
| --- | --- |
| `id` | 会话身份。调用方给;不给则 tmuxd 生成一个(像 tmux 的 `0` / `1` / `2`) |
| `cwd` | 启动目录。省略取 `TMUXD_WORKSPACE` |
| `cmd` | 启动命令。省略跑 `$TMUXD_SHELL` |

```python
s = t.session(id="work", cwd="/home/me/proj", cmd="npm run dev")
```

落到 `tmux new-session -d -s <id> -c <cwd> [cmd]`,一句话的事。**没有第四个字段。**

(HTTP 那层同形:`POST /api/sessions {"id","cwd","cmd"}`,见 [03 §4](03-http.md)。)

`id` 直接当 tmux 会话名用,所以受 tmux 的约束:不能含 `.` 和 `:`,不能为空。
不合法就 `400 bad_id` —— **不静默改写**,因为调用方得能凭同一个字符串再找回来,
被悄悄改过的 id 是找不回来的。

tmuxd **不校验 `cmd` 存不存在**。命令不在 PATH 里,tmux 照样把会话建起来然后立刻退出,
`ls` 里就是 `exited` —— 和你在终端里敲错命令的结果一样,不需要 tmuxd 额外发明一种错误。

### 2.1 为什么 id 由调用方给

因为**重入**:调用方需要"同一个东西,下次还能接回来"。

而"同一个东西"是什么,只有调用方知道 —— 是页面上那个块、是这次 CI 的 job、
是那个仓库目录。它能从自己的世界里算出一个稳定的字符串;tmuxd 生成的随机 id
对它毫无意义,它还得再存一张对照表,而那张表一旦和现实漂移就全乱了。

所以规则很简单:**你给 id,你负责它稳定;tmuxd 保证同一个 id 永远指向同一个现场。**

### 2.2 为什么不做 URI 寻址

早先的稿子把 shellbase 的那套 URI 身份搬了下来 ——
`claude:///home/me/proj?window=main&block=2`,外加"scheme 名即命令名"、
规范化规则、身份参数、别名注册表。**全部删掉了。**

那是 shellbase 的**布局协议**,不是终端服务的事。塞进这一层的代价是它要同时背上四样东西
(URI 规范化、身份参数语义、scheme → 命令的映射、别名表),而这四样**只服务一个调用方**。
一个下层为了一个上层的方言长出四个概念,是这类分层最典型的烂掉方式。

现在的分工干净得多:

```
shellbase:  claude:///home/me/proj?window=main&block=2
                        │  自己规范化、自己派生(它本来就有这套逻辑)
                        ▼
tmuxd:      t.session(id="main--claude-proj-2", cwd="/home/me/proj", cmd="claude")
```

**shellbase 把 URI 算成一个 id,tmuxd 只认 id。** 映射规则留在懂它的那一层。

shellbase 的重入语义一个字都不用改 —— 它的确定性映射照跑,只是产物从"内部 tmux 会话名"
变成"tmuxd 的 id",而 tmuxd 根本不需要知道这个字符串是怎么算出来的。
`block=2` 意味着什么、`window` 是页面还是窗口,这些问题在这一层**不存在**。

顺带少掉的东西:`TMUXD_ALIASES`、`uri.py`、`name_conflict` 和 `cmd_not_found` 两个错误码、
以及"scheme 是不是合法命令"这类裁决。**一层不该替另一层解释它的方言。**

## 3. 进终端:一个 URL,ttyd 原生的

```python
s = t.session(id="id5", cwd="/home/me/proj", cmd="claude")
print(s.url)        # http://localhost:12345/?arg=id5
```

**就是 ttyd 自己的地址。** 没有反向代理、没有 `/s/<id>/` 这类自己发明的路径、
没有 302 跳转 —— `?arg=` 是 ttyd 打开 `-a` 之后原生就有的传参方式,
tmuxd 只是把 id 填进去([01 §2](01-library.md))。

早先的稿子在这里放过一个 `GET /api/attach` 端点,收到 id 后查会话、按需创建、
再 302 到 ttyd。**删掉了** —— 那一跳的三件事,现在各归各位:

| 那一跳原本干的 | 现在归谁 |
| --- | --- |
| 有则接上、无则创建 | `t.session(...)` 本来就是这个语义(§2) |
| 记 `last_attached` | ttyd 连上来时 `attach.sh` 那边记 |
| 302 到真正的地址 | 不需要了,`s.url` 直接就是真正的地址 |

少一跳、少一个端点、少一次浏览器重定向,而且**这个 URL 可以直接贴给人**——
它不依赖任何还活着的 Python 进程去解析(只要 ttyd 还在)。

**`t.session(...)` 的语义就是 `tmux new-session -A`:有则接上,无则创建。**
这是 tmux 里最常用的一条命令,tmuxd 把它做成了一个 Python 方法。
不想要"无则创建"就用 `t.get(id)`,不存在抛 `NoSuchSession`。

已存在的会话**不会**因为这次给的 `cwd` / `cmd` 不同而被重建 —— **id 说了算**。
要换命令就先 `s.kill()` 再建,显式的。

### 3.1 会话只能由库创建

ttyd 的 `-a` 意味着有人能直接敲 `?arg=rogue`。所以 `attach.sh` 在 pty 创建点挡了一道:
会话不存在就直接退出,**它从不创建会话**。脚本和理由见 [01 §9](01-library.md)。

于是"谁在什么时候开了什么"这件事,必然经过库,记得住。

## 4. 分享,以及为什么这一层不锁

**tmuxd 里全部可读可写。** 没有只读 attach,没有只读链接,没有 `read_only` 错误码。

### 4.1 只读在这一层是假的

ttyd 默认只读、webmuxd 抄了这个默认 —— 那在**它们**的处境下是对的。
但把同一套搬到 tmuxd 会得到一个骗人的东西:

> 一个能列会话、能开新会话的 token,给他一个"只读 attach"有什么意义?
> 他直接 `t.session(id="x", cmd="tmux -L tmuxd attach -t work")` 开一个新会话,就进去了。

只读要真的成立,前提是"**这个凭据只能碰这一个会话、而且只能读**"。
那不是一个开关,那是一个**授权模型**:得先有身份,才谈得上谁能看谁能写。
tmuxd 只有一个 token,它的安全模型自始至终是一句话 ——
**拿到 token 即拥有这台机器的 shell**。在这个前提下加一个只读开关,
只会让人以为自己有边界,而实际上没有。这比没有边界更糟。

### 4.2 所以锁在上层

真要区分"谁能看、谁能写",在**有身份的那一层**做:

| 上层 | 怎么锁 |
| --- | --- |
| **shellbase 这类平台** | 它有自己的用户体系和 window 粒度的分享;它决定给谁渲染一个能敲字的 iframe,给谁渲染一个禁掉输入的 |
| **反代 / 网关** | 架在 ttyd 前面,按你自己的登录态决定放不放行、把 `?arg=` 限死在某个 id 上;HTTP 那层同理,放 `GET /api/sessions`、不放 `POST /keys` —— 这是在有身份的地方判的,才作数 |
| **你自己的编排程序** | 它本来就持有全部能力,爱怎么分发就怎么分发 |

**tmuxd 给上层的是完整能力,不是残缺能力。** 上层拿完整的,自己往下切;
下层给残缺的,上层只能干瞪眼。这个方向不能反。

顺带的好处:`attach.sh` 没有分支([01 §9](01-library.md))、ttyd 一律 `-W`、
接口少一个参数、错误码少一条、库少一个布尔。**少掉的这些都是不会写错的地方。**

### 4.3 也因此没有 share

早先的稿子里有个 `tmuxd share`:签一个**限定到单个会话、带过期**的一次性 token,
这样不用把主 token 发出去。**删掉了** —— 不是因为想法不好,是因为**这个架构里没地方放它**。

进终端的入口是 ttyd,而 ttyd 的鉴权是 `-c user:pass`,**进程级的**:
它只知道"这个连接有没有凭据",不知道也管不了"这个连接该不该看那个 id"
([01 §7](01-library.md))。要按会话签凭据,就得在 ttyd 前面架一层能判凭据的代理 ——
而那一层刻意不做(§4.2)。

所以要把一个会话交给别人,只有两条路,都得说清楚:

- **给 URL 和 token** —— 等于把这批会话**全部**交出去,因为他能把 `?arg=` 换成任何 id;
- **在上面套你自己的代理** —— 按你的用户体系发一个只指向那一个 id 的链接。
  这正是 §4.2 说的"锁在上层",share 只是它的一个具体形态。

**一个能力只在有身份的地方才做得对,在这一层做出来的都是赝品。**

## 5. 多客户端

tmux 的协作哲学是:会话独立于客户端存在,客户端只是"看向"它的窗口 —— 可以有任意多个,
来了就镜像、走了不影响。tmuxd 原样继承,**零额外工作**:

- N 个浏览器 attach 同一个会话 = N 个 tmux 客户端,输出实时镜像,**每一个都能敲**;
- **人和程序也是这个关系**:你在网页里敲的和 `s.send(...)` 打进去的,进的是同一个终端。
  这不是我们做的功能,是 tmux 白送的性质;
- 尺寸冲突用 `window-size latest` 解决(跟随最后操作的客户端),否则所有人被最小的那个窗口截断;
- `#{session_attached}` / `list-clients` 给出当前挂了几个客户端,作为 `s.clients` 返回
  —— 调用方在 `clients > 1` 时该二次确认再删。

**不做"接管 / 交还"开关。** webmuxd 需要那个(人在 VNC 里点鼠标和 API 发指令会打架),
tmuxd 不需要:终端的字符流本来就是串行进去的,两个客户端一起敲,tmux 的行为几十年来
所有人都知道是什么样。别发明新语义。

## 6. 生命周期:谁来决定杀

**detach 不杀会话。关网页不杀会话。客户端全断开也不杀会话。
你的 Python 进程退出也不杀会话。**
只有显式 `s.kill()` 才 `tmux kill-session`。

这条要专门写下来,因为 shellbase 那边是**"关闭即销毁"**(关掉块 = 杀 tmux 会话 + 删 state)。
那是**它的**策略,不是这一层的:shellbase 知道"这个块没了就再也没人要这个会话了",
tmuxd 不知道 —— 它面对的调用方可能只是刷新了一下页面。

> **规矩:tmuxd 不替调用方决定何时销毁。** 想要"关闭即销毁"的调用方自己调 `s.kill()`。

`with Tmuxd(...) as t` 退出时收的是 **ttyd**,**不碰会话**([01 §3](01-library.md))——
上下文管理器管的是你起的那个门面,不是屋子里的人。

删除时会话正被别人 attach 也照删(tmux 会把所有客户端踢出),返回里带上被踢掉的
`clients` 数,让调用方能在 UI 上解释发生了什么。

没有 `kill_window` / `kill_pane` 这类方法 —— 因为没有 window 和 pane(§1)。
会话是**唯一**的生命周期单位,创建、attach、销毁,三个动词到头。

## 7. 对账与回收

state 说的(应然)和 `tmux ls` 里实际存在的(实然)会漂移,tmuxd 负责对齐。

**触发时机**:每次 `t.sessions()` 时顺带,就这一个。

**不起后台线程,不设定时器。** 这是"库"和"服务"的一处真实差别:
一个被 `import` 进来的东西,不该背着调用方每 60 秒醒一次、写一次盘。
对账是**读的时候顺手做的**,谁也不用为不看的东西付代价。

真要常驻(`tmuxd serve`,[04 §2](04-cli.md)),那个壳自己按需要去轮询 —— 那是壳的自由,
不是库的默认。

| 情况 | 处理 |
| --- | --- |
| **state 有、tmux 无** | 标 `status: exited`,**保留记录**。调用方可能还引用着它,凭 cwd/cmd 能重建 |
| **tmux 有、state 无** | 标 `external: true`,**不杀、不收编**,并记一条 warning 日志 |
| **两边都有** | 正常,顺带刷新 `clients`、`current_command` 等实时字段 |

**正常情况下 `external` 一条都不该有。** 会话池是 tmuxd 专属的
([01 §4](01-library.md)),里面的东西按定义都是 tmuxd 开的 ——
出现 `external` 只意味着一件事:有人绕过 tmuxd,直接往那个 socket 里
`tmux -L tmuxd new-session` 了。

所以它是**漂移,不是场景**。tmuxd 的处理是"看见、说出来、但不动手":

- **列出来**,标 `external: true`,`ls` 里显眼;日志里记一条 warning,附一句
  "这个会话不是 tmuxd 开的,它不参与重建";
- **能 attach / send / kill** —— 既然它就在那儿,残废地对待它没有好处;
- **绝不杀、绝不收编** —— 不给它补一份 state 假装是自己开的,那会把一个可见的异常
  变成一个看不见的谎。

这条设计跟 shellbase 的做法一致(它那边 `external` 是"用户在终端里手工 `tmux new`"),
差别在于 tmuxd 用专属 socket 把这种情况从"常态"压到了"不该发生"。

**GC 只是兜底,永远不杀活着的会话。** 它清的是 `status: exited` 且超过保留期
(默认 7 天,`TMUXD_GC_TTL`)的 state 文件 —— 纯粹是删几个 JSON。
任何情况下 GC 都不会调 `kill-session`:一个后台服务自作主张杀掉别人跑了三天的会话,
是这类工具最不可原谅的行为。

## 8. Session 对象

`t.sessions()` 拿到的东西,一次说清(HTTP 那层原样序列化成 JSON):

```jsonc
{ "sessions": [
  { "id": "main--claude-proj-2",     // 调用方给的,tmuxd 不解释它怎么来的(§2.2)
    "cwd": "/home/me/proj",
    "cmd": "claude",
    "status": "alive",                // alive | exited
    "clients": 2,                     // 当前 attach 的客户端数
    "current_command": "claude",      // #{pane_current_command},终端里跑的是什么
    "created_at": "2026-08-08T09:00:00Z",
    "last_attached": "2026-08-08T10:30:00Z",
    "url": "http://localhost:12345/?arg=main--claude-proj-2" },

  { "id": "hotfix", "status": "alive",
    "external": true,                 // ← 有人绕过 tmuxd 直接开的,§7
    "clients": 1, "current_command": "vim",
    "url": "http://localhost:12345/?arg=hotfix" }   // ← 没有 cwd/cmd 记录,其余照常
] }
```

**没有 `windows` 计数,没有 pane 列表,没有 `uri`** —— 不做的东西不出现在这里
(§1、§2.2)。字段少到可以背下来:id、cwd、cmd,加上探出来的几个实时字段,和一个 `url`。

`clients`、`current_command`、`status` 是**现场探的**,不是 state 里读的
—— 属性每次访问都问一次 tmux,不缓存([01 §6](01-library.md))。

Session 上的方法也就五个:`send()`、`send_key()`、`rename()`、`kill()`,加一个 `url` 属性。
**没有 `capture()`、没有 `run()`、没有 `stream()`** —— 这一层不读([03 §2](03-http.md))。
`current_command` 尤其有用:它是"这个会话现在在干嘛"最便宜的答案,
`ls` 里显示它,一眼就知道这个会话是闲着的 shell 还是正跑着 Agent。
