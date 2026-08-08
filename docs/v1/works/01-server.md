# 01 · server

## 1. 一张图

```
┌─ tmuxd(一个 uvicorn 进程) ────────────────────────────────────┐
│                                                               │
│   :7681  HTTP ──┬─ /              会话列表页(很薄)            │
│                 ├─ /s/<name>/     attach 某个会话 → 终端网页    │
│                 ├─ /api/…         会话管理 + 程序化 I/O         │
│                 └─ /tty/          → ttyd  127.0.0.1:<随机>     │
│                                                               │
│   unix socket   $XDG_RUNTIME_DIR/tmuxd/default.sock (0600)    │
│                                                               │
│   ├── ttyd(子进程,只听回环,-W -a attach.sh)                  │
│   └── tmux -L tmuxd ◀── 不是子进程,它自己活着 ─────────────────┼──┐
└───────────────────────────────────────────────────────────────┘  │
                                                                   │
   ┌─ tmux server(独立进程,独立 socket) ──────────────────────────┘
   │   session work   window 0  pane %0 %1
   │   session build  window 0  pane %2
   └─  ← 你 ssh 进来 `tmux -L tmuxd attach -t work` 接的是同一个
```

**只暴露一个端口。** 终端网页、管理接口、I/O 接口在同一个 origin 下,
省掉跨域、省掉两套鉴权、`-p 7681:7681` 一句话就完事。

| 监听 | 地址 | 默认 | 用途 |
| --- | --- | --- | --- |
| HTTP | `127.0.0.1:7681` | **开** | 终端网页 + API,唯一入口 |
| HTTP 对外 | `0.0.0.0:7681` | 关 | `--listen`,**必须配 `TMUXD_TOKEN`** |
| 控制 socket | `$XDG_RUNTIME_DIR/tmuxd/default.sock` | 开 | 本机 CLI 走这个,靠文件权限,不需要 token |
| ttyd | `127.0.0.1:<随机>` | 开 | 只服务本机的反代,端口号对外没有意义 |
| tmux socket | `$TMUX_TMPDIR/tmux-$UID/tmuxd` | 开 | tmux 自己的,ssh 进来照常可用 |

**7681 是 ttyd 的传统端口,这是故意的**:tmuxd 站在 ttyd 原来的位置上,
原本 `ttyd tmux new -A -s work` 的人换过来,浏览器书签都不用改。

**HTTP 不是可选项** —— 网页那半就是本体,不开就没得看。所以问题从来不是"要不要开",
而是**绑在哪**。绑到 `0.0.0.0` 时没设 `TMUXD_TOKEN` 直接拒绝启动,
不给"我待会再加"的机会:那是把一台机器的 shell 放到网上。

## 2. 进程模型:谁是谁的子进程

这是整个设计里最重要的一处不对称,单独拎出来说。

```
tmuxd daemon (uvicorn)                    tmux server (-L tmuxd)
 │                                         │
 └── ttyd(PR_SET_PDEATHSIG)                ├── session work
                                           └── session build
     ↑ 归 tmuxd 管,死了跟着死                 ↑ 不归 tmuxd 管,自己活着
```

- **ttyd 是子进程。** tmuxd 起它、绑它的生死(`PR_SET_PDEATHSIG`,父进程一消失内核直接
  给它 SIGTERM)。ttyd 异常退出则 tmuxd 整体收摊 —— 避免留下"终端全挂但页面还在"的半死实例。
- **tmux server 不是子进程。** tmuxd 用 `tmux -L tmuxd new-session` 拉起它,之后
  两者只有命令行调用关系。tmuxd 被 `kill -9`、被升级、被换成另一个版本,
  会话统统不受影响。

> 对照 webmuxd(`docs/v1/works/05-server-session-runtime.md`):它的
> `process` runtime 里 session 是 server 的子进程,`kill-server` 会跟着死,所以它必须引入
> `runtime` 抽象来区分"会不会跟着死"。**tmuxd 不需要 runtime 这层** —— 会话只有一种拉法
> (`tmux new-session`),而且永远不跟着死。少一个概念。

`PR_SET_PDEATHSIG` 这条不能只写在 `finally` 里:uvicorn 处理完 SIGTERM 会把信号重新抛给
原处理器,进程直接死于信号,`finally` 根本不执行(SIGKILL 更是如此)。
这是 shellbase 踩过的坑,原样搬过来。

## 3. 跑法

同一份启动逻辑,三个壳:

| 命令 | 形态 | 谁用 |
| --- | --- | --- |
| `tmuxd serve` | 前台阻塞,日志走 stdout | 容器 ENTRYPOINT;systemd 的 `ExecStart` |
| `tmuxd start` / `stop` / `status` | 后台守护,PID / 日志 / 运行信息落 `~/.tmuxd/` | pip 装完、手边没有进程管理器 |
| `docker run` | 自带 tmux + ttyd 的镜像 | 不想污染宿主机 |

`start` 只是用 `start_new_session` 把 `serve` 脱离终端拉起来,再轮询 `/api/health` 等它就绪
—— 起不来就把日志尾巴摆到眼前,而不是丢个 pid 让人自己去找。

