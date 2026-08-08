# 01 · 库与进程模型

## 1. 核心是一个 Python 库

**tmuxd 首先是一个你 `import` 进来的库,不是一个你连过去的服务。**

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # ← 这行之后:ttyd 起来了,tmux 还没有
s = t.session(id="id5", cwd="/home/me/proj", cmd="claude")
s.send("继续", enter=True)
print(s.url)                              # http://localhost:12345/?arg=id5
```

四行,一个终端就跑起来了,而且**浏览器打开那个 URL 就能进去**。

这一层直接跑 `tmux` 命令 —— 不经过 HTTP,不经过任何自己的协议。
CLI([04](04-cli.md))和可选的 HTTP 暴露([03](03-http.md))都是**套在这个库外面的壳**:

```
        ┌── 你的 Python 程序   import tmuxd        ← 最短的一条
        │
tmuxd ──┼── CLI               tmuxd new / send    ← 同一个库,套了个命令行
 (库)   │
        └── HTTP(可选)       POST /sessions/…    ← 同一个库,套了个 HTTP 口
                                                     给别的语言、别的机器用
```

**三个壳,一个内核,而内核是 Python。** 这个方向很重要:
不是"先有服务再配个 SDK",而是"先有库,需要的时候把它暴露出去"。
大多数调用方(编排程序、Web 后端、脚本)本来就在同一个进程里,让它们绕一圈 HTTP 去调
自己进程里就能做的事,是白交的税。

## 2. 一张图

```
┌─ 你的 Python 进程 ────────────────────────────────────────────┐
│                                                              │
│   Tmuxd(port=12345, token=…)                                 │
│     │                                                        │
│     ├── 直接 subprocess 调 tmux ──────────────────────────────┼──┐
│     │                                                        │  │
│     ├── ttyd(子进程)  :12345  ← 人从这个端口进终端           │  │
│     │      -p 12345 -a -W -c tmuxd:changeme attach.sh        │  │
│     │                                                        │  │
│     └── serve_http(port=12346)(可选,默认不起)              │  │
│                                                              │  │
└──────────────────────────────────────────────────────────────┘  │
                                                                  │
   ┌─ tmux server「tmuxd 专属」(-L tmuxd,独立进程) ──────────────┘
   │   session id5      ← 一个会话 = 一个终端,不做 window / pane(02 §1)
   │   session build
   └─  谁的子进程都不是。你的程序退了,它还在
```

**端口就是 ttyd 的端口,URL 就是 ttyd 的 URL。** 没有反向代理、没有 `/s/<id>/` 这类
自己发明的路径、没有 302 跳转 —— `?arg=<id>` 是 ttyd 原生就有的东西(`-a` 打开),
tmuxd 只是把 id 填进去。

| 监听 | 谁的 | 默认 | 说明 |
| --- | --- | --- | --- |
| `:12345`(你给的 `port`) | **ttyd** | 起 | 人进终端的唯一入口 |
| `:12346`(`serve_http` 给的) | tmuxd 的 HTTP | **不起** | 要暴露给外面才开([03](03-http.md)) |
| tmux socket | tmux | 懒起 | **tmuxd 专属**,和你自己的 tmux 不是同一个(§4) |

**没有“不起 ttyd”这个选项。** `tmuxd = tmux + ttyd`,缺一个都不成立 ——
让 shell 活得比连接久是 tmux 的活,让人从浏览器进去是 ttyd 的活;
少了后者,这东西就退化成“一个 tmux 的 Python 封装”,而那不是它。

所以构造 `Tmuxd` 就意味着**一个完整的 tmuxd 在跑**:tmux 探得到,ttyd 在听。
任一不满足,构造直接失败([06](06-dependencies.md))。

## 3. 进程模型:谁是谁的子进程

这是整个设计里最重要的一处不对称。

```
你的 Python 进程                          tmux server (-L tmuxd)
 │                                         │
 ├── ttyd(PR_SET_PDEATHSIG)                ├── session id5
 └── (可选) HTTP 线程                       └── session build
     ↑ 归你管,你退了它们就没了                ↑ 谁都不归,自己活着
