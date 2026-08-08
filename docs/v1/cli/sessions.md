# 会话:new / ls / url / kill / rename / has

一个会话就是**一个终端**:一个 id、一个启动目录、一条启动命令。没有 window,没有 pane,
没有第四个字段。

每条命令收的都是同一对参数 **`-t` / `--id`** —— `--id` 是规范写法(和库、HTTP、
webmuxd 同名),`-t` 是它的短形式。**只收一个 id,没有 `session:window.pane` 那套语法。**

`-s` / `--session` / `--target` 是 1.0.0 的拼法,仍然可用,只是不再出现在 `--help` 里。

---

## `tmuxd new`

```
tmuxd new [-t ID] [-c DIR] [-e K=V]... [-- COMMAND...]
```

**语义是"有则接上,无则创建"**(`tmux new-session -A`)。

```console
$ tmuxd new -t work -c ~/proj
work  →  http://127.0.0.1:12345/?arg=work

$ tmuxd new --id ci-42 -c ~/proj -- npm run dev
ci-42  →  http://127.0.0.1:12345/?arg=ci-42
```

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `-t, --id ID` | 自动生成(`0`、`1`、`2`…) | **要重入就自己给** |
| `-c, --cwd DIR` | `TMUXD_WORKSPACE`,再没有就当前目录 | 启动目录 |
| `-e, --env K=V` | — | 可重复。落到 `tmux new-session -e` |
| `-- COMMAND...` | `$SHELL` | 启动命令,写在 `--` 后面 |

几条要记的:

- **id 决定一切。** 对一个已存在的 id 再 `new` 一次,`-c` / `-e` / 命令**全部被忽略**,
  你接上的是原来那个现场。要换命令就先 `kill` 再建,显式的;
- **id 不能含 `.` 和 `:`,不能以 `-` 开头,不能为空**(tmux 的限制加上一点自保)。
  不合法直接报错,**不静默改写** —— 被悄悄改过的 id 你就再也找不回来了;
- **命令不存在不是错误。** 会话照样建起来,然后立刻退出,在 `ls` 里就是 `exited` ——
  和你在自己终端里敲错命令一模一样,没必要为它发明一种错误码;
- 没配 ttyd 端口时,那行箭头后面会写 `(no ttyd port configured)`。

```console
$ tmuxd new -t bad:id
✗ bad_id: session id must not contain ':'
```

---

## `tmuxd ls`

```console
$ tmuxd ls
ci-42                alive   0 clients  sh       /home/me/proj
work                 alive   1 client   bash     /home/me/proj
stale                exited  swept in 7 days
```

四列:id、状态、当前挂着几个客户端、终端里正在跑什么命令、启动目录。

**没有 window / pane 计数** —— 一个会话就是一个终端。

`clients` 是**现场问 tmux 的**:人在浏览器里打开那个 URL 就 +1,关掉就 -1。
删一个 `clients > 1` 的会话前该二次确认,别把正在看的人踢掉。

### `-F` 格式串

tmux 同款写法,给脚本用:

```console
$ tmuxd ls -F '#{session_id}|#{session_status}|#{pane_current_command}'
ci-42|alive|sh
work|alive|bash
```

支持的占位符**只有这几个**:

| 占位符 | 值 |
| --- | --- |
| `#{session_id}` / `#{session_name}` | id |
| `#{session_status}` | `alive` / `exited` |
| `#{session_attached}` | 客户端数 |
| `#{session_cwd}` / `#{session_cmd}` | 启动目录 / 启动命令 |
| `#{session_url}` | 入口地址 |
| `#{pane_current_command}` | 终端里正在跑什么 |

`--json` 输出完整对象数组(字段见 [SDK · Session](../sdk/session.md#to_dict))。

### <a id="external"></a>`external`:有人绕过了 tmuxd

会话池是 tmuxd 专属的,所以正常情况下**一条 external 都不该有**。
它出现只意味着有人直接对那个 socket 敲了 `tmux new-session`。

tmuxd 的态度是"看见、说出来、但不动手":列出来、标出来、日志记一条 warning,
**既不杀也不收编**(不给它补一份状态记录假装是自己开的)。
它照样能 `url` / `send` / `kill`,只是没有 cwd 和 cmd 的记录。

---

## `tmuxd url`

```console
$ tmuxd url -t work
http://127.0.0.1:12345/?arg=work

$ tmuxd url -t work -o          # 顺便用浏览器打开
```

这就是**入口地址本身** —— ttyd 原生的 `?arg=`,没有跳转、没有代理、没有中间层。
可以直接贴给人,它不依赖任何还活着的 Python 进程去解析。

- `-o` 用 `open-cmd` 配置项打开(`set -g open-cmd "firefox %u"`),没配就用
  `xdg-open` / `open`;
- 端口上没人守着时会往 stderr 提示一句 `nothing is listening`,但**照样把地址打出来** ——
  地址是对的,只是门暂时关着,`tmuxd start` 就好。

> **拿到这个 URL 和 token 的人,能进这个池里的任何会话** —— ttyd 的鉴权是进程级的,
> 他把 `?arg=` 换个 id 就行。**这一层不做按会话授权**,要那个就在 ttyd 前面
> 套一层你自己的代理。所以没有 `share` 命令。

---

## `tmuxd kill`

```console
$ tmuxd kill -t ci-42
killed ci-42

$ tmuxd kill -t work
killed work (2 client(s) thrown out)
```

**只有这条命令会销毁会话。** detach 不会,关网页不会,客户端全断开不会,
`tmuxd stop` 不会,你的脚本退出也不会。

正被人 attach 着也照删(tmux 会把所有客户端踢出),提示里带上踢掉了几个,
好让你解释刚才发生了什么。

---

## `tmuxd rename`

```console
$ tmuxd rename -t old new
old → new
```

新 id 已被占用会报 `session_exists`(退出码 4),不会覆盖。

---

## `tmuxd has`

只看退出码,不打印任何东西。给脚本用:

```bash
tmuxd has -t work || tmuxd new -t work -c ~/proj
```

| 退出码 | 含义 |
| --- | --- |
| 0 | 在 |
| 3 | 不在(**这不是错误**,是答案) |

---

## 错误长什么样

```console
$ tmuxd send -t ghost x
✗ no session with id "ghost"        # 退出码 3

$ tmuxd new -t bad:id
✗ bad_id: session id must not contain ':'   # 退出码 4
```

一律是 `✗` 打头、写到 **stderr**;stdout 上永远只有正常输出,可以放心 `|` 给别的命令。