`serve` 起服务前把运行信息(pid / 监听地址 / ttyd 端口 / tmux socket / 启动时刻)写进
`~/.tmuxd/instance.json`,`status` 读的就是这份。**文件会说谎**(进程可能已经没了、
token 可能被环境变量盖过),所以 `status` 一律回头验证:pid 还在不在、`/api/health` 应不应答、
落盘的 token 拿去 `/api/auth/verify` 认不认。

后台实例的 token 落在 `~/.tmuxd/token`(0600)复用 —— 否则 `stop` / `start` 一次,
浏览器里存的链接全废。

**默认形态是 pip 装在机器上跑,不是容器。** 这跟 webmuxd 正相反,理由很直接:tmuxd 的价值是
"给**这台机器**的 tmux 开个口",装进容器反而把它跟机器上的工具、代码、凭据隔开了。
容器形态留着给"我只想要一个干净的远程 shell"的场景。

```bash
docker run -d --name tmuxd -p 7681:7681 \
  -e TMUXD_TOKEN=changeme \
  -v $PWD:/workspace \
  tmuxd:1.0
```

## 4. 崩了怎么办

| 谁挂了 | 会话 | 恢复 |
| --- | --- | --- |
| **tmuxd** | **全活着** | 重启后 `tmux ls` 一探全回来(§5 对账) |
| **ttyd** | 全活着 | tmuxd 拉起新的,浏览器自动重连 |
| **tmux server** | 全没,等价 `kill-server` | 无。state 里的记录标 `exited`,可按 cwd/cmd 重建 |
| **机器重启** | 全没 | 同上 —— tmux 从来不做跨重启持久化,tmuxd 也不假装做 |

没有"unhealthy 状态机",没有 draining。崩了就重启,该丢的丢 —— 和 tmux 里某个 pane 的进程
死了一样。

**"tmuxd 重启会话无损"是这个产品最该被验收的一条性质**,M1 就要有测试:
`tmuxd new -s a && kill -9 <pid> && tmuxd start && tmuxd ls` 必须还看得见 `a`,
而且 `capture` 出来的内容和挂之前一致。

## 5. 状态存哪

真相在 tmux 里,不在文件里。tmuxd 自己存的东西只是**线索**:

```
~/.tmuxd/                        # TMUXD_STATE_DIR 可覆盖(容器里指向挂载卷)
├── instance.json                # pid / 监听 / ttyd 端口 / tmux socket / 启动时刻
├── token                        # 0600
└── sessions/<name>.json         # {name, uri, cwd, cmd, kind, created_at, last_attached}
```

一条 `sessions/<name>.json` 记的是 tmux **答不上来的那部分**:这个会话当初是用哪条 URI 开的、
起始工作目录、启动命令、上次被 attach 是什么时候。tmux 只知道"有个叫 work 的会话现在活着"。

读写纪律照搬 shellbase(它这套已经跑通了):

- **原子写**:写临时文件后 `os.replace()`,任何时刻磁盘上都是完整 JSON;
- **单写者**:uvicorn 单进程是唯一写者(`attach.sh` 只读),进程内用 asyncio 锁串行化同名文件;
- **无缓存直读**:量级是几十个小 JSON,每次请求直接读盘,省掉一致性问题。

不用 SQLite:状态就是"几十个小对象",文件系统天然提供按名字寻址、原子替换和 `cat` 即可调试。

`GET /api/sessions` **每次都现场跑一次 `tmux ls`**,不读缓存 —— 文件是线索,tmux 才是真相。
两边不一致时的处理见 [02 §5](02-session.md)。

## 6. 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `TMUXD_TOKEN` | 随机生成并打印 | 访问令牌;绑 `0.0.0.0` 时必须显式设置 |
| `TMUXD_PORT` | `7681` | 对外监听端口 |
| `TMUXD_LISTEN` | `127.0.0.1` | 监听地址 |
| `TMUXD_TMUX_SOCKET` | `tmuxd` | `tmux -L` 的名字;设 `default` 即接管你现有的 tmux |
| `TMUXD_TMUX_CONF` | 随包 `tmux.conf` | 传给 `tmux -f`,与你自己的 `~/.tmux.conf` 隔离 |
| `TMUXD_STATE_DIR` | `~/.tmuxd` | 状态目录 |
| `TMUXD_WORKSPACE` | daemon 的 cwd | 新会话的默认工作目录 |
| `TMUXD_SHELL` | `$SHELL` | 不带命令的会话跑什么 |
| `TMUXD_COLS` / `TMUXD_ROWS` | `120` / `40` | 新会话的尺寸;**没人 attach 时它决定折行宽度**(见 [03 §3](03-io.md)) |
| `TMUXD_HISTORY_LIMIT` | `10000` | 写进 tmux.conf 的 `history-limit`,决定 `capture -S -` 能回滚多远 |
| `TMUXD_RECORD` | 关 | 开启输出录制(见 [03 §5](03-io.md)) |
| `TMUXD_RECORD_LIMIT` | `50MB` | 单个会话录制文件的环形截断上限 |
| `TMUXD_GC_TTL` | `7d` | `exited` 的 state 记录保留多久(见 [02 §7](02-session.md)) |
| `TMUXD_ALIASES` | 空 | JSON,URI scheme 别名(见 [02 §3](02-session.md)) |

