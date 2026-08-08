# 会话

**一个会话就是一个终端**:一个 id、一个启动目录、一条启动命令。没有 window,没有 pane,
没有第四个字段。

这一篇讲会话的全部:**怎么建、怎么取、拿到之后能干什么**。
实例本身(构造参数、ttyd、socket)在 [tmuxd.md](tmuxd.md)。

---

## 建一个,或者接上已有的

### `t.session(id=None, cwd=None, cmd=None, env=None) -> Session`

**有则接上,无则创建**(`tmux new-session -A` 的语义)。最常用的一个。

```python
s = t.session(id="work", cwd="~/proj", cmd="npm run dev")
s = t.session(id="work")            # 再来一次:接上原来那个
s = t.session()                     # id 不给就生成:"0"、"1"、"2"…
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `id` | 生成 | 不能含 `.` `:`,不能以 `-` 开头,不能为空,≤200 字符 |
| `cwd` | 实例的 `workspace` | `~` 会展开,相对路径按当前进程的 cwd 解析成绝对路径 |
| `cmd` | 默认 shell | 一整条命令行字符串,交给 tmux 起 |
| `env` | 无 | `{"K": "V"}`,落到 `tmux new-session -e` |

**已存在的会话不会因为这次给的 `cwd` / `cmd` 不同而被重建 —— id 说了算。**
要换命令就先 `kill()` 再建,显式的。

**命令不存在不会抛异常**:会话建起来后立刻退出,`status` 是 `exited`。
这和你在自己终端里敲错命令是一回事。

抛:`BadId`、`TmuxGone`。

### `t.create(id=None, cwd=None, cmd=None, env=None) -> Session`

同上,但 id 已存在时抛 `SessionExists` 而不是接上。

用在"我确信这是个新东西"的地方 —— 撞了说明有 bug,该让它响。

### `t.get(id) -> Session`

**只接不建**,不存在抛 `NoSuchSession`。

```python
try:
    t.get("work").send("继续", enter=True)
except NoSuchSession:
    ...
```

`session()` 和 `get()` 是两个动词而不是一个布尔参数,因为
**"我以为会接上结果开了个新的"是最难查的那类 bug**。

### `t.has(id) -> bool`

存在返回 `True`。id 非法也返回 `False`(不抛)。

```python
if not t.has("work"):
    t.session(id="work", cwd="~/proj")
```

### `t.sessions() -> list[Session]`

全部会话,**每次都现场跑一次 `tmux ls`**,不读缓存。

```python
for s in t.sessions():
    print(s.id, s.status, s.clients, s.current_command)
```

这个方法同时做三件事:

- **对账**:记录里有、tmux 里没有的标 `exited`;<a id="external"></a>tmux 里有、
  记录里没有的标 `external`(有人绕过库直接开的 —— 列出来,**既不杀也不收编**);
- **回收**:`exited` 且超过 `gc_ttl` 的记录删掉。**只删 JSON 文件,永远不 kill 活会话**;
- 顺带刷新 `clients` / `current_command` 这些实时字段。

> **对账只在这里发生 —— 没有后台线程,没有定时器。**
> 一个被 `import` 进来的库不该背着调用方每 60 秒醒一次、写一次盘。

**tmux server 还没起来时返回 `[]`,不是抛异常** —— tmux 在 server 不存在时以 exit 1
报 `error connecting to ...`,库把它读成空列表。这是实现上最容易写错的一处。

### `t.url_for(id) -> str`

不需要会话存在,纯算字符串。

```python
t.url_for("work")        # http://127.0.0.1:12345/?arg=work
```

---

## `Session`

不要自己构造它,从上面几个方法拿。

### 存下来的字段

| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 身份 |
| `cwd` | `str \| None` | 启动目录(绝对路径)。external 会话是 `None` |
| `cmd` | `str \| None` | 启动命令。没给就是 `None`(跑默认 shell) |
| `created_at` | `str \| None` | ISO 8601 UTC,如 `"2026-08-08T09:00:00Z"` |
| `last_attached` | `str \| None` | 上次通过库接上的时间 |
| `external` | `bool` | `True` = 有人绕过库直接开的 |

这些是 tmux **答不上来**的那部分,存在状态文件里。tmux 只知道"有个叫 id5 的会话活着"。

### 实时值:每次访问都问一次 tmux

**都是 property,不缓存。** 拿到手就是当下的事实。

| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `status` | `str` | `"alive"` / `"exited"` |
| `alive` | `bool` | `status == "alive"` |
| `clients` | `int` | 当前挂着几个客户端。人打开网页 +1,关掉 -1 |
| `current_command` | `str \| None` | 终端里**正在跑**什么(`bash` / `vim` / `claude`…) |

```python
if s.alive and s.clients == 0:
    print("没人在看", s.url)
