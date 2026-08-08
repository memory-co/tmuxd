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
- **`keys` / `capture` / `run` 不用选 pane。** 少一个参数,也少一整类"打到别的 pane 里去了"的 bug;
- **`#{pane_current_command}` 直接就是会话的状态**,不用先问"哪个 pane 是活动的";
- **和 webmuxd 对称了。** 那边不做 pane(一块 VNC 屏同时只显示一个 tab),这边也不做,
  两个产品的模型是同一个形状。

### 人自己分屏了怎么办

不禁。tmux 的按键还在,你 attach 进去按 `C-b %` 照样分屏 —— 那是你在用 tmux,不是在用 tmuxd。

规则很简单:**tmuxd 的所有操作一律把 `-t <session>` 交给 tmux 自己解析**,
也就是落在**当前活动 window 的当前活动 pane** 上。这是 tmux 的默认行为,天然正确,
tmuxd 一行特判都不用写。

会话池是 tmuxd 专属的([01 §2.1](01-server.md)),所以"多 window/pane 的会话"只能是
attach 进去的人自己分出来的。规则一样:能 attach、能 capture、能 send,只认活动的那个。
tmuxd 不假装看不见它们,也不假装能管它们。

## 2. 身份:名字,或者 URI

会话的身份是 **tmux 会话名**。找到它有两条路:

| 寻址方式 | 长什么样 | 谁用 |
| --- | --- | --- |
| **名字** | `work` | 人、CLI、脚本 |
| **URI** | `claude:///home/me/proj?window=main&block=2` | 平台(shellbase 这类调用方) |

`target` 参数**同时接受两种**:不含 `://` 的按名字解析,含的先规范化成 URI 再确定性派生名字。
一条规则,不需要两套端点。

### 2.1 为什么要有 URI 这条路

因为调用方是程序时,"想个名字"这件事本身就是个负担,而且它需要的是**重入**:
同一个块、同一个页面、下次再打开,要接回同一个现场。名字得由某种确定性规则生成,
否则调用方就得自己维护一张"块 → 会话名"的表 —— 那张表一旦和现实漂移就全乱了。

URI 把这张表消掉了:**一条字符串自包含地指认一个现场**,规范化后确定性派生会话名。
调用方不存表,重入就是把同一条 URI 再解析一遍。

这套语义是从 shellbase 抄下来的(shellbase `docs/v1/works/uri.md`),
但**只抄机制,不抄语义**:

> **tmuxd 不解释 URI 的含义,只承诺"规范化后不同即不同会话"。**

shellbase 的 `window` / `block` 是它自己的布局概念,tmuxd 眼里它们只是 query 里两个
参与身份的字符串。tmuxd 既不知道什么是 window,也不校验它 —— 这个切口划在这里,
是为了让布局语义留在布局那一层,别渗进终端服务。

> 顺带说明一处巧合:shellbase 的身份参数恰好叫 `window`,而 tmuxd 又刚好不做 tmux 的 window。
> 两者毫无关系 —— 前者是**页面**,后者是 tmux 的窗口。tmuxd 对前者不解释,对后者不提供。

### 2.2 规范化与派生

规范化规则:

- scheme 小写、path 去尾斜杠、`%` 编码归一;
- **query 参数全部参与身份**,按键名排序后定序(这一层没有"非身份参数"了 ——
  原先唯一的那个 `mode=ro` 随只读一起删掉了,见 §4)。

派生会话名 = 可读 slug + URI 规范形的短哈希:

```
bash:///home/me                              →  bash-home-me-1a4f9c
claude:///home/me/proj?window=main&block=1   →  claude-proj-7b21e0
claude:///home/me/proj?window=main&block=2   →  claude-proj-c93d55   ← block 不同即另一个现场
claude:///home/me/proj?window=review&block=1 →  claude-proj-0e77a2   ← window 不同亦然
```

slug 是给 `tmux ls` 的人看的,**哈希才是身份**。名字长度截到 40 字符以内
(tmux 会话名不能含 `.` 和 `:`,slug 里一律换成 `-`)。

派生名和用户显式起的名字撞车时:**报 `409 name_conflict`,不猜、不加后缀**。
六位哈希撞车的概率可以忽略,真撞上了说明有人在手工起怪名字,该让他知道。

state 里存的是**原始 URI 与派生名的对照**,所以这层映射随时可审计:

