# 04 · CLI

CLI 是**套在库外面的第三个壳**,但它和 SDK 那条链路有一处根本不同:

> **CLI 必须有一个 server 在跑。** `tmuxd ls` 是个活几十毫秒就退出的进程 ——
> 它持不住 ttyd(ttyd 会跟着它死),也持不住会话状态。
> 所以它只能**去问一个持得住的东西**,那就是 `tmuxd serve`([03 §1](03-server.md))。

```bash
pip install "tmuxd[server]"     # CLI 和 server 是一体的
tmuxd start                     # 先有 server
tmuxd new -t work               # CLI 才有东西可打
```

CLI 打的是**本机管控口**(默认 `127.0.0.1:7682`),和 ttyd 那个口分开(§5)。
要驱动别的机器,用 `ssh box tmuxd …`,理由见 §5.1。

**tmux 是参考,不是对齐目标。** 借它的短字母是因为顺手,不是因为要兼容它 ——
tmuxd 的语法是 tmuxd 自己的,而它真正要对齐的是**同一个家族里的 webmuxd**(§3.1)。
要记的东西格外少:十三条命令,一个 id。

## 1. 先说清楚:它不取代 tmux

**你人在那台机器的终端里,想开个自己的会话干活,就用 `tmux`。**
那是你的 tmux,tmuxd 看不见也不碰([01 §4](01-library.md))。

`tmuxd` 的 CLI 管的是**另一套**东西 —— tmuxd 自己那个会话池:

| 场景 | 用什么 |
| --- | --- |
| 我 ssh 在那台机器上,想开个会话自己干活 | `tmux`(你自己的 socket,与 tmuxd 无关) |
| 我要在远端机器上跑一条命令、拿输出和退出码 | **`ssh`** —— 不是 tmuxd([03 §7](03-server.md)) |
| 我要驱动**另一台机器上**的 tmuxd | **`ssh box tmuxd …`** —— 没有 `-H`,见 §5.1 |
| 我要在一个**有人在看**的会话里投喂一条指令 | `tmuxd send` |
| 我要开个会话,把入口发给别人 | `tmuxd new` + `tmuxd url` |
| 我要看 tmuxd 手里现在有哪些会话、谁挂着 | `tmuxd ls` |
| 我在写 Python 调用方,想先手敲试试 | `tmuxd`(每条命令就是一次库调用,§9) |

第二行要专门看一眼:**`tmuxd` 里没有"跑命令拿结果"这种事**。
要那个就用 ssh,它本来就更直([03 §7](03-server.md))。

设计纪律:**借 tmux 的短字母,但语义和长名跟着 tmuxd 自己走。**
凡是容易被 tmux 习惯带错预期的地方,在 §7 列出来。

## 2. 让门面活得比脚本久

库形态下 ttyd 是**你进程的子进程**,脚本退出它就没了([01 §3](01-library.md))。
想让那个网页入口一直在,就得有个进程守着 —— 这就是 `serve`:

```bash
tmuxd serve   [--port 12345] [--bind 0.0.0.0] [--token …] [--http-port 12346]
tmuxd start   # 同样的参数,只是转到后台
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

`serve` 给 systemd / 容器 ENTRYPOINT 用;`start` / `stop` 给"手边没有进程管理器"的人用
(`start` 就是把 `serve` 用 `start_new_session` 甩到后台,再轮询等它就绪 ——
起不来就把 `daemon.log` 的尾巴摆到眼前,而不是丢个 pid 让人自己找)。
两条路跑的是同一份 `Tmuxd(...)`,只是外面包的壳不同。

`--http-port` 才会把 HTTP 壳一起起来([03 §1](03-server.md)),默认没有。

## 3. 会话

```bash
tmuxd new  [-t ID] [-c DIR] [-e K=V]... [-- CMD...]
tmuxd ls   [-F FORMAT]
tmuxd url    -t ID [-o]          # 打印入口 URL;-o = 顺手用默认浏览器打开
tmuxd kill   -t ID
tmuxd has    -t ID
```

**每条命名会话的命令都是同一对参数:`-t` / `--id`。**
为什么不是 tmux 的 `-s` / `-t` 两套,见 §3.1。

```console
$ tmuxd new -t work -c ~/proj
work  →  http://127.0.0.1:12345/?arg=work

$ tmuxd new --id ci-42 -c ~/proj -- npm run dev
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
- `has` 只返回退出码,给脚本用:`tmuxd has -t work || tmuxd new -t work`;
- `-t` 不给则生成一个 id(像 tmux 的 `0` / `1` / `2`);**要重入就自己给**
  ([02 §2.1](02-session.md));
