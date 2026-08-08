# 05 · CLI

照着 tmux 设计。用过 tmux 的人应该不用查文档。

## 1. 先说清楚:它不取代 tmux

**你人在那台机器的终端里,就用 `tmux`。** 别用 tmuxd 的 CLI ——
它要过一层 HTTP,只会更慢更绕。

`tmuxd` 的 CLI 是给**从外面**用的:

| 场景 | 用什么 |
| --- | --- |
| 我 ssh 在那台机器上,想开个会话干活 | `tmux`(或 `tmux -L tmuxd`,接的是同一批会话) |
| CI 脚本要在远端机器上跑一串命令并拿退出码 | `tmuxd -H … run` |
| 我要把一个会话分享给同事围观 | `tmuxd share` |
| 我要看这台机器上现在有哪些会话、谁挂着 | `tmuxd ls` |
| 我在写调用方,想先手敲试试 API | `tmuxd`(每条命令就是一次 API 调用,§8) |

设计纪律:**和 tmux 同名的子命令,语义必须一致**;不一致的地方要么改名,要么在这份文档里
单独列出来(§7)。

## 2. daemon

```bash
tmuxd serve   [--listen 0.0.0.0:7681] [--tmux-socket NAME] [--record]
tmuxd start   # 后台起,打印 URL 和 token
tmuxd stop    # 停门面,会话全活着
tmuxd status  # 回头核实:pid 在不在、health 应不应答、token 认不认
tmuxd info    # = GET /api/server
```

```console
$ tmuxd start
tmuxd 1.0  →  http://127.0.0.1:7681
token: 8f2c1e9a…(已存 ~/.tmuxd/token)
tmux:  socket=tmuxd  version=3.3a  sessions=0

$ tmuxd stop
门面已停。3 个会话仍在运行(tmux -L tmuxd ls 可见)。
```

**`stop` 的那行提示不是客套话**,它是这个产品最需要被理解的性质
([01 §2](01-server.md)):门面和屋子是分开的。

```bash
tmuxd kill-server --tmux      # 真的要杀 tmux server(会话全没),需要确认
```

## 3. 会话

```bash
tmuxd new  [-s NAME | URI] [-c DIR] [-x COLS] [-y ROWS] [-e K=V]... [-- CMD...]
tmuxd ls   [--uri] [-F FORMAT]
tmuxd attach -t NAME [-p] [--read-only]
tmuxd share  -t NAME [--writable] [--ttl 1h]
tmuxd kill   -t NAME
tmuxd rename -t NAME NEW
tmuxd has    -t NAME
```

```console
$ tmuxd new -s work -c ~/proj
work  →  http://127.0.0.1:7681/s/work/

$ tmuxd new claude:///home/me/proj?window=main\&block=1
claude-proj-7b21e0  →  http://127.0.0.1:7681/s/claude-proj-7b21e0/

$ tmuxd ls
work                 alive   1 window   2 clients  bash    ~/proj
claude-proj-7b21e0   alive   1 window   0 clients  claude  ~/proj
hotfix               alive   3 windows  1 client   vim     ~          external
stale                exited  —          —          —       —          7 天后自动清

$ tmuxd attach -t work            # 用默认浏览器打开
$ tmuxd attach -t work -p         # 只打印 URL(无 GUI 环境用)
http://127.0.0.1:7681/s/work/

$ tmuxd share -t work
http://box:7681/s/work/?t=…   (只读,1 小时后过期)

$ tmuxd share -t work --writable
http://box:7681/s/work/?t=…   (可操作,1 小时后过期)
⚠ 这个链接能在你的机器上执行任意命令
```

- **detach 不需要命令** —— 关掉网页就是 detach,会话照跑;
- `has` 只返回退出码,给脚本用:`tmuxd has -t work || tmuxd new -s work`;
- `attach` 是**你自己看**(完整权限),`share` 是**给别人**(默认只读),
  区别与理由见 [02 §4](02-session.md);
- `ls` 里 `external` 那一列不是异常 —— 是你 ssh 进去手工开的会话,一等公民
  ([02 §7](02-session.md));
- `-F` 用 tmux 同款占位符:`#{session_name}` `#{session_windows}` `#{session_attached}`
  `#{pane_current_command}` `#{session_uri}` `#{session_status}`。

## 4. 操作

```bash
tmuxd send    -t NAME "npm test" [--enter]     # 字面量
tmuxd keys    -t NAME C-c q Enter              # tmux 键名
tmuxd capture -t NAME [-S -] [--ansi] [--raw]
tmuxd wait    -t NAME --for 'Done in' [--timeout 30]
tmuxd run     -t NAME "git rev-parse HEAD"     # 拿退出码
tmuxd resize  -t NAME -x 200 -y 50
tmuxd stream  -t NAME                          # 像 tail -f
tmuxd watch                                    # 事件流
```

```console
$ tmuxd capture -t work | tail -3        # 对应 tmux capture-pane -p
$ npm test
PASS  src/foo.test.ts
Done in 4.2s

$ tmuxd run -t work "git status --porcelain"
 M src/foo.ts
exit 0  (84ms)

$ tmuxd run -t work "make deploy"
✗ not_a_shell: pane 里跑的是 vim
  加 --force 强行发送,或改用 tmuxd send

$ tmuxd stream -t work | tee session.log   # Ctrl-C 退出
```