```bash
$ tmuxd ls --uri
claude-proj-7b21e0   claude:///home/me/proj?window=main&block=1   alive  2 clients
claude-proj-c93d55   claude:///home/me/proj?window=main&block=2   alive  0 clients
```

### 2.3 scheme 名即命令名

URI 的 scheme 直接解释成"要跑的命令",path 是工作目录 —— 也是 shellbase 的约定,
它足够好用而且**不需要注册表**:

```
bash:///home/me/proj      →  cd /home/me/proj && bash
claude:///home/me/proj    →  cd /home/me/proj && claude
vim:///home/me/notes.md   →  cd /home/me && vim notes.md     # path 是文件:cwd 取父目录,文件名作参数
htop://                   →  cd $TMUXD_WORKSPACE && htop
```

- **path 是目录**:cwd = 该目录,命令无参启动;**path 是文件**:cwd = 父目录,文件名作第一个参数;
- 创建前 `shutil.which(<scheme>)` 确认命令在 PATH 里,不在 → `400 cmd_not_found`;
- `TMUXD_ALIASES` 可以给 scheme 起别名或钉死参数(`{"lg": "lazygit", "cc": "claude --resume"}`),
  **但它是可选增强,不是准入门槛** —— PATH 里有的命令开箱即用;
- **这不是新增权限**:能开 `bash://` 的人本来就能跑任意命令,scheme 只是把
  "去哪个目录、跑哪个命令"编码进了定位符。tmuxd 的安全模型自始至终是"拿到 token 即拥有这台机器的 shell"。

用名字创建的会话走另一条路,参数显式给:

```jsonc
POST /api/sessions
{ "name": "work", "cwd": "/home/me/proj", "cmd": "npm run dev" }   // cmd 省略则跑 $TMUXD_SHELL
```

两条路最后落到同一个 `tmux new-session -d -s <name> -c <cwd> [cmd]`。

## 3. attach:无中生有 + 302

```
调用方                      tmuxd                                ttyd            tmux
  │ GET /api/attach?target=work                                   │               │
  │─────────────────────────▶│ 解析 target → 会话名               │               │
  │                          │ tmux has-session?                  │               │
  │                          │  ├─ 有 → 更新 last_attached         │               │
  │                          │  └─ 无 → new-session -d + 写 state  │──────────────▶│
  │◀── 302 /tty/?arg=work ───│                                     │               │
  │──(浏览器 / iframe 自动跟随)────────────────────────────────────▶│ attach.sh ───▶│
```

**语义就是 `tmux new-session -A`:有则接上,无则创建。** 这是 tmux 里最常用的一条命令,
也是 ttyd 教程里那半句 —— 把它做成一个 URL,就是 tmuxd 的入口。

| 参数 | 说明 |
| --- | --- |
| `target` | 会话名或 URI(§2)。必填 |
| `create` | `false` = 关掉无中生有,不存在就 `404 no_such_session` |

**只有两个参数。** 没有 `mode` —— attach 一律是完整的读写 attach,理由见 §4。

前端(不管是 tmuxd 自带的薄壳页,还是 shellbase 那样的 iframe 调用方)**永远把 iframe 的
src 指向 attach 端点,不直接指向 `/tty/`**。浏览器对 iframe 内的 302 会自动跟随,
对调用方完全透明。好处是:会话的诞生必然经过 tmuxd,谁在什么时候开了什么,记得住。

### 3.1 创建收口:attach.sh 要校验

ttyd 的 `-a` 允许 URL 传参,那就意味着有人能直接敲 `/tty/?arg=rogue` 绕过 attach 端点。
所以 pty 创建点必须再挡一道:

```bash
# bin/attach.sh —— ttyd -W -a 调用,$1 = 会话名
# $_TMUXD_SOCK 由 tmuxd 拉 ttyd 时注入(实例名推导而来,不是用户配置项,01 §2.1)
[ -n "$1" ] || exit 1
if ! tmux -L "$_TMUXD_SOCK" has-session -t "=$1" 2>/dev/null; then
    echo "unknown session: $1 (open it via tmuxd)"; exit 1
fi
exec tmux -L "$_TMUXD_SOCK" attach-session -t "=$1"
```