```

- **ttyd 是你进程的子进程。** `Tmuxd()` 起它,绑它的生死(`PR_SET_PDEATHSIG`,
  父进程一消失内核直接给它 SIGTERM)。你的脚本跑完退出,ttyd 跟着没,
  那个 `:12345` 的网页入口也就没了。
- **tmux server 不是任何人的子进程。** 第一次 `t.session(...)` 时被
  `tmux new-session` 顺手拉起来,之后它自己活着。你的程序崩了、升级了、换成另一个版本,
  **会话统统不受影响**。

这条不对称就是这东西的全部价值:**门面短命,屋子长命。**
你可以写一个只跑三秒的脚本,派完活就退出,而它派下去的那几个 Agent 会话还在跑,
等下一次有人(或有程序)接上来。

需要门面也活着,就别让进程退出 —— 或者用 `tmuxd serve`([04 §2](04-cli.md)),
那是同一个库套了个守护进程的壳。

```python
with Tmuxd(port=12345) as t:      # 退出时收掉 ttyd
    t.session(id="job-1", cwd="/srv/app", cmd="./deploy.sh")
# 这里 ttyd 没了,job-1 还在跑
```

> `PR_SET_PDEATHSIG` 这条不能只写在 `finally` 里:进程被 SIGKILL 时 `finally` 根本不执行。
> 交给内核才可靠。这是 shellbase 踩过的坑,原样搬过来。

### 3.1 ttyd 是无状态的,所以可以复用

ttyd 在这套里只干一件事:把连接 exec 到 `attach.sh`。**它不持有任何会话状态**,
状态全在 tmux 那边。所以同一个端口上已经有一个 tmuxd 起的 ttyd 时,
`Tmuxd(port=12345)` **直接接手用**,不重复起、也不报错:

| 那个端口上是什么 | 行为 |
| --- | --- |
| 空的 | 起一个 ttyd,归我管(我退它退) |
| 已有一个 tmuxd 起的 ttyd(同 socket) | **接手用**,不重起;我退了它**不退**(不是我的孩子) |
| 别人的东西 | `PortInUse`,不猜、不抢 |

判据是状态目录里按"端口 + socket"记的一份 pidfile。这条让"Web 后端每次重启都
`Tmuxd(port=…)` 一下"变成安全操作 —— 否则每次重启都要么撞端口、要么把用户正连着的
网页踢掉。

### 3.2 那“只想看一眼”的命令怎么办

既然构造就意味着“确保 ttyd 在跑”,`tmuxd ls` 这种只读命令怎么办?

**有实例在跑时靠接手(§3.1)** —— 不重起、退出时也不带走,代价只是一次端口探测。

**没有实例在跑时,不该顺手起一个门面然后立刻带走它。** 那既浪费又违反直觉:
一条只读命令留下了副作用。正确的行为是**如实说没有**:

```console
$ tmuxd ls
✗ 没有实例在跑(端口 7681 上没人听)。先 tmuxd start。
```

这比“起一个 50 毫秒就死的 ttyd”诚实,也和 `tmuxd = tmux + ttyd` 一致:
**没有完整的 tmuxd,就没有 tmuxd 可查。**

## 4. 专属 socket:tmuxd 全权管理,不碰你的 tmux

**tmuxd 对 tmux 的全部依赖,就是知道二进制在哪。**

```
构造时只做两件事,都不启动任何 tmux 进程:
  which tmux          →  Tmuxd(tmux_bin=…) 可覆盖,默认取 PATH
  tmux -V             →  低于 3.0 直接报错(要用 `window-size`,tmux 2.9 才有)
