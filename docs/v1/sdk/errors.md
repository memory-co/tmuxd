# 异常

**两个基类,分法就是全部要点:**

```
TmuxdError
├── SessionError    你要的东西不对 —— 改参数就能好
└── PlatformError   环境坏了 —— 该告警,别重试
```

```python
from tmuxd import SessionError, PlatformError
```

写重试逻辑时按基类判就够了:

```python
try:
    s = t.session(id=sid, cwd=proj, cmd="claude")
except SessionError as exc:
    log.warning("跳过 %s: %s", proj, exc.message)   # 改一下还能继续
except PlatformError:
    raise                                          # 这台机器出事了
```

---

## `SessionError` —— 能自愈

| 类 | `code` | 什么时候 |
| --- | --- | --- |
| `NoSuchSession` | `no_such_session` | `get()` / `send()` / `kill()` 到一个不存在的 id |
| `SessionExists` | `session_exists` | `create()` 撞了已有 id;`rename()` 的新名字被占了 |
| `BadId` | `bad_id` | id 为空、含 `.` 或 `:`、以 `-` 开头、超过 200 字符 |

```python
from tmuxd import NoSuchSession

try:
    t.get("work").send("继续", enter=True)
except NoSuchSession:
    t.session(id="work", cwd="~/proj")     # 建一个就是了
```

**`BadId` 不会被静默改写。** 一个被悄悄改过的 id,你下次就再也找不回那个现场了。

---

## `PlatformError` —— 该告警

| 类 | `code` | 什么时候 |
| --- | --- | --- |
| `TmuxMissing` | `tmux_missing` | PATH 里没有 tmux,或版本低于 3.0 |
| `TtydMissing` | `ttyd_missing` | PATH 里没有 ttyd(只在要起 ttyd 时才检查) |
| `TtydFailed` | `ttyd_failed` | ttyd 起来就退了,或者没在超时内开始监听 |
| `PortInUse` | `port_in_use` | 端口上是别人的东西 —— **不猜、不抢** |
| `TmuxGone` | `tmux_gone` | tmux server 没了,或者某条 tmux 命令失败了 |
| `Unauthorized` | `unauthorized` | token 不对(`RemoteTmuxd`) |
| `Unreachable` | `unreachable` | 连不上远端(`RemoteTmuxd`) |

这几个重试没有意义:PATH 里长不出 tmux,端口不会自己空出来。

---

## 每个异常带什么

```python
exc.message      # 人读的一句话
exc.code         # 机器码,和 HTTP 的 error 字段一模一样
exc.details      # dict,附加上下文
exc.to_dict()    # {"error": code, "message": ..., "details": {...}}
```

```python
try:
    Tmuxd(port=8080)
except PortInUse as exc:
    print(exc.code)       # port_in_use
    print(exc.details)    # {'port': 8080}
```

`code` 不是给日志看的装饰 —— **HTTP 那层的错误码就是它原样序列化的**,
而 `RemoteTmuxd` 收到响应后再按 `code` 还原成同一个异常类。所以:

```python
local  = Tmuxd(port=12345)
remote = RemoteTmuxd("http://box:12346", token="…")

for t in (local, remote):
    try:
        t.get("ghost")
    except NoSuchSession:      # 本地远程同一个 except,不用写两遍
        ...
```

---

## 不是异常的几件事

| 情况 | 实际行为 |
| --- | --- |
| tmux server 还没起来就 `sessions()` | 返回 `[]`。没有会话不是错误 |
| `has()` 传了个非法 id | 返回 `False`,不抛 |
| 启动命令不存在 | 会话建起来后立刻退出,`status == "exited"` |
| `kill()` 一个已经没了的会话 | 不报错,清掉记录,返回 0 |
| 会话里跑的程序崩了 | 会话变 `exited`,记录留着,7 天后回收 |

最后一条尤其要清楚:**tmuxd 不监控你会话里的东西**。它只知道那个 tmux 会话还在不在。

---

## 构造参数不对是 `ValueError`

两条守则用标准 `ValueError`,因为它们是**调用代码写错了**,不是运行时状况:

```python
Tmuxd(socket="default")            # ValueError: 那会把会话开进你自己的 tmux
Tmuxd(port=7681, bind="0.0.0.0")   # ValueError: 对外监听必须给 token
```

第二条不给"我待会再加"的机会:那是把一台机器的 shell 放到网上。
