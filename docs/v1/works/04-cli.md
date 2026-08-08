# 04 · CLI

照着 tmux 设计。用过 tmux 的人应该不用查文档 —— 而且这次要记的东西格外少。

## 1. 先说清楚:它不取代 tmux

**你人在那台机器的终端里,想开个自己的会话干活,就用 `tmux`。**
那是你的 tmux,tmuxd 看不见也不碰([01 §2.1](01-server.md))。

`tmuxd` 的 CLI 管的是**另一套**东西 —— tmuxd 自己那个会话池,给**从外面**用的:

| 场景 | 用什么 |
| --- | --- |
| 我 ssh 在那台机器上,想开个会话自己干活 | `tmux`(你自己的 socket,与 tmuxd 无关) |
| 我要在远端机器上跑一条命令、拿输出和退出码 | **`ssh`** —— 不是 tmuxd([03 §1](03-api-and-sdk.md)) |
| 我要在一个**有人在看**的会话里投喂一条指令 | `tmuxd send` |
| 我要把一个会话交给同事接着弄 | `tmuxd share` |
| 我要看 tmuxd 手里现在有哪些会话、谁挂着 | `tmuxd ls` |
| 我在写调用方,想先手敲试试 API | `tmuxd`(每条命令就是一次 API 调用,§8) |

第二行要专门看一眼:**`tmuxd` 里没有"跑命令拿结果"这种事**。
要那个就用 ssh,它本来就更直([03 §1](03-api-and-sdk.md))。

设计纪律:**和 tmux 同名的子命令,语义必须一致**;不一致的地方要么改名,要么在这份文档里
单独列出来(§6)。

## 2. daemon

```bash
tmuxd serve   [--listen 0.0.0.0:7681]
tmuxd start   # 后台起,打印 URL 和 token
tmuxd stop    # 停门面,会话全活着
tmuxd status  # 回头核实:pid 在不在、health 应不应答、token 认不认
tmuxd info    # = GET /api/server
```

```console
$ tmuxd start
tmuxd 1.0  →  http://127.0.0.1:7681
token: 8f2c1e9a…(已存 ~/.tmuxd/token)
tmux:  /usr/bin/tmux 3.3a   socket=tmuxd(专属)   sessions=0
```

**`tmux:` 那一行只报三件事:二进制在哪、版本多少、用的哪个专属 socket。**
tmuxd 对 tmux 的全部依赖就这么多([01 §2.1](01-server.md))。

```console
$ tmuxd stop
门面已停。3 个会话仍在运行(tmuxd start 回来即可)。
```

**`stop` 的那行提示不是客套话**,它是这个产品最需要被理解的性质:门面和屋子是分开的。

```bash
tmuxd kill-server --tmux      # 真的要杀 tmuxd 的 tmux server(它的会话全没),需要确认
```

`--tmux` 杀的是 **tmuxd 专属的那个 tmux server**,碰不到你自己的 —— 两者在不同 socket 上。

## 3. 会话

```bash
tmuxd new  [-s ID] [-c DIR] [-e K=V]... [-- CMD...]
tmuxd ls   [-F FORMAT]
tmuxd attach -t ID [-p]
tmuxd share  -t ID [--ttl 1h]
tmuxd kill   -t ID
tmuxd rename -t ID NEW
tmuxd has    -t ID
```

```console
$ tmuxd new -s work -c ~/proj
work  →  http://127.0.0.1:7681/s/work/

$ tmuxd new -s ci-42 -c ~/proj -- npm run dev
ci-42  →  http://127.0.0.1:7681/s/ci-42/

$ tmuxd ls
work                 alive   2 clients  bash    ~/proj
ci-42                alive   0 clients  node    ~/proj
stale                exited  —          —       —          7 天后自动清

$ tmuxd attach -t work            # 用默认浏览器打开
$ tmuxd attach -t work -p         # 只打印 URL(无 GUI 环境用)
http://127.0.0.1:7681/s/work/

$ tmuxd share -t work
http://box:7681/s/work/?t=…   (1 小时后过期)
⚠ 拿到这个链接的人能在你的机器上执行任意命令
```

- **detach 不需要命令** —— 关掉网页就是 detach,会话照跑;
- `has` 只返回退出码,给脚本用:`tmuxd has -t work || tmuxd new -s work`;
- `attach` 是**你自己看**,`share` 是**给别人**——区别不在权限(两者都是完整读写),
  而在凭据:`share` 签的 token 只对这一个会话有效、会过期([02 §4](02-session.md));
- `-s` 不给则 tmuxd 生成一个 id(像 tmux 的 `0` / `1` / `2`);**要重入就自己给**
  ([02 §2.1](02-session.md));
- **`ls` 里没有 window / pane 计数** —— 一个会话就是一个终端([02 §1](02-session.md));
- `-F` 用 tmux 同款占位符:`#{session_id}` `#{session_attached}`
  `#{pane_current_command}` `#{session_status}`。

## 4. 往里敲

```bash
tmuxd send -t ID "npm test" [--enter]       # 字面量
tmuxd keys -t ID C-c q Enter                # tmux 键名
```

**就这两条。** 没有 `capture`、没有 `run`、没有 `wait`、没有 `stream`、没有 `watch` ——
tmuxd 不读终端内容,理由见 [03 §1](03-api-and-sdk.md)。

```console
$ tmuxd send -t work "npm test" --enter
✓ sent

$ tmuxd keys -t work C-c
✓ sent
```

