# tmuxd

**tmuxd = 一个 Python 库,把 tmux 和 ttyd 拼成"活得比连接久、还能被程序往里敲的终端"。**

`ttyd tmux new -A -s work` 这条命令,大家都写过。它把两个东西拼在一起:tmux 负责会话活着,
ttyd 负责让你在浏览器里看见。拼是拼上了,但拼出来的东西**没有把手**——
会话是谁开的、开在哪个目录、还活着没有,一概问不到;想从外面往里投喂一条指令,
只能 ssh 进去敲 `tmux send-keys`。

tmuxd 把这条命令做成一个**你 import 进来的东西**:

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # ← 这行之后:ttyd 起来了,tmux 还没有
s = t.session(id="id5", cwd="/home/me/proj", cmd="claude")
s.send("把测试跑一遍", enter=True)
print(s.url)                              # http://localhost:12345/?arg=id5
```

四行。第四行那个 URL 发给谁,谁在浏览器里就进了这个终端 —— 看得见,也能直接接手敲。

## 核心是库,不是服务

**不是"先有服务再配个 SDK",而是"先有库,需要的时候把它暴露出去"。**

```
        ┌── 你的 Python 程序   import tmuxd        ← 最短的一条,大多数人到这里就够了
        │
tmuxd ──┼── CLI               tmuxd new / send    ← 同一个库,套了个命令行
 (库)   │
        └── HTTP(可选)       POST /sessions/…    ← 同一个库,套了个 HTTP 口
                                                     给别的语言、别的机器用
```

大多数调用方(编排程序、Web 后端、脚本)本来就和它在同一个进程里。
让它们绕一圈 HTTP 去调自己进程里就能做的事,是白交的税 —— 所以 **HTTP 默认不开**
([03 §1](03-http.md))。

## 会话模型小到一句话

> **一个 id、一个启动目录、一条启动命令。** 没有第四个字段。

```python
s = t.session(id="id5", cwd="/home/me/proj", cmd="claude")
```

语义就是 `tmux new-session -A`:有则接上,无则创建。id 是**调用方给的**,
因为只有调用方知道"同一个东西"指的是什么([02 §2](02-session.md))。

## 五条收窄

这份设计是**一路减出来的**,减掉的每一样都比留下的更能说明它是什么。

**① 一个会话就是一个终端。** tmux 的多路复用(window / pane)**不用**。
tmuxd 只借 tmux 的一件事:让 shell 活得比连接久、能被多人同时看见。
要多个终端就多开几个会话 —— 这本来就是调用方在做的事([02 §1](02-session.md))。

**② 不读,只写。** 没有 `capture`、没有 `run`、没有输出流、没有录制、没有事件流。
读终端内容这件事要么归**人**(打开那个 URL 看,ttyd 已经做完了),要么归 **ssh**
(程序化拿输出和退出码,那条路本来就更直)。只留一个写入动作:往会话里敲
([03 §2](03-http.md))。

**③ 全部可读可写,不做权限。** 没有只读 attach,没有只读链接,也没有 `share`。
ttyd 的鉴权是进程级的,在这一层做"只读"只是**假的安全感**。
真要区分谁能看谁能写,那需要身份系统,**往上层去锁**([02 §4](02-session.md))。

**④ 会话池是专属的,不碰你自己的 tmux。** 对 tmux 的全部依赖是"二进制在哪",
然后一律 `tmux -L tmuxd` 开一套独立 server。你的 `tmux ls` 和它的 `t.sessions()`
两份清单永不相交 —— 既让 tmuxd 能对自己那批会话全权负责,更让它**没有能力**
动你那些跑了三天的工作会话([01 §4](01-library.md))。

**⑤ 没有自己发明的路径。** 进终端的地址就是 ttyd 原生的 `?arg=<id>` ——
不做反向代理、不做 `/s/<id>/`、不做 302 跳转([02 §3](02-session.md))。
早先那套 `claude:///proj?window=main&block=2` 的 URI 寻址也还给 shellbase 了:
**调用方自己算出一个 id,tmuxd 只认 id**([02 §2.2](02-session.md))。

减到最后剩下的是一句话:**让会话活着,让人看见,让程序往里敲。**

同一条判据也决定了两个外部依赖怎么处理:**ttyd 是实现细节,可以自带;
tmux 是契约的一部分,只探测**([06](06-dependencies.md))。

## 两条不对称,这东西的全部价值

**① 门面短命,屋子长命。**

```python
with Tmuxd(port=12345) as t:
    t.session(id="job-1", cwd="/srv/app", cmd="./deploy.sh")
# 这里 ttyd 没了(它是你进程的子进程),job-1 还在跑(tmux server 不归任何人)
```

你可以写一个只跑三秒的脚本,派完活就退出,而它派下去的会话还在跑
([01 §3](01-library.md))。

**② 人和程序敲的是同一个终端。**
`s.send(...)` 打进去的和有人在网页里敲进去的,进的是同一个 pane。
这不是我们做的功能,是 tmux 白送的([02 §5](02-session.md))——
也正因为白送,才值得整个设计围着它转。

## tmux / ttyd 对照