随包 `tmux.conf` 只定三件必须定的事,其余一概不碰用户习惯:

```conf
set -g history-limit 10000        # capture -S - 能回滚多远
set -g window-size latest         # 多客户端尺寸不一时跟随最后操作的那个,别被最小窗口截断
set -g status off                 # 网页里一个会话一个页面,状态栏是噪音;用户可覆盖
```

`window-size latest` 这条来自 shellbase 的协作设计:默认值会让所有人被最小的那个窗口截断。

## 7. 鉴权

| 走哪 | 怎么鉴权 |
| --- | --- |
| unix socket | 文件权限(0600,只有你自己)。**不需要 token** |
| HTTP `/api/*` | `Authorization: Bearer $TMUXD_TOKEN`,或登录后的 HttpOnly Cookie |
| HTTP `/s/<name>/` 与 `/tty/` | 同上,或该会话的一次性分享 token(见 [02 §4](02-session.md)) |

鉴权是包在 ASGI 应用**最外层**的一道门(`AuthGate`),HTTP 与 WebSocket 一视同仁:
没有有效令牌,任何请求都到不了下游路由,WS 在 accept 之前就被关掉,握手不会建立。
放行名单只有三项:`/login`、`POST /api/auth/login`、`GET /api/health`。
**新增路由默认受保护 —— 这个默认方向是有意的。**

ttyd 自身不感知认证(它只听回环,只被 tmuxd 反代),鉴权收敛在一处。
Cookie 名字带端口(`tmuxd_token_7681`),因为 cookie 作用域不认端口 ——
同机多实例共用一个名字会互相顶下线。

登录限流:令牌桶(容量 6、每 6 秒回补 1),超限 `429`。token 比较用常量时间比较。

**公网部署必须在外层套 TLS**(云 LB / caddy / 反代)。tmuxd 容器内只出 HTTP,
v1 不做证书自动化。

## 8. 反代 ttyd 的实现要点

三条,都是 shellbase 已经踩实的:

- 读超时放到 24h,否则空闲终端会被自己的反代掐断;
- WebSocket 反代必须**先连上游、拿到子协议协商结果,再 accept 客户端**,否则子协议对不上
  (ttyd 用的是 `tty` 子协议);
- ttyd 以 `-i 127.0.0.1 -p 0 -W -a /opt/tmuxd/bin/attach.sh` 启动:`-p 0` 让内核挑空闲端口
  (写死会跟用户自己跑的 ttyd 撞车),`-a` 允许 URL 传参,`-W` 打开可写
  —— **只读是在 tmux 层用 `attach -r` 实现的,不在 ttyd 层**(理由见 [02 §4](02-session.md))。

> **实测(2026-08-08,本机)**:tmux 3.3a、ttyd 1.7.7。
> `ttyd --help` 确认 `-a/--url-arg`、`-W/--writable`(**readonly by default**)、
> `-b/--base-path`、`-p 0` 随机端口均在。
> tmux 侧 `capture-pane -p -J -e -S -`、`send-keys -l`、`pipe-pane -o`、
> `display -p '#{pane_current_command}'`、`set -g window-size latest` 全部可用。
> 换版本时复核这一段即可。

## 9. 仓库结构

```
tmuxd/
├── pyproject.toml            # pip 包:后端 + 前端产物 + deploy 资源
├── Dockerfile                # 可选形态
├── deploy/tmux.conf
├── bin/attach.sh             # ttyd → tmux(含 state 校验,见 02 §2)
├── server/tmuxd/
│   ├── main.py               # FastAPI 实例、启动钩子(拉 ttyd、建状态目录、打 token)
│   ├── gateway.py            # AuthGate + 静态托管 + ttyd 反代
│   ├── cli.py                # serve / start / stop / status + 全部子命令(见 05)
│   ├── sessions.py           # 会话 CRUD、attach 302、对账(见 02)
│   ├── io.py                 # keys / capture / run / stream(见 03)
│   ├── tmux.py               # 唯一一处拼 tmux 命令行的地方
│   ├── uri.py                # URI 规范化 → 会话名(见 02 §2)
│   ├── state.py              # 原子写、对账、回收
│   └── auth.py               # login / verify / logout + 分享 token 签发
├── sdk/tmuxd_client/         # Python SDK(见 04 §4)
└── web/                      # 会话列表页 + attach 壳页(很薄)
```

`tmux.py` 是**唯一**拼 tmux 命令行的地方。这一条不是洁癖:tmux 的
`-t` 目标语法、format 字符串、转义规则处处是坑,散在各处必然写歪。
所有 tmux 交互从这一个模块出去,以后要换 control mode(`tmux -CC`)也只动它。