- **`ls` 里没有 window / pane 计数** —— 一个会话就是一个终端([02 §1](02-session.md));
- `-F` 用 tmux 同款占位符:`#{session_id}` `#{session_attached}`
  `#{pane_current_command}` `#{session_status}`。`#{session_name}` 是
  `#{session_id}` 的别名,保留但不是规范写法(§3.1)。

### 3.1 一个东西只该有一个名字,而那个名字来自 webmuxd

`session id` 不是 tmux 的概念(tmux 叫 session **name**),它是
**tmuxd 和 webmuxd 共用的那个概念** —— 一个调用方自己定的字符串,
服务按它寻址、按它重入。webmuxd 的会话对象就长这样:

```jsonc
// webmuxd api/server.md
{ "id": "work", "runtime": "container", "state": "ready", ... }
{ "id": "work",   // 必填,你自己定
```

**所以命名的权威在家族里,不在 tmux。** 而同一个字符串在 tmuxd 里曾经有四个名字:

| 层 | 叫法 |
| --- | --- |
| 库 | `t.session(id=…)`、`t.get(id)`、`s.id` |
| HTTP | `POST {"id": …}`、`/api/sessions/{id}` |
| CLI(1.0.0) | `new -s ID`,**其余** `-t ID` |
| `-F` | `#{session_id}` 和 `#{session_name}` 两个都认 |

这违反了两条:**家族里同一个概念该同一个名字**,以及这一层最该守的
**出问题时可以把任意一层翻译成另一层**([03 §13](03-server.md))。
而且不只是不整齐,有两处是实际会误导人的:

**① `--target` 在替一个不存在的概念占名字。** tmux 的 target 是带语法的
(`session:window.pane`),而 tmuxd **没有那套语法**([02 §2](02-session.md))。
叫 `--target` 会让人以为可以写 `work:1`。**借字母可以,借错概念不行。**

**② `new -s` 和其余 `-t` 的区分在这里没有意义。** 这个区分是 tmux 的:
`new-session -s` 给新东西起名,别的命令 `-t` 指认已有的。
但 tmuxd 的 `new` 是**有则接上、无则创建** —— 它完全可能正在指认一个已存在的会话。
"起名"和"指认"在这一层是同一件事,**同一个值不该因为动词不同就换个参数名**。

定下来的写法:

```
-t, --id ID        每条命名会话的命令都用这一对
```

- **`-t` 这个字母留着** —— 顺手,而且它在 tmux 和 webmuxd 里都是这个位置。
  **借的是字母,不是概念**;
- **长名是 `--id`** —— 和库、和 HTTP、和 webmuxd、和 `#{session_id}` 全部对上。
  **`--id` 是规范写法,`-t` 只是它的短形式**;
- **`-s` / `--session` / `--target` 保留为别名**,不打印在 `--help` 里。
  1.0.0 已经发到 PyPI,`tmuxd new -s work` 必须继续能跑。

顺带一条纪律:**新增参数的长名以库/家族里的名字为准。** 短字母可以向 tmux 借,
长名不行 —— 那会让"翻译成另一层"和"家族里叫法一致"这两件事一起烂掉。

## 4. 往里敲

```bash
tmuxd send -t ID "npm test" [--enter]       # 字面量
tmuxd keys -t ID C-c q Enter                # tmux 键名
```

**就这两条。** 没有 `capture`、没有 `run`、没有 `wait`、没有 `stream`、没有 `watch` ——
tmuxd 不读终端内容,理由见 [03 §7](03-server.md)。

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
(`s.send()` / `s.send_key()`,[03 §10](03-server.md))——
**`tmuxd send -t x "Enter the code"` 打进去的就是这七个词,不会变成一个回车。**
这是 tmux 的 `send-keys` 最容易咬人的地方,壳这一层直接消掉。

## 5. 实例,以及"远端"这件事

`-L` 换的是 **tmuxd 实例**:

```bash
tmuxd -L ci new -t build     # 另一套实例:自己的端口、自己的状态目录、自己的 tmux 池
```

**实例名同时决定它的 tmux socket**(`-L ci` → `tmux -L tmuxd-ci`),
所以两套实例的会话池互不可见,也都不碰你自己的 tmux —— 一个概念,不是两个
([01 §4](01-library.md))。

### 5.1 没有 `-H`:远端交给 ssh

```bash
ssh box tmuxd new -t work -c ~/proj
ssh box tmuxd send -t work "npm test" --enter
ssh box tmuxd url -t work
```

