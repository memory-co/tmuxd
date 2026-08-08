# tmuxd Python SDK

**这个库是 tmuxd 的核心,不是某个服务的客户端。** CLI 和 HTTP 都是套在它外面的壳。

设计依据见 [`../works/01-library.md`](../works/01-library.md),这里是接口本身。

```bash
pip install tmuxd          # 零运行时依赖;机器上要有 tmux(≥3.0)
```

ttyd 不用自己装 —— 包里自带,系统上有就优先用系统的
([works/06](../works/06-dependencies.md))。

> **走 SDK 这条链路不需要 server。** 你的进程本身就持有实例,ttyd 是你的子进程。
> 要暴露给外面,把 tmuxd 挂进**你已经在跑的那个 Web 应用**就行([tmuxd.md](tmuxd.md))。
> 需要一个独立 server 的是 CLI 那条链路 —— 见 [works/03 §1](../works/03-server.md)。

## 四行

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # 这行之后:ttyd 起来了,tmux 还没有
s = t.session(id="id5", cwd="~/proj", cmd="claude")
s.send("把测试跑一遍", enter=True)
print(s.url)                              # http://127.0.0.1:12345/?arg=id5
```

那个 URL 发给谁,谁在浏览器里就进了这个终端 —— 看得见,也能直接接手敲。

## 两个类,就这么多

| | 是什么 | 详见 |
| --- | --- | --- |
| [`Tmuxd`](tmuxd.md) | 一个实例:一个 ttyd + 一个专属会话池 | [tmuxd.md](tmuxd.md) |
| [会话](session.md) | 建 / 取 / 列,以及 `Session`:`send` / `send_key` / `kill` / `url` | [session.md](session.md) |
| [异常](errors.md) | 两个基类,分"你能改"和"环境坏了" | [errors.md](errors.md) |

```python
from tmuxd import Tmuxd, Session, NoSuchSession, SessionExists, BadId, \
                  TmuxGone, PortInUse, __version__
```

**没有远程客户端。** 要驱动别的机器上的 tmuxd,用 `ssh box tmuxd …`,
或者直接拿 `requests` 打那八个 HTTP 端点 —— 理由见
[works/03 §13](../works/03-server.md)。

## 三条你必须知道的性质

### ① 门面短命,屋子长命

```python
with Tmuxd(port=12345) as t:
    t.session(id="job-1", cwd="/srv/app", cmd="./deploy.sh")
# 这里 ttyd 没了(它是你进程的子进程)
# job-1 还在跑(tmux server 不归任何人)
```

**`close()` / `with` 退出收的是 ttyd,永远不碰会话。** 你可以写一个只跑三秒的脚本,
派完活就退出,而它派下去的会话继续跑,等下一次有人(或有程序)接上来。

要销毁会话只有一条路:`s.kill()`。

### ② 人和程序敲的是同一个终端

`s.send(...)` 打进去的,和有人在浏览器里敲进去的,进的是同一个 pane。
这不是这个库做的功能,是 tmux 白送的 —— 也正因为白送,整个设计围着它转。

### ③ 它不碰你自己的 tmux

会话池永远开在专属 socket 上(`tmux -L tmuxd`)。你的 `tmux ls` 和 `t.sessions()`
两份清单**永不相交**。`socket="default"` 会直接报错,不给你把它们混到一起的机会。

## 不读,只写

**没有 `capture()`、没有 `run()`、没有 `stream()`,也没有 `rename()`。** 这是设计,不是没来得及做:

| 你想干嘛 | 用什么 |
| --- | --- |
| 看这个会话现在什么样 | 人打开 `s.url` |
| 程序化拿一条命令的输出和退出码 | **`subprocess` / `ssh`** —— 那里有干净的 stdout 和真的退出码 |
| 程序化往一个**有人在看**的会话里投喂 | `s.send()` ← 只有这件事别处办不了 |

理由展开在 [works/03-server.md §7](../works/03-server.md)。

## 一个完整的例子:Agent 宿主

在若干个仓库目录里各跑一个 CLI Agent,把任务投进去,然后**让人看着它干活**:

```python
from tmuxd import Tmuxd, NoSuchSession

t = Tmuxd(port=12345, token=TOKEN)

def sid(proj):                                  # id 怎么算是你的事,tmuxd 不管
    return "%s--%s" % (job_id, proj.strip("/").replace("/", "-"))

for proj in projects:
    s = t.session(id=sid(proj), cwd=proj, cmd="claude")
    s.send(task_prompt, enter=True)
    print("%s: %s" % (proj, s.url))             # 把入口贴进工单

# ... 几小时后,另一个进程里 ...
t = Tmuxd(port=12345, token=TOKEN)              # 复用已经在跑的那个 ttyd
for proj in projects:
    try:
        t.get(sid(proj)).send("/status", enter=True)
    except NoSuchSession:
        print("%s 的会话没了" % proj)
```

**这段代码从头到尾没有读过一个字符。** 它做的是"把活派下去、把入口交出去",
判断干得怎么样是点开链接那个人的事。

`sid()` 那两行就是整个"寻址协议" —— 它长在调用方里,因为只有调用方知道
"同一个东西"指的是什么。tmuxd 收到的永远只是一个字符串。

## 和 CLI / HTTP 的关系

三个壳,一个内核,名字一一对应:

| 库 | CLI | HTTP |
| --- | --- | --- |
| `t.session(id, cwd, cmd, env)` | `tmuxd new -t ID -c DIR -- CMD` | `POST /api/sessions` |
| `t.sessions()` | `tmuxd ls` | `GET /api/sessions` |
| `t.get(id)` | `tmuxd has -t ID` | `GET /api/sessions/{id}` |
| `t.info()` | `tmuxd info` | `GET /api/info` |
| `s.send(text, enter=)` | `tmuxd send -t ID TEXT --enter` | `POST /api/sessions/{id}/keys` |
| `s.send_key(*keys)` | `tmuxd keys -t ID KEY...` | 同上,`{"keys": [...]}` |
| `s.kill()` | `tmuxd kill -t ID` | `DELETE /api/sessions/{id}` |
| `s.url` | `tmuxd url -t ID` | 响应里的 `url` 字段 |

**出问题时可以把任意一层翻译成另一层**,这是名字不搞"更友好的改名"的全部理由。
CLI 那一列的 `-t` 就是 `--id` 的短形式 —— 三层里这个东西都叫 `id`,
同家族的 webmuxd 也叫 `id`。