**六行,没有一个条件分支** —— 这是"不做只读"带来的实现红利:
原本这里要按模式在 `attach-session` 和 `attach-session -r` 之间分叉,
还得把模式一路从 URL 传到环境变量再传到脚本。现在没有模式。

另外两处细节:

- **`attach-session` 而不是 `new-session -A`** —— 创建的活全在 tmuxd 那边干完了,
  这里只负责接上。`attach.sh` 从不创建会话,这条比 shellbase 的版本更严:
  那边为默认 `bash://` 保留了一个 `new-session -A` 兜底,tmuxd 不留;
- **`-t "=$1"`** —— `=` 前缀关掉 tmux 的前缀匹配。不加的话 `attach -t work` 会匹配上
  `workbench`,是个真实会踩的坑。

## 4. 分享,以及为什么这一层不锁

**tmuxd 里全部可读可写。** 没有只读 attach,没有只读链接,没有 `read_only` 错误码。

### 4.1 只读在这一层是假的

ttyd 默认只读、webmuxd 抄了这个默认 —— 那在**它们**的处境下是对的。
但把同一套搬到 tmuxd 会得到一个骗人的东西:

> 一个能列会话、能开新会话的 token,给他一个"只读 attach"有什么意义?
> 他直接 `POST /api/sessions` 开一个新会话,在里面 `tmux -L tmuxd attach -t work` 就进去了。

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
| **反代 / 网关** | 按路径和方法放行:只放 `GET /api/sessions/*/capture` 和 `WS /stream`,不放 `POST /keys` —— 这才是货真价实的只读,而且是在有身份的地方判的 |
| **你自己的编排程序** | 它本来就持有全部能力,爱怎么分发就怎么分发 |

**tmuxd 给上层的是完整能力,不是残缺能力。** 上层拿完整的,自己往下切;
下层给残缺的,上层只能干瞪眼。这个方向不能反。

顺带的好处:`attach.sh` 没有分支(§3.1)、ttyd 一律 `-W`([01 §8](01-server.md))、
API 少一个参数、错误码少一条、SDK 少一个布尔。**少掉的这些都是不会写错的地方。**

### 4.3 share 还在,但它不是只读

```console
$ tmuxd share -t work
http://box:7681/s/work/?t=…   (1 小时后过期)
⚠ 拿到这个链接的人能在你的机器上执行任意命令

$ tmuxd share -t work --ttl 15m
```

`POST /api/sessions/{t}/share` 签一个**限定到单个会话、带过期**的一次性 token。
它解决的不是"能不能写",而是另外两件真实的事:

- **不用把主 token 发出去** —— 主 token 能列会话、能开新会话、能杀会话,分享 token 只能碰这一个;
- **会过期** —— 发出去的东西自己会失效,不用记得回收。

这是**能力收窄**,不是权限分级。警告那一行必须打印,而且不能靠 `--writable` 之类的开关
来"确认" —— 因为根本没有另一种模式可选,发链接这个动作本身就是全部的授权。

## 5. 多客户端

tmux 的协作哲学是:会话独立于客户端存在,客户端只是"看向"它的窗口 —— 可以有任意多个,
来了就镜像、走了不影响。tmuxd 原样继承,**零额外工作**:

- N 个浏览器 attach 同一个会话 = N 个 tmux 客户端,输出实时镜像,**每一个都能敲**;
- **人和程序也是这个关系**:你在网页里敲的和 `POST /keys` 打进去的,进的是同一个终端。
  这不是我们做的功能,是 tmux 白送的性质;
- 尺寸冲突用 `window-size latest` 解决(跟随最后操作的客户端),否则所有人被最小的那个窗口截断;
- `#{session_attached}` / `list-clients` 给出当前挂了几个客户端,`GET /api/sessions` 里
  作为 `clients` 字段返回 —— 调用方在 `clients > 1` 时该二次确认再删。

**不做"接管 / 交还"开关。** webmuxd 需要那个(人在 VNC 里点鼠标和 API 发指令会打架),
tmuxd 不需要:终端的字符流本来就是串行进去的,两个客户端一起敲,tmux 的行为几十年来
所有人都知道是什么样。别发明新语义。

## 6. 生命周期:谁来决定杀

**detach 不杀会话。关网页不杀会话。客户端全断开也不杀会话。**
只有显式 `DELETE /api/sessions/{t}` 才 `tmux kill-session`。