早先的稿子有个 `-H` 指向远端 tmuxd 的 HTTP 口,**去掉了**。`ssh` 覆盖它的全部用途,
而且更好:

- **不用多开一个端口**,不用再管一份 token;
- **复用 ssh 的鉴权和审计** —— 谁在什么时候敲了什么,你的 sshd 日志里本来就有;
- **CLI 因此只有一条代码路径**。原来 `-H` 模式下 `serve` / `start` / `stop` 要
  特殊报错(进程生命周期是对面的事)、退出码要多一档、错误要多两类 ——
  **一条只在少数人用、却让所有人多背一套分支的路径,不值得。**

HTTP 壳([03](03-server.md))照旧存在,它是给**够不着 shell** 的调用方留的:
别的语言、别的容器、CI runner。有 ssh 的人本来就不需要它。

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

## 7. 别被 tmux 的习惯带错

tmux 是参考,所以这张表不是"违规清单",而是**给带着 tmux 肌肉记忆的人看的路标** ——
下面这些地方,tmuxd 的答案和你手指的记忆不一样:

| | tmux | tmuxd | 为什么 |
| --- | --- | --- | --- |
| 无 `-t` 又有多个会话 | 挑最近的 | **报错** | 往错的终端敲命令代价太大 |
| `kill-server` | 杀 tmux server | **停 ttyd,会话全活** | 门面和屋子分开;要杀真的得 `--tmux` |
| `attach` | 占住你的终端 | 改名 **`url`**,只打印地址 | 它本来就是给浏览器用的([02 §3](02-session.md)) |
| `send-keys` | 一个命令,`-l` 区分 | 拆成 `send` / `keys` | 消掉"Enter 变回车"那个坑 |
| `capture-pane` | 有 | **无** | 不读终端内容([03 §7](03-server.md)) |
| `attach -r` 只读 | 有 | **无** | 这一层不分权限,锁在上层([02 §4](02-session.md)) |
| window / pane 命令 | 一大堆 | **一条都没有** | 一个会话就是一个终端([02 §1](02-session.md)) |
| 前缀键 `C-b` | 有 | **无** | CLI 不劫持键盘;要前缀键就进网页用 tmux 本体 |
| 会话名前缀匹配 | 支持 | **精确匹配**(`-t "=id"`) | `work` 匹配上 `workbench` 是真实事故 |
| `-t` 是 target(带 `session:window.pane` 语法) | 是 | **`-t/--id`,只收一个 id** | 那套语法这一层没有;而且 `id` 是家族的概念,不是 tmux 的(§3.1) |
| `new -s` / 其余 `-t` 两套 | 是 | **统一 `-t/--id`** | `new` 是有则接上,"起名"和"指认"在这里是同一件事(§3.1) |
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
| 5 | *保留* —— 曾是"连不上远端 tmuxd",`-H` 去掉后没有产出者(§5.1) |
| 6 | tmux server 没了(`tmux_gone`) |

4 可以改参数重试,**6 该告警**。和库的异常一一对应([03 §11](03-server.md))。

**5 空着不复用。** 已经发出去的退出码含义不该改 —— 有人的脚本可能在判 5。

## 9. CLI ↔ 库对照

CLI 不做任何库没有的事,每条命令就是一次调用:

| CLI | 库 |
| --- | --- |
| `serve` / `start` | 构造 `Tmuxd(...)` 并不让进程退出 |
| `stop` | 结束那个进程(ttyd 跟着走,会话不动) |
| `info` | `t.info()` |
| `new -t ID` | `t.session(id, cwd, cmd, env)` |
| `ls` | `t.sessions()` |
| `url` | `s.url` |
| `kill` | `s.kill()` |
| `has` | `t.get(id)` 抓不抓得到 |
| `send` | `s.send(text, enter=)` |
| `keys` | `s.send_key(*keys)` |

**每条命令就是一次库调用,一一对应。** 唯一多出来的东西是输出格式化和退出码映射,
两样都在壳里做,不进库 —— 连 id 解析都没有,它就是原样传过去的字符串。
`--json` 在 `ls` / `info` 上可用,输出就是库对象序列化后的样子,和 HTTP 那层完全一致。

## 10. 兼容

`-s` / `--session` / `--target` 是 `-t` / `--id` 的别名,**不设移除期限**。
它们不出现在 `--help` 里,但永远能用。

一个已经发出去的参数名,留着的成本是一行 argparse,删掉的成本是别人的脚本 —— 不对等。