输出只有一行 `✓ sent`,而且它的意思**仅仅是"字符已经交给 tmux 了"** ——
不是"命令跑完了",更不是"跑成功了"。想知道结果,让人点开
`tmuxd attach -t work -p` 那个链接看一眼,或者换 ssh。

`send` 与 `keys` 分成两条命令,和 API 的两个字段一一对应([03 §5](03-api-and-sdk.md))——
**`tmuxd send -t x "Enter the code"` 打进去的就是这七个词,不会变成一个回车。**
这是 tmux 的 `send-keys` 最容易咬人的地方,CLI 层面直接消掉。

## 5. 实例与远端

```bash
tmuxd -H https://box.internal:7681 ls
export TMUXD_HOST=https://box.internal:7681
export TMUXD_TOKEN=…
```

`-H` 指向的是**一个远端 tmuxd**,所以 `new` / `ls` / `send` / `kill`
全都照常可用 —— 由那边执行。没有 `-H` 时优先走本机 unix socket(不需要 token),
socket 不在则退到 `127.0.0.1:7681` + token。

`-L` / `-S` 换的是 **tmuxd 实例**,和 tmux 的写法一致:

```bash
tmuxd -L ci new -s build     # 另一套 tmuxd:自己的控制 socket、自己的端口、自己的 tmux 池
tmuxd -S /tmp/x.sock ls
```

**实例名同时决定它的 tmux socket**(`-L ci` → `tmux -L tmuxd-ci`),
所以两套 tmuxd 的会话池互不可见,也都不碰你自己的 tmux —— 一个概念,不是两个
([01 §2.1](01-server.md))。

## 6. 配置

`~/.tmuxd.conf`,tmux 的 `set -g` 写法:

```conf
set -g port          7681
set -g history-limit 10000
set -g attach-cmd    "firefox %u"     # %u = attach URL
```

优先级:命令行参数 > 环境变量 > 配置文件 > 内置默认。
配置项名字和 `TMUXD_*` 环境变量一一对应(`history-limit` ↔ `TMUXD_HISTORY_LIMIT`)。

**这里没有 tmux socket 这一项** —— 它由实例名推导,不给单独配([01 §2.1](01-server.md))。
唯一和 tmux 有关的配置是 `TMUXD_TMUX_BIN`(二进制在哪),而且平时不用设。

## 7. 和 tmux 故意不一样的地方

除了 §1 的定位,还有几处刻意偏离,都记在这:

| | tmux | tmuxd | 为什么 |
| --- | --- | --- | --- |
| 无 `-t` 又有多个会话 | 挑最近的 | **报错** | 往错的终端敲命令代价太大 |
| `kill-server` | 杀 tmux server | **停门面,会话全活** | 门面和屋子分开;要杀真的得 `--tmux` |
| `send-keys` | 一个命令,`-l` 区分 | 拆成 `send` / `keys` | 消掉"Enter 变回车"那个坑 |
| `capture-pane` | 有 | **无** | 不读终端内容([03 §1](03-api-and-sdk.md)) |
| `attach` | 占住你的终端 | **打开浏览器 / 打印 URL** | 它本来就是给外面用的 |
| `attach -r` 只读 | 有 | **无** | 这一层不分权限,锁在上层([02 §4](02-session.md)) |
| window / pane 命令 | 一大堆 | **一条都没有** | 一个会话就是一个终端([02 §1](02-session.md)) |
| 前缀键 `C-b` | 有 | **无** | CLI 不劫持键盘;要前缀键就 attach 进去用 tmux 本体 |
| 会话名前缀匹配 | 支持 | **精确匹配**(`-t "=name"`) | `work` 匹配上 `workbench` 是真实事故 |
| `-L` / `-S` | 换 tmux server | 换 **tmuxd 实例**(连带换它的 tmux socket) | 实例是一个概念,不是两个 |

## 8. 退出码

给脚本用,不要靠解析输出:

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 一般失败 |
| 2 | 用法错误(参数不对) |
| 3 | 会话不存在(`has` 用这个) |
| 4 | 状态不对(`session_exists` / `bad_id`) |
| 5 | 连不上 tmuxd(`unreachable` / `unauthorized`) |
| 6 | tmuxd 的 tmux server 没了(`tmux_gone`) |

4 可以改参数重试,5 检查配置,**6 该告警**。和 API 的错误二分一致
([03 §6](03-api-and-sdk.md))。

## 9. CLI ↔ API 对照

CLI 不做任何 API 没有的事,每条命令就是一次调用:

| CLI | API |
| --- | --- |
| `new` | `POST /api/sessions` |
| `ls` | `GET /api/sessions` |
| `attach` | `GET /api/attach?id=`(拿 302 的地址,不跟随) |
| `share` | `POST /api/sessions/{id}/share` |
| `kill` | `DELETE /api/sessions/{id}` |
| `rename` | `POST /api/sessions/{id}/rename` |
| `send` / `keys` | `POST /api/sessions/{id}/keys` |
| `has` | `GET /api/sessions/{id}` |
| `info` | `GET /api/server` |
| `stop` | `POST /api/server/shutdown` |
| `status` | `GET /api/health` + 本地 `instance.json` 核实 |

**十一条命令,十一个端点,一一对应。** 唯一多出来的东西是输出格式化和退出码映射,
两样都在客户端做,不进服务端 —— 连 target 解析都没有了,id 就是原样传过去的字符串。
`--json` 在 `ls` / `info` / `status` 上可用,输出就是 API 的原始响应。
