# 04 · CLI

CLI 是**套在库外面的第三个壳**([01 §1](01-library.md))。它不走 HTTP ——
本地跑的时候直接 `import tmuxd`,和你自己写 Python 调的是同一份代码。
只有加了 `-H` 指向远端时才走 HTTP([03 §8](03-http.md))。

命令照着 tmux 设计,用过 tmux 的人不用查文档 —— 而且这次要记的东西格外少。

## 1. 先说清楚:它不取代 tmux

**你人在那台机器的终端里,想开个自己的会话干活,就用 `tmux`。**
那是你的 tmux,tmuxd 看不见也不碰([01 §4](01-library.md))。

`tmuxd` 的 CLI 管的是**另一套**东西 —— tmuxd 自己那个会话池:

| 场景 | 用什么 |
| --- | --- |
| 我 ssh 在那台机器上,想开个会话自己干活 | `tmux`(你自己的 socket,与 tmuxd 无关) |
| 我要在远端机器上跑一条命令、拿输出和退出码 | **`ssh`** —— 不是 tmuxd([03 §2](03-http.md)) |
| 我要在一个**有人在看**的会话里投喂一条指令 | `tmuxd send` |
| 我要开个会话,把入口发给别人 | `tmuxd new` + `tmuxd url` |
| 我要看 tmuxd 手里现在有哪些会话、谁挂着 | `tmuxd ls` |
| 我在写 Python 调用方,想先手敲试试 | `tmuxd`(每条命令就是一次库调用,§9) |

第二行要专门看一眼:**`tmuxd` 里没有"跑命令拿结果"这种事**。
要那个就用 ssh,它本来就更直([03 §2](03-http.md))。

设计纪律:**和 tmux 同名的子命令,语义必须一致**;不一致的地方要么改名,要么在 §7 列出来。

## 2. 让门面活得比脚本久

库形态下 ttyd 是**你进程的子进程**,脚本退出它就没了([01 §3](01-library.md))。
想让那个网页入口一直在,就得有个进程守着 —— 这就是 `serve`:

```bash
tmuxd serve   [--port 12345] [--bind 0.0.0.0] [--token …]   # 前台阻塞
tmuxd start   # 后台起,打印 URL 和 token
tmuxd stop    # 停 ttyd。会话全活着
tmuxd status  # 回头核实:pid 在不在、端口应不应答
tmuxd info    # 版本、ttyd、tmux 二进制与版本、会话统计
```

```console
$ tmuxd start
tmuxd 1.0
ttyd:  http://127.0.0.1:12345   token=8f2c1e9a…(已存 ~/.tmuxd/tmuxd/token)
tmux:  /usr/bin/tmux 3.3a   socket=tmuxd(专属)   sessions=0(server 还没起)
```

**`tmux:` 那一行只报三件事:二进制在哪、版本多少、用的哪个专属 socket。**
tmuxd 对 tmux 的全部依赖就这么多,而且 server 是懒起的([01 §4.1](01-library.md))。

```console
$ tmuxd stop
ttyd 已停。3 个会话仍在运行(tmuxd start 回来即可)。
```

**`stop` 的那行提示不是客套话**,它是这个产品最需要被理解的性质:门面和屋子是分开的。

```bash
tmuxd kill-server --tmux      # 真的要杀 tmuxd 的 tmux server(它的会话全没),需要确认
```

`--tmux` 杀的是 **tmuxd 专属的那个 tmux server**,碰不到你自己的 —— 两者在不同 socket 上。

`serve` 给 systemd / 容器 ENTRYPOINT 用;`start` / `stop` 给"手边没有进程管理器"的人用。
两条路跑的是同一份 `Tmuxd(...)`,只是外面包的壳不同。

## 3. 会话

```bash
tmuxd new  [-s ID] [-c DIR] [-e K=V]... [-- CMD...]
tmuxd ls   [-F FORMAT]
tmuxd url    -t ID [-o]          # 打印入口 URL;-o = 顺手用默认浏览器打开
tmuxd kill   -t ID
tmuxd rename -t ID NEW
tmuxd has    -t ID
```

```console
$ tmuxd new -s work -c ~/proj
work  →  http://127.0.0.1:12345/?arg=work

$ tmuxd new -s ci-42 -c ~/proj -- npm run dev
ci-42  →  http://127.0.0.1:12345/?arg=ci-42

$ tmuxd ls
work     alive   2 clients  bash    ~/proj
ci-42    alive   0 clients  node    ~/proj
stale    exited  —          —       —          7 天后自动清

$ tmuxd url -t work
http://127.0.0.1:12345/?arg=work

$ tmuxd url -t work -o          # 打开浏览器
```

- **`url` 取代了原先的 `attach`。** 名字换掉是因为它现在真的只是**打印一个地址** ——
  ttyd 原生的 `?arg=`,不经过任何跳转([02 §3](02-session.md));
- **没有 `share`。** 要给别人就把这个 URL 和 token 一起给 ——
  而那等于把这批会话全部交出去,理由见 [02 §4.3](02-session.md);
- **detach 不需要命令** —— 关掉网页就是 detach,会话照跑;
- `has` 只返回退出码,给脚本用:`tmuxd has -t work || tmuxd new -s work`;
- `-s` 不给则生成一个 id(像 tmux 的 `0` / `1` / `2`);**要重入就自己给**
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
tmuxd 不读终端内容,理由见 [03 §2](03-http.md)。

```console
$ tmuxd send -t work "npm test" --enter
✓ sent

$ tmuxd keys -t work C-c
✓ sent
```