```

`current_command` 是"这个会话现在在干嘛"最便宜的答案 —— 它不读屏幕内容,
只是问 tmux 前台进程叫什么。

> 每次访问都是一次 `tmux` 子进程调用。连着读四个属性就是四次调用;
> 要一次拿全用 [`to_dict()`](#to_dict)。

### `url`

```python
print(s.url)      # http://127.0.0.1:12345/?arg=id5
```

**入口地址本身** —— ttyd 原生的 `?arg=`,没有跳转、没有代理。
可以直接贴给人,它不依赖任何还活着的 Python 进程去解析。
id 里的特殊字符会被百分号编码。

> **拿到这个 URL 和 token 的人能进这个池里的任何会话** —— ttyd 的鉴权是进程级的。
> 要按会话授权,在 ttyd 前面套一层你自己的代理。

---

## 写入:两个方法

### `send(text, enter=False) -> Session`

**打字面量。一个字符都不解释。**

```python
s.send("npm test", enter=True)
s.send("--help me")                # 前导横杠不会被当成选项
s.send("Enter the code")           # 打进去的就是这七个词
```

底层是 `tmux send-keys -l`;`enter=True` 之后再补一个回车键。

### `send_key(*keys) -> Session`

**按键。** 收 tmux 的键名。

```python
s.send_key("C-c")
s.send_key("Escape", ":", "w", "q", "Enter")
```

`Enter` `Escape` `Tab` `Space` `BSpace` `Up` `Down` `C-c` `C-d` `M-x` `F1`…

### 为什么分成两个

`tmux send-keys` 不加 `-l` 时会把参数当**键名**解析,于是 `"Enter the code"` 里的
`Enter` 变成回车。**tmuxd 把这个坑挡在了接口形状上**,不给你混着写的机会。

### 返回值的含义

两个方法都返回 `self`(可以串),而**返回这件事只意味着"字符已经交给 tmux 了"**。

不是"命令跑完了",更不是"跑成功了" —— 这一层不读终端,没有能力知道后续。
想知道结果,让人打开 `s.url`,或者根本别用 tmuxd,用 `ssh`。

会话已经不在时抛 `NoSuchSession`。

---

## `kill() -> int`

**销毁会话,返回被踢掉的客户端数。**

```python
if s.clients > 1:
    ...                     # 有人正看着,该二次确认
n = s.kill()
```

- **只有这个方法会销毁会话。** detach 不会,关网页不会,`t.close()` 不会,
  你的进程退出不会;
- 正被人 attach 着也照删,tmux 会把所有客户端踢出;
- 会话已经不在时不报错,只把状态记录清掉,返回 0。

---

## <a id="to_dict"></a>`to_dict() -> dict`

一次把全部字段取齐(实时值只探一轮),也是 HTTP 那层返回的原样。

```python
{
  "id": "id5",
  "cwd": "/home/me/proj",
  "cmd": "claude",
  "status": "alive",
  "clients": 2,
  "current_command": "claude",
  "created_at": "2026-08-08T09:00:00Z",
  "last_attached": "2026-08-08T10:30:00Z",
  "url": "http://127.0.0.1:12345/?arg=id5",
}
```

external 会话多一个 `"external": true`,并且 `cwd` / `cmd` / `created_at` 是 `None`。

---

## 没有的东西

| 想要 | 为什么没有 | 改用 |
| --- | --- | --- |
| `capture()` 抓屏 | 抓出来是**屏幕**不是日志,受宽度折行,全屏程序只给一帧 | 人打开 `s.url` |
| `run()` 拿退出码 | 得往命令后面拼标记、还得先判断里面是不是闲着的 shell | `subprocess` / `ssh` |
| `stream()` 输出流 | 要挂 `pipe-pane`、解析 ANSI、还会把密码落盘 | 同上 |
| `rename()` | **id 是身份不是标签。** 调用方靠"同一个 id 指向同一个现场"重入,改名正好破坏这条 | `kill()` 掉再建一个 |
| `resize()` | 尺寸只在有人 attach 时有意义,`window-size latest` 已经处理了 | — |
| `split()` / window / pane | 多路复用是调用方的事:要几个终端开几个会话 | 多开会话 |

完整论证见 [works/02 §6.1](../works/02-session.md) 与
[works/03 §2](../works/03-http.md)。
