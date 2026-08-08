# `Session`

一个终端。**三个存下来的字段 —— id、cwd、cmd —— 加上随时问 tmux 要的几个实时值。**

不要自己构造它,从 `t.session()` / `t.get()` / `t.sessions()` 拿。

```python
s = t.session(id="id5", cwd="~/proj", cmd="claude")
```

没有 window,没有 pane:tmux 的多路复用在这一层不用,也不暴露。
要几个终端就开几个会话。

---

## 存下来的字段

| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 身份。`rename()` 之后这个属性会跟着变 |
| `cwd` | `str \| None` | 启动目录(绝对路径)。external 会话是 `None` |
| `cmd` | `str \| None` | 启动命令。没给就是 `None`(跑默认 shell) |
| `created_at` | `str \| None` | ISO 8601 UTC,如 `"2026-08-08T09:00:00Z"` |
| `last_attached` | `str \| None` | 上次通过库接上的时间 |
| `external` | `bool` | `True` = 有人绕过库直接开的,tmuxd 只列不管 |

这些是 tmux **答不上来**的那部分,存在状态文件里。tmux 只知道"有个叫 id5 的会话活着"。

---

## 实时值:每次访问都问一次 tmux

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

`current_command` 是"这个会话现在在干嘛"最便宜的答案 —— 它不是读屏幕内容,
只是问 tmux 前台进程叫什么。

> 每次访问都是一次 `tmux` 子进程调用。在循环里连着读四个属性就是四次调用;
> 要一次拿全用 [`to_dict()`](#to_dict)。

---

## `url`

```python
print(s.url)      # http://127.0.0.1:12345/?arg=id5
```

**入口地址本身** —— ttyd 原生的 `?arg=`,没有跳转、没有代理。
可以直接贴给人,它不依赖任何还活着的 Python 进程去解析。

id 里的特殊字符会被百分号编码。`Tmuxd(port=None)` 时是 `None`。

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

## 生命周期

### `rename(new_id) -> Session`

```python
s.rename("after")
s.id        # "after"
```

新 id 已被占用抛 `SessionExists`;非法抛 `BadId`;会话不在抛 `NoSuchSession`。

### `kill() -> int`

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
| `resize()` | 尺寸只在有人 attach 时有意义,`window-size latest` 已经处理了 | — |
| `split()` / window / pane | 多路复用是调用方的事:要几个终端开几个会话 | 多开会话 |

完整论证见 [works/03-http.md §2](../works/03-http.md)。