| tmux / ttyd | tmuxd |
| --- | --- |
| tmux server | 底下**就是**一个 tmux server(专属 `-L tmuxd`,**懒起**) |
| tmux session | session,**一个会话 = 一个终端** |
| tmux window / pane | — **不做**,见 [02 §1](02-session.md) |
| `tmux new -s work` | `t.session(id="work", cwd=…, cmd=…)` |
| `tmux new-session -A` | 同上 —— 就是这个语义 |
| `tmux attach -t work` | 浏览器打开 `s.url` |
| detach(`C-b d`) | 关掉网页 |
| `tmux attach -r` | — **不做**,一律可写 |
| `tmux ls` | `t.sessions()` |
| 会话名 | `id` —— 调用方给,tmuxd 不解释([02 §2](02-session.md)) |
| `tmux send-keys -l` | `s.send(text, enter=True)` |
| `tmux send-keys` | `s.send_key("C-c")` —— **写入动作只有这两个** |
| `tmux capture-pane` | — **不做**,要看就打开 URL,要抓输出用 ssh |
| `tmux pipe-pane` | — **不做** |
| scrollback / `history-limit` | 人在网页里往回滚,tmux 自己的事 |
| `tmux kill-session` | `s.kill()` |
| `-L name` / `-S path` | `Tmuxd(socket="ci")` —— 换实例,连带换 tmux socket |
| **ttyd** `-p PORT` | `Tmuxd(port=12345)` |
| **ttyd** `-a` URL 传参 | **原样用**:`?arg=<id>` 就是 attach 入口 |
| **ttyd** `-c user:pass` | `Tmuxd(token=…)` —— 进程级鉴权([01 §7](01-library.md)) |
| **ttyd** 默认只读,`-W` 才可写 | 一律 `-W`,权限交给上层 |

**没有多出来的概念。** 一张表看完,tmuxd 就是"tmux 的一个子集,加一个 ttyd,加几行胶水"。

## 它跑起来之后,你的 tmux 什么都没变

```bash
tmux ls          # 你自己的会话,一个不多一个不少
```

一句话的价值主张:**给一台机器加一个终端服务口,而不动这台机器上原有的任何东西。**

## 文档

**这里是设计稿(为什么这么定)。** 要查怎么用,看使用文档:
[CLI](../cli/) · [Python SDK](../sdk/)。

| 文件 | 内容 |
| --- | --- |
| [01-library.md](01-library.md) | `Tmuxd` 对象、进程模型、专属 socket、状态、鉴权、构造参数 |
| [02-session.md](02-session.md) | 一个会话一个终端、身份就是 id、URL、多客户端、生命周期、对账 |
| [03-http.md](03-http.md) | 可选的 HTTP 壳:为什么默认不开、端点表、往里敲、错误码 |
| [04-cli.md](04-cli.md) | 命令行,照 tmux 设计;以及它为什么不取代 tmux |
| [05-consumers.md](05-consumers.md) | 谁该用它:shellbase 的迁移清单、与 webmuxd 的关系 |
| [06-dependencies.md](06-dependencies.md) | 两个依赖两种态度:tmux 只探测,ttyd 自带兜底 |

## 明确不做

保持它是个工具,不是平台。判断新功能该不该加,问一句:**tmux 会做这个吗?** 不会就别加。
而且还要再问一句:**这件事非得在这一层做吗?** 能往上放、能交给 ssh、能交给 ttyd 的,就别自己扛。

- ❌ **读终端内容**(capture / run / 输出流 / 录制)—— 归人或归 ssh([03 §2](03-http.md))
- ❌ **事件流** —— 没人会盯着一个 tmux 的事件;要状态就 `t.sessions()`([03 §7](03-http.md))
- ❌ **window / pane** —— tmux 在这里只当共享 terminal 用,多路复用那部分不要([02 §1](02-session.md))
- ❌ **只读 / 权限分级 / `share` 链接** —— 这一层全部可读可写,锁在上层([02 §4](02-session.md))
- ❌ **接管用户已有的 tmux** —— 只探测二进制,一律 `-L` 开专属池([01 §4](01-library.md))
- ❌ **打包或编译 tmux** —— 它在契约里,自带一份就不再是"你的 tmux"([06 §2](06-dependencies.md))
- ❌ **URI / 寻址协议** —— 调用方自己算 id,那层方言留在懂它的地方([02 §2.2](02-session.md))
- ❌ **反向代理 / 自己发明的 URL 路径** —— 用 ttyd 原生的 `?arg=`([02 §3](02-session.md))
- ❌ **自研终端渲染** —— 网页那半是 ttyd 的活,不重写
- ❌ **多租户 / RBAC / 配额** —— 拿到凭据即拥有这批会话,边界靠网络和容器
- ❌ **数据库** —— 状态是几个小 JSON,真相在 `tmux ls` 里
- ❌ **跨机器编排 / 会话池 / 调度** —— 要多机就多台机器各跑一个
- ❌ **自带 HTTP 客户端** —— 远端用 `ssh box tmuxd …`,或者直接 `requests` 打那七个端点([03 §8](03-http.md))

## 里程碑

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 库** | `Tmuxd` / `Session` 两个类、ttyd 生命周期与复用、专属 socket、state 与对账 | 四行代码起一个会话并拿到能用的 URL;`kill -9` 掉进程,会话无损 |
| **M2 写入** | `send` / `send_key`、异常体系、tmux server 懒起的空列表处理 | 脚本能开会话、往里投喂、把 URL 交出去 |
| **M3 CLI** | 命令行壳(同一个库)、配置文件、退出码 | 不写 Python 也能用全部能力 |
| **M4 HTTP** | 可选的 HTTP 壳(默认不开) | 别的语言、别的容器能驱动同一批会话 |
| **M5 收编** | shellbase 切过来 | shellbase 删掉 `attach.sh`、ttyd 守护与本地终端注册表,功能不回退 |