输出只有一行 `✓ sent`,而且它的意思**仅仅是"字符已经交给 tmux 了"** ——
不是"命令跑完了",更不是"跑成功了"。想知道结果,打开 `tmuxd url -t work` 那个地址看一眼,
或者换 ssh。

`send` 与 `keys` 分成两条命令,和库上的两个方法一一对应
(`s.send()` / `s.send_key()`,[03 §5](03-http.md))——
**`tmuxd send -t x "Enter the code"` 打进去的就是这七个词,不会变成一个回车。**
这是 tmux 的 `send-keys` 最容易咬人的地方,壳这一层直接消掉。

## 5. 实例与远端

`-L` 换的是 **tmuxd 实例**,和 tmux 的写法一致:

```bash
tmuxd -L ci new -s build     # 另一套实例:自己的端口、自己的状态目录、自己的 tmux 池
```

**实例名同时决定它的 tmux socket**(`-L ci` → `tmux -L tmuxd-ci`),
所以两套实例的会话池互不可见,也都不碰你自己的 tmux —— 一个概念,不是两个
([01 §4](01-library.md))。

`-H` 指向一个**开了 HTTP 的远端 tmuxd**([03](03-http.md)):

```bash
tmuxd -H http://box:12346 ls
export TMUXD_HOST=http://box:12346
export TMUXD_TOKEN=…
```

这是 CLI 唯一走 HTTP 的时候。**能做的事因此少一档** ——
`serve` / `start` / `stop` 在 `-H` 下报错,因为进程生命周期是对面那台机器的事
([03 §8](03-http.md)):

```console
$ tmuxd -H http://box:12346 stop
✗ 远端模式下不能 stop —— ttyd 的生死归对面那个进程管
```

## 6. 配置

`~/.tmuxd.conf`,tmux 的 `set -g` 写法:

```conf
set -g port          12345
set -g history-limit 10000
set -g open-cmd      "firefox %u"     # %u = 会话 URL,给 tmuxd url -o 用
```

优先级:命令行参数 > 环境变量 > 配置文件 > 库的默认值。
配置项名字和 `Tmuxd(...)` 的构造参数一一对应([01 §8](01-library.md))——
**配置文件只是构造参数的另一种写法**,不是第二套东西。

**这里没有 tmux socket 这一项** —— 它由实例名推导,不给单独配。
唯一和 tmux 有关的是 `tmux-bin`(二进制在哪),而且平时不用设。

## 7. 和 tmux 故意不一样的地方

| | tmux | tmuxd | 为什么 |
| --- | --- | --- | --- |
| 无 `-t` 又有多个会话 | 挑最近的 | **报错** | 往错的终端敲命令代价太大 |
| `kill-server` | 杀 tmux server | **停 ttyd,会话全活** | 门面和屋子分开;要杀真的得 `--tmux` |
| `attach` | 占住你的终端 | 改名 **`url`**,只打印地址 | 它本来就是给浏览器用的([02 §3](02-session.md)) |
| `send-keys` | 一个命令,`-l` 区分 | 拆成 `send` / `keys` | 消掉"Enter 变回车"那个坑 |
| `capture-pane` | 有 | **无** | 不读终端内容([03 §2](03-http.md)) |
| `attach -r` 只读 | 有 | **无** | 这一层不分权限,锁在上层([02 §4](02-session.md)) |
| window / pane 命令 | 一大堆 | **一条都没有** | 一个会话就是一个终端([02 §1](02-session.md)) |
| 前缀键 `C-b` | 有 | **无** | CLI 不劫持键盘;要前缀键就进网页用 tmux 本体 |
| 会话名前缀匹配 | 支持 | **精确匹配**(`-t "=id"`) | `work` 匹配上 `workbench` 是真实事故 |
| `-L` | 换 tmux server | 换 **tmuxd 实例**(连带换它的 tmux socket) | 实例是一个概念,不是两个 |

## 8. 退出码

给脚本用,不要靠解析输出:

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 一般失败 |
| 2 | 用法错误(参数不对) |
| 3 | 会话不存在(`has` 用这个) |
| 4 | 状态不对(`session_exists` / `bad_id` / `port_in_use`) |
| 5 | 连不上远端 tmuxd(`-H` 模式:`unreachable` / `unauthorized`) |
| 6 | tmux server 没了(`tmux_gone`) |

4 可以改参数重试,5 检查配置,**6 该告警**。和库的异常一一对应([03 §6](03-http.md))。

## 9. CLI ↔ 库对照

CLI 不做任何库没有的事,每条命令就是一次调用:

| CLI | 库 |
| --- | --- |
| `serve` / `start` | 构造 `Tmuxd(...)` 并不让进程退出 |
| `stop` | 结束那个进程(ttyd 跟着走,会话不动) |
| `info` | `t.info()` |
| `new` | `t.session(id, cwd, cmd, env)` |
| `ls` | `t.sessions()` |
| `url` | `s.url` |
| `kill` | `s.kill()` |
| `rename` | `s.rename(new_id)` |
| `has` | `t.get(id)` 抓不抓得到 |
| `send` | `s.send(text, enter=)` |
| `keys` | `s.send_key(*keys)` |

**十一条命令,十一个库调用,一一对应。** 唯一多出来的东西是输出格式化和退出码映射,
两样都在壳里做,不进库 —— 连 id 解析都没有,它就是原样传过去的字符串。
`--json` 在 `ls` / `info` 上可用,输出就是库对象序列化后的样子,和 HTTP 那层完全一致。
