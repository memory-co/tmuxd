# tmuxd CLI

命令行是**套在库外面的壳**。本地跑的时候它直接 `import tmuxd`,和你自己写 Python 调的是
同一份代码;只有 `-H` 指向远端时才走 HTTP。

设计依据见 [`../works/04-cli.md`](../works/04-cli.md),这里是命令本身。

```bash
pip install tmuxd          # 需要机器上有 tmux(≥3.0);要网页入口还需要 ttyd
```

## 五分钟

```console
$ tmuxd start                        # 起服务:ttyd 守着,打印 URL 和 token
$ tmuxd new -s work -c ~/proj
work  →  http://127.0.0.1:7681/?arg=work

$ tmuxd send -t work "npm test" --enter
✓ sent

$ tmuxd url -t work -o               # 用浏览器打开,看它跑
$ tmuxd ls
work                 alive   1 client   node     /home/me/proj
```

**你自己的 `tmux ls` 不会因此多出任何东西** —— tmuxd 的会话池在专属 socket 上。

## 命令总表

| 命令 | 干什么 | 详见 |
| --- | --- | --- |
| `serve` | 前台跑,守着 ttyd | [daemon.md](daemon.md) |
| `start` / `stop` | 后台起 / 停。**停的是门面,会话全活** | [daemon.md](daemon.md) |
| `status` | 回头核实 pid 和端口 | [daemon.md](daemon.md) |
| `info` | 版本、ttyd、tmux、会话统计 | [daemon.md](daemon.md) |
| `kill-server` | 销毁这个池里的全部会话 | [daemon.md](daemon.md) |
| `new` | 建会话,或接上同 id 的那个 | [sessions.md](sessions.md) |
| `ls` | 列会话 | [sessions.md](sessions.md) |
| `url` | 打印入口地址 | [sessions.md](sessions.md) |
| `kill` | 销毁一个会话 | [sessions.md](sessions.md) |
| `rename` | 换 id | [sessions.md](sessions.md) |
| `has` | 存在就退出 0,给脚本用 | [sessions.md](sessions.md) |
| `send` | 往里打**字面量文本** | [keys.md](keys.md) |
| `keys` | 往里**按键**(`C-c`、`Enter`…) | [keys.md](keys.md) |

**十三条命令,每条就是一次库调用。** 没有任何命令能做库做不到的事。

## 全局选项

放在子命令**前面**(argparse 不收后置的全局选项):

```bash
tmuxd -L ci --port 9000 new -s build     # ✅
tmuxd new -s build -L ci                 # ❌ 不认
```

| 选项 | 说明 |
| --- | --- |
| `-L, --socket NAME` | 实例名。**同时决定它的 tmux socket**(`-L ci` → `tmux -L tmuxd-ci`) |
| `-H, --host URL` | 驱动一个开了 HTTP 的远端 tmuxd;唯一走 HTTP 的模式 |
| `--port N` | ttyd 端口(入口地址用它) |
| `--bind ADDR` | ttyd 绑哪。非回环地址**必须**配 `--token`,否则拒绝启动 |
| `--token T` | ttyd 的 basic auth 密码(用户名固定 `tmuxd`);`-H` 模式下是 Bearer token |
| `--state-dir DIR` | 状态目录,默认 `~/.tmuxd` |
| `--json` | 原样输出 API 的 JSON(`ls` / `info` / `status`) |
| `--version` | |

## 退出码

给脚本用,**不要解析输出**:

| 码 | 含义 | 怎么办 |
| --- | --- | --- |
| 0 | 成功 | |
| 1 | 一般失败 | |
| 2 | 用法错误(参数不对) | 改命令 |
| 3 | 会话不存在(`has` 用这个) | |
| 4 | 状态不对(`session_exists` / `bad_id` / `port_in_use`) | 改参数重试 |
| 5 | 连不上远端 tmuxd(`unreachable` / `unauthorized`) | 检查 `-H` 和 token |
| 6 | tmux server 没了(`tmux_gone`) | **告警,别重试** |

```bash
tmuxd has -t work || tmuxd new -s work        # 3 就是"没有",不是错误
```

## 配置文件

`~/.tmuxd.conf`,tmux 的 `set -g` 写法。`TMUXD_CONFIG` 可以指到别处。

```conf
# 注释以 # 开头
set -g port          12345
set -g bind          127.0.0.1
set -g token         changeme
set -g socket        default
set -g state-dir     ~/.tmuxd
set -g history-limit 10000
set -g tmux-bin      /usr/local/bin/tmux
set -g open-cmd      "firefox %u"     # %u = 会话 URL,给 tmuxd url -o 用
```

**这八项就是全部会被读取的键** —— 写别的不会报错,但也不会生效。
配置项名字和 `Tmuxd(...)` 的构造参数一一对应([SDK 文档](../sdk/tmuxd.md#构造参数)),
**配置文件只是构造参数的另一种写法**,不是第二套东西。

**这里没有 tmux socket 这一项**:它由 `-L` 的实例名推导,不给单独配。

## 环境变量

优先级:**命令行参数 > 环境变量 > 配置文件 > 内置默认**。

| 变量 | 对应 |
| --- | --- |
| `TMUXD_PORT` / `TMUXD_BIND` / `TMUXD_TOKEN` | `--port` / `--bind` / `--token` |
| `TMUXD_SOCKET` | `-L` |
| `TMUXD_HOST` | `-H` |
| `TMUXD_STATE_DIR` | `--state-dir` |
| `TMUXD_WORKSPACE` | 新会话的默认 `cwd` |
| `TMUXD_SHELL` | 不给命令时跑什么 |
| `TMUXD_HISTORY_LIMIT` | 人在网页里能往回滚多远 |
| `TMUXD_GC_TTL` | `exited` 记录保留多少秒(默认 604800) |
| `TMUXD_URL_HOST` | 入口 URL 里用的主机名(机器在反代后面时给它) |
| `TMUXD_TMUX_BIN` / `TMUXD_TTYD_BIN` | 两个二进制的路径 |
| `TMUXD_CONFIG` | 配置文件路径 |

## 它不取代 tmux

**你人在那台机器上想开个自己的会话干活,就用 `tmux`。** 那是你的 tmux,tmuxd 看不见也不碰。

| 想干的事 | 用什么 |
| --- | --- |
| ssh 上去开个会话自己干活 | `tmux` |
| 在远端跑一条命令、拿输出和退出码 | **`ssh`** —— tmuxd 里没有这种事 |
| 往一个**有人在看**的会话里投喂指令 | `tmuxd send` |
| 开个会话,把入口发给别人 | `tmuxd new` + `tmuxd url` |

第二行值得多看一眼:**tmuxd 不读终端内容**,所以没有 `capture` / `run` / `wait` / `stream`。
要输出和退出码就用 ssh([为什么](../works/03-http.md))。
