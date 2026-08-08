# 02 · session

## 1. 会话就是 tmux 的会话

tmuxd 不发明会话模型。一个 tmuxd session **就是**底下那个 tmux server 里的一个 session,
带着它原本的 window 和 pane。

| tmux | tmuxd | API 里 |
| --- | --- | --- |
| session | session | `/api/sessions/{target}` |
| window | window | `target` 写成 `work:1` |
| pane | pane | `target` 写成 `work:1.2`,或直接 `%7` |

**pane 保留**,和 webmuxd 不一样 —— 那边一块 VNC 屏同时只能显示一个 tab,所以它砍掉了 pane;
tmuxd 底下是真 tmux,分屏本来就有,砍掉反而要多写代码。
`target` 语法直接沿用 tmux 的 `session[:window[.pane]]`,用过 tmux 的人不用查文档。

不给 window/pane 时,操作落在**当前活动的那个** —— 也是 tmux 的规矩。

## 2. 两种寻址,一个身份

会话的身份是 **tmux 会话名**。找到它有两条路:

| 寻址方式 | 长什么样 | 谁用 |
| --- | --- | --- |
| **名字** | `work`、`build:1.0` | 人、CLI、脚本 |
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

### 2.2 规范化与派生

规范化规则:

- scheme 小写、path 去尾斜杠、`%` 编码归一;
- **剔除非身份参数**:`mode` 是唯一的内置非身份参数(只影响本次怎么打开);
- 其余 query 参数**全部参与身份**,按键名排序后定序。

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
| `mode` | `ro` = 只读 attach(`tmux attach -r`)。**只读不触发无中生有** —— 观看一个不存在的会话没有意义,直接 `404 no_such_session` |
| `create` | `false` = 关掉无中生有,不存在就 `404` |

前端(不管是 tmuxd 自带的薄壳页,还是 shellbase 那样的 iframe 调用方)**永远把 iframe 的
src 指向 attach 端点,不直接指向 `/tty/`**。浏览器对 iframe 内的 302 会自动跟随,
对调用方完全透明。好处是:会话的诞生必然经过 tmuxd,谁在什么时候开了什么,记得住。

### 3.1 创建收口:attach.sh 要校验

ttyd 的 `-a` 允许 URL 传参,那就意味着有人能直接敲 `/tty/?arg=rogue` 绕过 attach 端点。
所以 pty 创建点必须再挡一道:

```bash
# bin/attach.sh —— ttyd -W -a 调用,$1 = 会话名
[ -n "$1" ] || exit 1
if ! tmux -L "${TMUXD_TMUX_SOCKET:-tmuxd}" has-session -t "=$1" 2>/dev/null; then
    echo "unknown session: $1 (open it via tmuxd)"; exit 1
fi
exec tmux -L "${TMUXD_TMUX_SOCKET:-tmuxd}" attach-session ${TMUXD_RO:+-r} -t "=$1"
```

注意两处细节:

- **`attach-session` 而不是 `new-session -A`** —— 创建的活全在 tmuxd 那边干完了,
  这里只负责接上。`attach.sh` 从不创建会话,这条比 shellbase 的版本更严:
  那边为默认 `bash://` 保留了一个 `new-session -A` 兜底,tmuxd 不留;
- **`-t "=$1"`** —— `=` 前缀关掉 tmux 的前缀匹配。不加的话 `attach -t work` 会匹配上
  `workbench`,是个真实会踩的坑。

## 4. 只读与分享

抄 ttyd 的默认值:**ttyd 默认只读,要 `-W` 才允许客户端敲键盘。这个默认是对的。**
但要把两件事分开,不要混:

| | 谁用 | 鉴权 | 权限 |
| --- | --- | --- | --- |
| `tmuxd attach` | **你自己** | 控制 socket(文件权限)或你的 token | 完整 |
| `tmuxd share` | **给别人** | 一次性 token,带过期 | **默认只读** |

```console
$ tmuxd share -t work
http://box:7681/s/work/?t=...   (只读,1 小时后过期)

$ tmuxd share -t work --writable
http://box:7681/s/work/?t=...   (可操作,1 小时后过期)
⚠ 这个链接能在你的机器上执行任意命令
```

这个不对称是故意的:一个能在你机器上敲命令的链接,不该顺手就发出去。

**只读在 tmux 层实现,不在 ttyd 层。** ttyd 起的时候是 `-W`(可写),
只读性由 `attach.sh` 改用 `tmux attach-session -r` 保证。理由:ttyd 的可写性是**进程级**的,
一个进程服务所有会话,没法按连接区分;而 `-r` 是**客户端级**的,同一个会话可以同时挂着
一个可写客户端和三个只读客户端 —— 正是"我干活、同事围观"要的形状。

一次性 token 由 `POST /api/sessions/{t}/share` 签发,`{read_only, ttl_s}`,
默认 `read_only: true`、`ttl_s: 3600`。token 只对**这一个会话**有效,
不能拿去调 `/api/sessions` 列别的会话。

## 5. 多客户端

tmux 的协作哲学是:会话独立于客户端存在,客户端只是"看向"它的窗口 —— 可以有任意多个,
来了就镜像、走了不影响。tmuxd 原样继承,**零额外工作**:

- N 个浏览器 attach 同一个会话 = N 个 tmux 客户端,输出实时镜像,可写的那些都能敲;
- **人和程序也是这个关系**:你在网页里敲的和 `POST /keys` 打进去的,进的是同一个 pane。
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

配套的 `POST /api/sessions/{t}/kill-window`、`kill-pane` 同理:显式才动。

## 7. 对账与回收

state 说的(应然)和 `tmux ls` 里实际存在的(实然)会漂移,tmuxd 负责对齐。

**触发时机**:daemon 启动时一次 + 每 60s 一次 + `GET /api/sessions` 时顺带。

| 情况 | 处理 |
| --- | --- |
| **state 有、tmux 无** | 标 `status: exited`,**保留记录**。调用方可能还引用着它,凭 cwd/cmd 能重建 |
| **tmux 有、state 无** | 标 `kind: external`,**不杀、不收编**。列出来,可以 attach,但没有 URI/cwd 记录 |
| **两边都有** | 正常,顺带刷新 `clients`、`windows` 等实时字段 |

`external` 不是异常路径,是**一等场景**:你 ssh 进机器 `tmux -L tmuxd new -s hotfix`,
它立刻就在网页上出现;`TMUXD_TMUX_SOCKET=default` 接管你现有 tmux 时,
一开始**所有会话都是 external**。这条路必须好用,不能因为"没有 state 记录"就残废。

external 会话缺的只是 tmux 答不上来的那部分(URI、启动命令、创建者),
attach / capture / keys / kill 一概照常。

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
    "windows": 1,
    "clients": 2,                     // 当前 attach 的客户端数
    "current_command": "claude",      // #{pane_current_command},活动 pane 里跑的是什么
    "created_at": "2026-08-08T09:00:00Z",
    "last_attached": "2026-08-08T10:30:00Z",
    "attach_url": "/s/claude-proj-7b21e0/" },

  { "name": "hotfix", "kind": "external", "status": "alive",
    "windows": 3, "clients": 1, "current_command": "vim",
    "attach_url": "/s/hotfix/" }      // ← external 缺 uri/cwd/cmd,其余照常
] }
```

`clients` 和 `current_command` 是**现场探的**,不是 state 里读的。
`current_command` 尤其有用:它是"这个会话现在在干嘛"最便宜的答案,
也是 [03 §3](03-io.md) 里 `run` 的守卫依据。