```

`tmux -V` 不会拉起 server,所以"构造时检查"和"§4.1 server 懒起"并不矛盾 ——
早点失败比等到有人打开浏览器才失败好。

**不去发现已有的 tmux server,不去接管用户正在用的会话,不读 `~/.tmux.conf`**
(用 `-f` 指向随包配置)。

会话池永远开在一个**专属 socket** 上,由实例名推导:

| 实例 | tmux socket |
| --- | --- |
| `Tmuxd(...)` 默认 | `tmux -L tmuxd` |
| `Tmuxd(socket="ci")` | `tmux -L tmuxd-ci` |
| **你自己的 tmux** | `tmux`(default socket)—— **tmuxd 看不见,也不去看** |

`tmux -L` 开出来的是一个**完全独立的 server**:独立进程、独立 socket、独立会话空间。
这是 tmux 自己提供的隔离机制,不是我们发明的。

**为什么必须专属,而不是提供一个"接管现有 tmux"的开关:**

- **tmuxd 要能对这批会话全权负责。** 对账([02 §7](02-session.md))、无中生有、GC ——
  这些都建立在"这个 socket 里的东西都是我开的"之上。一旦掺进用户手工开的工作会话,
  每条规则都得多一句"除非这是用户的",而"用户的"和"我的"根本没有可靠的判据;
- **反过来更要紧:tmuxd 绝不该动你的工作会话。** 一个库在你自己的 tmux 里往里面敲字、
  改配置、甚至在 GC 时盯着你那个跑了三天的会话 —— 光是"它有能力这么做"就已经不可接受了。
  专属 socket 让这件事在物理上不可能;
- **副作用是干净的心智模型**:`tmux ls` 是你的,`t.sessions()` 是它的,两份清单永不相交。

`socket="default"` 会**直接报错**并说明理由 —— 这不是保守,是这条不变量的唯一守法。

> 那"不是黑盒"的承诺还成立吗?成立,但降格为**逃生舱**:同 UID 下你当然可以
> `tmux -L tmuxd ls` 进去看一眼、排个障。那是调试手段,不是使用方式 ——
> 正常路径下你不该需要它,更不该在那个 socket 里手工 `new-session`
> (真那么干了,见 [02 §7](02-session.md) 的 `external`)。

### 4.1 tmux server 是懒起的

`Tmuxd()` 构造完,**tmux 那边一个进程都没有**。第一次 `t.session(...)` 时
`tmux new-session` 顺手把 server 拉起来。

这带来一个必须写对的细节:

> **实测(2026-08-08,tmux 3.3a)**:server 不存在时,`tmux -L tmuxd ls` 和 `has-session`
> 都以 **exit 1** 加 `error connecting to /tmp/tmux-1000/tmuxd (No such file or directory)`
> 收场。库必须把这一种失败**当成"空列表 / 不存在"**,而不是抛异常 ——
> 否则一个还没建过任何会话的 `Tmuxd` 实例,`t.sessions()` 就会炸。
>
> 这是最容易写错、又最容易被测试漏掉的一处,单独记在这。

## 5. 崩了怎么办

| 谁挂了 | 会话 | 恢复 |
| --- | --- | --- |
| **你的 Python 进程** | **全活着** | 下次 `Tmuxd(...)` 起来,`t.sessions()` 一探全回来(§6 对账) |
| **ttyd** | 全活着 | 网页入口暂时没了;重新构造 `Tmuxd` 会补一个 |
| **tmux server**(tmuxd 那个) | 全没,等价 `kill-server` | 无。state 里的记录标 `exited`,可按 cwd/cmd 重建 |
| **机器重启** | 全没 | 同上 —— tmux 从来不做跨重启持久化,tmuxd 也不假装做 |

没有"unhealthy 状态机",没有 draining。崩了就重启,该丢的丢。

**"进程重启会话无损"是这个产品最该被验收的一条性质**,M1 就要有测试:
起一个会话 → `kill -9` 掉你的 Python 进程 → 重新 `Tmuxd(...)` → 会话还在,
attach 回去现场和挂之前一模一样。

## 6. 状态存哪

真相在 tmux 里,不在文件里。tmuxd 自己存的东西只是**线索**:

```
~/.tmuxd/<socket>/               # state_dir 可覆盖
├── ttyd-<port>.json             # ttyd 的 pid / 端口 / 起它的进程(§3.1 复用判据)
└── sessions/<id>.json           # {id, cwd, cmd, created_at, last_attached}
```

一条 `sessions/<id>.json` 记的是 tmux **答不上来的那部分**:起始工作目录、启动命令、
什么时候建的、上次被 attach 是什么时候。tmux 只知道"有个叫 id5 的会话现在活着"。

读写纪律照搬 shellbase(它这套已经跑通了):

- **原子写**:写临时文件后 `os.replace()`,任何时刻磁盘上都是完整 JSON;
- **跨进程锁**:库可能被多个进程同时 import(Web 后端的多个 worker),
  所以同名文件的写入用文件锁串行化 —— 这比 shellbase 严一档,那边只有单个 uvicorn 进程;
- **无缓存直读**:量级是几十个小 JSON,每次调用直接读盘,省掉一致性问题。

不用 SQLite:状态就是"几十个小对象",文件系统天然提供按 id 寻址、原子替换和 `cat` 即可调试。

`t.sessions()` **每次都现场跑一次 `tmux ls`**,不读缓存 —— 文件是线索,tmux 才是真相。
两边不一致时的处理见 [02 §7](02-session.md)。

## 7. 鉴权:ttyd 的,而且是进程级的

`token` 传给 ttyd 的 `-c`:

```
ttyd -p 12345 -a -W -c tmuxd:<token> /opt/tmuxd/bin/attach.sh
```

浏览器打开 `http://localhost:12345/?arg=id5` 时弹一次 basic auth,输 `tmuxd` / `<token>`。