`send` 与 `keys` 分成两条命令,和 API 的两个字段一一对应([03 §2](03-io.md))——
**`tmuxd send -t x "Enter the code"` 打进去的就是这七个词,不会变成一个回车。**
这是 tmux 的 `send-keys` 最容易咬人的地方,CLI 层面直接消掉。

`run` 的退出码**同时**作为 CLI 的退出码传出来吗?**不。**
`tmuxd run` 的退出码表示"这次调用成功没有",命令自己的退出码在输出里、
`--exit-code` 才让它透传:

```bash
tmuxd run -t work "make test" --exit-code || echo "测试挂了"
```

不默认透传,是因为分不清"命令失败"和"调用失败"会让脚本写错 ——
这两件事在远程执行里必须分开。

## 5. 远端

```bash
tmuxd -H https://box.internal:7681 ls
export TMUXD_HOST=https://box.internal:7681
export TMUXD_TOKEN=…
```

`-H` 指向的是**一个远端 tmuxd**,所以 `new` / `ls` / `kill` / `run` 全都照常可用 ——
由那边执行。没有 `-H` 时优先走本机 unix socket(不需要 token),socket 不在则退到
`127.0.0.1:7681` + token。

socket 语义和 tmux 一致:

```bash
tmuxd -L ci ls              # 换一套互不可见的 tmuxd(自己的 socket + 自己的 tmux socket)
tmuxd -S /tmp/x.sock ls
```

## 6. 配置

`~/.tmuxd.conf`,tmux 的 `set -g` 写法:

```conf
set -g port          7681
set -g tmux-socket   tmuxd
set -g cols          120
set -g rows          40
set -g history-limit 10000
set -g record        off
set -g attach-cmd    "firefox %u"     # %u = attach URL
```

优先级:命令行参数 > 环境变量 > 配置文件 > 内置默认。
配置项名字和 `TMUXD_*` 环境变量一一对应(`history-limit` ↔ `TMUXD_HISTORY_LIMIT`)。

## 7. 和 tmux 故意不一样的地方

除了 §1 的定位,还有几处刻意偏离,都记在这:

| | tmux | tmuxd | 为什么 |
| --- | --- | --- | --- |
| 无 `-t` 又有多个会话 | 挑最近的 | **报错** | 往错的 pane 敲命令代价太大 |
| `kill-server` | 杀 tmux server | **停门面,会话全活** | 门面和屋子分开;要杀真的得 `--tmux` |
| `send-keys` | 一个命令,`-l` 区分 | 拆成 `send` / `keys` | 消掉"Enter 变回车"那个坑 |
| `attach` | 占住你的终端 | **打开浏览器 / 打印 URL** | 它本来就是给外面用的 |
| 前缀键 `C-b` | 有 | **无** | CLI 不劫持键盘;要前缀键就用 tmux 本体 |
| 会话名前缀匹配 | 支持 | **精确匹配**(`-t "=name"`) | `work` 匹配上 `workbench` 是真实事故 |

## 8. 退出码

给脚本用,不要靠解析输出:

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 一般失败 |
| 2 | 用法错误(参数不对) |
| 3 | 会话 / window / pane 不存在(`has` 用这个) |
| 4 | 状态不对(`not_a_shell` / `name_conflict` / `read_only`) |
| 5 | 超时(`timeout`) |
| 6 | 连不上 tmuxd(`unreachable` / `unauthorized`) |
| 7 | 底下的 tmux server 没了(`tmux_gone`) |

4/5 可重试,6 检查配置,**7 该告警**。和 API 的错误二分一致([03 §7](03-io.md))。

## 9. CLI ↔ API 对照

CLI 不做任何 API 没有的事,每条命令就是一次调用:

| CLI | API |
| --- | --- |
| `new` | `POST /api/sessions` |
| `ls` | `GET /api/sessions` |
| `attach` | `GET /api/attach?target=`(拿 302 的地址,不跟随) |
| `share` | `POST /api/sessions/{t}/share` |
| `kill` | `DELETE /api/sessions/{t}` |
| `rename` | `POST /api/sessions/{t}/rename` |
| `send` / `keys` | `POST /api/sessions/{t}/keys` |
| `capture` / `wait` | `GET /api/sessions/{t}/capture` |
| `run` | `POST /api/sessions/{t}/run` |
| `resize` | `POST /api/sessions/{t}/resize` |
| `stream` | `WS /api/sessions/{t}/stream` |
| `watch` | `WS /api/events` |
| `info` | `GET /api/server` |
| `stop` | `POST /api/server/shutdown` |
| `status` | `GET /api/health` + 本地 `instance.json` 核实 |

**唯一多出来的东西**是 target 解析、输出格式化和退出码映射。三样都在客户端做,不进服务端。
`--json` 在所有读命令上可用,输出就是 API 的原始响应 —— 方便和 API 混着用。