这条要专门写下来,因为 shellbase 那边是**"关闭即销毁"**(关掉块 = 杀 tmux 会话 + 删 state)。
那是**它的**策略,不是这一层的:shellbase 知道"这个块没了就再也没人要这个会话了",
tmuxd 不知道 —— 它面对的调用方可能只是刷新了一下页面。

> **规矩:tmuxd 不替调用方决定何时销毁。** 想要"关闭即销毁"的调用方自己发 `DELETE`。

删除时会话正被别人 attach 也照删(tmux 会把所有客户端踢出),响应里带上被踢掉的
`clients` 数,让调用方能在 UI 上解释发生了什么。

没有 `kill-window` / `kill-pane` 这类端点 —— 因为没有 window 和 pane(§1)。
会话是**唯一**的生命周期单位,创建、attach、销毁,三个动词到头。

## 7. 对账与回收

state 说的(应然)和 `tmux ls` 里实际存在的(实然)会漂移,tmuxd 负责对齐。

**触发时机**:daemon 启动时一次 + 每 60s 一次 + `GET /api/sessions` 时顺带。

| 情况 | 处理 |
| --- | --- |
| **state 有、tmux 无** | 标 `status: exited`,**保留记录**。调用方可能还引用着它,凭 cwd/cmd 能重建 |
| **tmux 有、state 无** | 标 `kind: external`,**不杀、不收编**,并记一条 warning 日志 |
| **两边都有** | 正常,顺带刷新 `clients`、`current_command` 等实时字段 |

**正常情况下 `external` 一条都不该有。** 会话池是 tmuxd 专属的
([01 §2.1](01-server.md)),里面的东西按定义都是 tmuxd 开的 ——
出现 `external` 只意味着一件事:有人绕过 tmuxd,直接往那个 socket 里
`tmux -L tmuxd new-session` 了。

所以它是**漂移,不是场景**。tmuxd 的处理是"看见、说出来、但不动手":

- **列出来**,标 `kind: external`,`ls` 里显眼;日志里记一条 warning,附一句
  "这个会话不是 tmuxd 开的,它不参与重建";
- **能 attach / capture / keys / kill** —— 既然它就在那儿,残废地对待它没有好处;
- **绝不杀、绝不收编** —— 不给它补一份 state 假装是自己开的,那会把一个可见的异常
  变成一个看不见的谎。

这条设计跟 shellbase 的做法一致(它那边 `external` 是"用户在终端里手工 `tmux new`"),
差别在于 tmuxd 用专属 socket 把这种情况从"常态"压到了"不该发生"。

**GC 只是兜底,永远不杀活着的会话。** 它清的是 `status: exited` 且超过保留期
(默认 7 天,`TMUXD_GC_TTL`)的 state 文件 —— 纯粹是删几个 JSON。
任何情况下 GC 都不会调 `kill-session`:一个后台服务自作主张杀掉别人跑了三天的会话,
是这类工具最不可原谅的行为。

## 8. 会话对象

`GET /api/sessions` 返回的形状,一次说清:

```jsonc
{ "sessions": [
  { "name": "claude-proj-7b21e0",
    "uri": "claude:///home/me/proj?window=main&block=1",
    "kind": "uri",                    // named | uri | external
    "status": "alive",                // alive | exited
    "cwd": "/home/me/proj",
    "cmd": "claude",
    "cols": 120, "rows": 40,
    "clients": 2,                     // 当前 attach 的客户端数
    "current_command": "claude",      // #{pane_current_command},终端里跑的是什么
    "created_at": "2026-08-08T09:00:00Z",
    "last_attached": "2026-08-08T10:30:00Z",
    "attach_url": "/s/claude-proj-7b21e0/" },

  { "name": "hotfix", "kind": "external", "status": "alive",
    "cols": 80, "rows": 24, "clients": 1, "current_command": "vim",
    "warning": "not created by tmuxd",   // ← 有人绕过 tmuxd 直接开的,§7
    "attach_url": "/s/hotfix/" }
] }
```

**没有 `windows` 计数,没有 pane 列表** —— 不做的东西不出现在响应里(§1)。

`clients`、`current_command`、`cols/rows` 是**现场探的**,不是 state 里读的。
`current_command` 尤其有用:它是"这个会话现在在干嘛"最便宜的答案,
也是 [03 §4](03-io.md) 里 `run` 的守卫依据。