**必须说清楚它的粒度:token 是进程级的,不是会话级的。**
拿到 token 的人可以把 `?arg=` 换成任何一个 id —— ttyd 只有一个 `-c`,
它不知道也管不了"这个连接该不该看那个会话"。

这和这一层的一贯立场是一致的([02 §4](02-session.md)):
**tmuxd 不分权限,拿到凭据就是拿到这批会话的全部。**
要按人、按会话授权,在**有身份的那一层**做 —— 在 ttyd 前面放一个反代,
按你自己的登录态决定放不放行,再把 `?arg=` 限死。

因此也**没有 `share` 这种一次性分享链接**:签一个"只对这个会话有效"的凭据,
需要一个能按会话判凭据的中间层,而这个架构里刻意没有那一层。
要给别人一个会话,要么给 URL 和 token(等于给这批会话的全部),
要么在上面套一层你自己的代理。

不设 `token` 就不加 `-c` —— **谁能连到这个端口谁就能进终端**。
所以默认 `Tmuxd(...)` 里 ttyd 只绑 `127.0.0.1`;要 `bind="0.0.0.0"` 而没给 token,
**直接报错**,不给"我待会再加"的机会:那是把一台机器的 shell 放到网上。

**公网必须在外层套 TLS**(云 LB / caddy / 反代)。ttyd 自己有 `-S`,但证书管理不是这个库的事。

## 8. 构造参数

库的配置就是构造参数,没有配置文件层(CLI 那层才有,见 [04 §6](04-cli.md))。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `port` | `7681` | ttyd 端口,也是 `s.url` 里的那个 |
| `bind` | `127.0.0.1` | ttyd 绑哪;`0.0.0.0` 时 `token` 必填 |
| `token` | 无 | ttyd basic auth 的密码(用户名固定 `tmuxd`) |
| `socket` | `tmuxd` | 实例名 → tmux socket + 状态目录(§4) |
| `workspace` | 当前 cwd | 新会话的默认 `cwd` |
| `shell` | `$SHELL` | 不给 `cmd` 时跑什么 |
| `history_limit` | `10000` | 写进 tmux.conf,决定人在网页里能往回滚多远 |
| `tmux_bin` | PATH 里的 `tmux` | **tmuxd 对 tmux 的唯一依赖**(§4) |
| `ttyd_bin` | PATH 里的 `ttyd` | 同理 |
| `state_dir` | `~/.tmuxd` | 状态目录 |
| `gc_ttl` | `7d` | `exited` 的 state 记录保留多久([02 §7](02-session.md)) |
| `url_host` | 由 `bind` 推导 | `s.url` 里用的主机名(机器在 NAT/反代后面时给它) |

对应的 `TMUXD_*` 环境变量作为兜底(`TMUXD_PORT`、`TMUXD_TOKEN`…),
优先级:构造参数 > 环境变量 > 默认。CLI 再往前压一层配置文件。

随包 `tmux.conf` 只定三件必须定的事,其余一概不碰用户习惯:

```conf
set -g history-limit 10000        # 人在网页里能往回滚多远
set -g window-size latest         # 多客户端尺寸不一时跟随最后操作的那个,别被最小窗口截断
set -g status off                 # 一个会话就是一个终端,没有 window 可切,状态栏纯属噪音
```

`window-size latest` 来自 shellbase 的协作设计:默认值会让所有人被最小的那个窗口截断。
`status off` 是"不做 window/pane"([02 §1](02-session.md))的直接后果。

**不 unbind 任何按键。** 用户进去按 `C-b %` 分屏是他在用 tmux,拦他没道理;
tmuxd 只是不为分屏提供任何 API,操作一律落在活动 pane 上。

## 9. attach.sh

ttyd 的 `-a` 允许 URL 传参,那就意味着有人能直接敲 `?arg=rogue`。
所以 pty 创建点必须挡一道 —— **会话只能由库创建**:

```sh
#!/bin/sh
# $_TMUXD_SOCK / $_TMUXD_TMUX 由库拉 ttyd 时注入到它的环境里
set -eu
TMUX="${_TMUXD_TMUX:-tmux}"; SOCK="${_TMUXD_SOCK:-tmuxd}"
[ "$#" -ge 1 ] && [ -n "$1" ] || { echo "tmuxd: no session id given" >&2; exit 1; }
if ! "$TMUX" -L "$SOCK" has-session -t "=$1" 2>/dev/null; then
    echo "tmuxd: unknown session '$1'" >&2; exit 1
fi
exec "$TMUX" -L "$SOCK" attach-session -t "=$1"
```

- **`attach-session` 而不是 `new-session -A`** —— 创建的活全在库那边干完了,
  这里只负责接上。`attach.sh` 从不创建会话;
- **`-t "=$1"`** —— `=` 前缀关掉 tmux 的前缀匹配。不加的话 `attach -t work` 会匹配上
  `workbench`,是个真实会踩的坑(§9.1);
- **没有一个条件分支** —— 因为这一层没有只读模式,也没有别的模式。

### 9.1 精确匹配:session 目标和 pane 目标写法不同

这条踩下去很疼,单独记:

> **实测(2026-08-08,tmux 3.3a)**,只存在会话 `workbench` 时:
>
> | 写法 | 结果 |
> | --- | --- |
> | `send-keys -t 'work:'` | **exit 0,字符静默进了 `workbench`** |
> | `send-keys -t '=work'` | `can't find pane: =work` —— 解析不了 |
> | `send-keys -t '=work:'` | `can't find session: work` ✅ |
> | `has-session -t 'work'` | **exit 0**(前缀匹配) |
> | `has-session -t '=work'` | exit 1 ✅ |
>
> 也就是说:**session 类命令用 `=id`,pane 类命令(`send-keys` / `capture-pane` /
> `display -p`)必须写成 `=id:`** —— 少个冒号 tmux 根本解析不出目标,
> 而少个等号就会把按键送到别人的终端里。

这也是为什么所有 tmux 命令行只在一处拼(§10):这种规则散在各处必然写歪。

> **实测(2026-08-08,本机)**:tmux 3.3a、ttyd 1.7.7。
> `ttyd --help` 确认 `-a/--url-arg`、`-W/--writable`(**readonly by default**)、
> `-c/--credential`、`-i` 绑定、`-p 0` 随机端口均在。
> tmux 侧 `new-session -d`、`has-session -t "=id"`、`attach-session`、`send-keys -l`、
> `display -p '#{pane_current_command}'`、`set -g window-size latest` 全部可用。
> 换版本时复核这一段即可。

## 10. 仓库结构

```
tmuxd/
├── pyproject.toml            # 零运行时依赖
├── tmuxd/                    # ← 核心就是这里
│   ├── __init__.py           # Tmuxd、Session、异常,对外只有它们
│   ├── core.py               # Tmuxd:构造、ttyd 生命周期、会话增删查、对账
│   ├── session.py            # Session:send / send_key / kill / url
│   ├── tmux.py               # 唯一一处拼 tmux 命令行的地方
│   ├── ttyd.py               # 拉起 / 复用 / 绑生死(§3)
│   ├── state.py              # 原子写、文件锁
│   ├── errors.py             # 两个基类,HTTP 错误码是它们的投影
│   ├── http.py               # 可选的 HTTP 壳(见 03),按需 import
│   ├── cli.py                # 命令行壳(见 04)
│   └── data/                 # attach.sh + tmux.conf 模板,随包发出去
└── tests/
```

两条纪律:

- **`tmux.py` 是唯一拼 tmux 命令行的地方。** 不是洁癖:tmux 的 `-t` 目标语法(§9.1)、
  format 字符串、转义规则处处是坑,散在各处必然写歪;
- **`import tmuxd` 不该拖进任何东西。** `http.py` 在用到时才 import,
  而且**整个包零运行时依赖** —— HTTP 壳是标准库 `http.server` 写的。
  七个端点、没有长连接,一个 Web 框架在这里帮不上忙,却会成为所有人的安装负担。
