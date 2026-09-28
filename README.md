# tmuxd

[![PyPI](https://img.shields.io/pypi/v/tmuxd)](https://pypi.org/project/tmuxd/)
[![Python](https://img.shields.io/pypi/pyversions/tmuxd)](https://pypi.org/project/tmuxd/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**tmux + ttyd 做成一个 Python 库:活得比连接久、程序能往里敲、人能用浏览器打开的终端。**

**简体中文** · [English](README.en.md) · [更新日志](CHANGELOG.md)

**tmuxd 是一个 `*muxd` 组件** —— 一扇 HTTP 上的窗给人,一个 Python 把手给程序。
这一族的规范定在 [shellbase](https://github.com/memory-co/shellbase):
[new-interface](https://github.com/memory-co/shellbase/blob/main/docs/v1/new-interface.md)(为什么是这个形状) · [muxd-spec](https://github.com/memory-co/shellbase/blob/main/docs/v1/muxd-spec.md)(算不算一个组件) · 姊妹项目 [webmuxd](https://github.com/memory-co/webmuxd)(浏览器那一块)

---

`ttyd tmux new -A -s work` 这条命令大家都写过:tmux 让会话活着,ttyd 让你在浏览器里看见。
能用,但拼出来的东西**没有把手** —— 这个会话是谁开的、开在哪个目录、还活着没有?
想从外面投喂一条指令?只能 ssh 进去敲 `tmux send-keys`。

tmuxd 就是把这条命令做成一个**你 import 进来的东西**。

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # 这行之后:ttyd 起来了,tmux 还没有
s = t.session(id="id5", cwd="~/proj", cmd="claude")
s.send("把测试跑一遍", enter=True)
print(s.url)                              # http://127.0.0.1:12345/?arg=id5
```

那个 URL 发给谁,谁的浏览器就**在这个终端里** —— 看得见,也能直接接手敲。
**程序把活派下去,人看着它跑。**

嵌进一个 Web 后端时,这扇窗**不用另开端口,也不用另一套密码**:ttyd 听在一个 unix socket 上,
`t.asgi()` 把它挂到你自己的路由下,谁能进哪扇窗由你的登录说了算 —— 见[窗开在哪](#窗开在哪两扇门)。

## 快速开始

机器上要有 `tmux`(≥ 3.0)和 `ttyd` —— 见[依赖](#依赖)。

### 当库用 —— 不需要 server

```bash
pip install tmuxd          # 零运行时依赖
```

```python
from tmuxd import Tmuxd

with Tmuxd(port=12345, token="changeme") as t:
    s = t.session(id="deploy", cwd="/srv/app", cmd="./deploy.sh")
    print("在这看:", s.url)
# ttyd 跟着你的进程走了,部署还在跑
```

实例在你自己的进程里,没有别的东西要起。

### 嵌进 Web 后端 —— 窗走你的端口、你的登录

```bash
pip install "tmuxd[asgi]"       # + websockets;你的 ASGI server 要带 WebSocket(如 uvicorn[standard])
```

```python
from fastapi import FastAPI
from tmuxd import Tmuxd

app = FastAPI()
t = Tmuxd(base_path="/tty")          # 不给端口:ttyd 听在 ~/.tmuxd/tmuxd/ttyd.sock(0600)

def gate(scope):
    """每个进窗的请求都问一次:页面、/token、/ws。返回 True 放行,False 拒绝。"""
    return my_login_allows(scope)    # 你自己的登录态,比如从 cookie 里认人

app.mount("/tty", t.asgi(authorize=gate))

@app.post("/api/work")
def start_work():
    s = t.session(id="job-1", cmd="claude")
    return {"window": s.url}         # "/tty/?arg=job-1" —— 同源相对地址,前端直接塞进 iframe
```

只有你的 app 一个端口。窗和你的 API 走同一扇门 —— 详见下一节。

### 用命令行 —— 需要一个 server

```bash
pip install "tmuxd[server]"     # + fastapi + uvicorn
tmuxd start                     # 两个口都随机挑,记进 ~/.tmuxd/daemon.json
```

```bash
tmuxd new  -s work -c ~/proj
tmuxd send -s work "npm test" --enter
tmuxd url  -s work -o           # 顺手用浏览器打开
tmuxd ls
tmuxd stop                      # 停的是 server,会话照跑
```

一条 CLI 命令活几十毫秒就退出,持不住 ttyd 也持不住会话状态,
所以它只能**去问一个持得住的东西**。CLI 和 server 因此是一起装的
—— [为什么](docs/v1/works/03-server.md)。

## 窗开在哪:两扇门

ttyd 那一页(人看的那扇窗)可以开在两种地方,**由构造参数决定**:

|  | ttyd 自己一个端口(TCP) | 挂进你的 app(unix socket) |
| --- | --- | --- |
| 怎么写 | `Tmuxd(port=12345, token=…)` | `Tmuxd(base_path="/tty")` + `app.mount("/tty", t.asgi(authorize=gate))` |
| ttyd 听在 | `127.0.0.1:12345`(或你给的 `bind`) | `<state_dir>/<socket>/ttyd.sock`,权限 0600 |
| `s.url` | `http://127.0.0.1:12345/?arg=id5` | `/tty/?arg=id5`(相对,同源) |
| 谁能进 | 知道 token 的人(basic auth,全员一个) | 你的 `gate(scope)` 说了算 —— 按人、按窗 |
| 要开几个口 | 多一个,防火墙要放 | 不多开,就是你 app 那一个 |
| 适合 | 脚本、单机、CLI(`tmuxd start` 永远是这种) | 已经有登录的 Web 后端 |

**怎么选的:给了 `port=`(或 `TMUXD_PORT`、`listen="tcp"`)就是 TCP;什么都不给就是 socket。**

> ⚠️ **这是一处不兼容改动。** 2.1.0 里 `Tmuxd()` 不给端口会挑一个随机 TCP 口;
> 现在它开的是 socket,`s.url` 变成相对地址,不 mount `t.asgi()` 就没人进得去。
> 想要原来的行为,写 `Tmuxd(listen="tcp")`。见[更新日志](CHANGELOG.md)。

**为什么要有第二种。** ttyd 自己占一个口,窗就在你的门外:多一个端口要开防火墙;
多一套认证 —— 一个明文、全员共用的 token,跟你的登录毫无关系,拿到它的人绕过登录直接进 shell,
你的登出、改密码也管不到它。挂进你的 app 之后,窗和 API 走同一个端口、同一扇门。

**门怎么写。** `authorize(scope)` 拿到原始 ASGI scope(path、query、headers、cookie 都在),同步异步都行:

| 返回 | 结果 |
| --- | --- |
| 假 | HTTP 403;WebSocket 握手时关掉(1008) |
| `True` | 放行 |
| `[(name, value), …]` | 放行,并把这些头加到响应上 —— 比如核过一张票后种 cookie |

**不传 `authorize` 就是全放行。** socket 的 0600 只挡住本机别的用户直连 ttyd;
挂上 `t.asgi()` 之后,**谁能连到你 app 的端口,谁就能打开 `/tty/?arg=<任意 id>` 拿到 shell**
—— 比 TCP 模式的 basic auth 还松。所以只在两种情况下省掉它:
宿主只听 `127.0.0.1`、自己一个人用;或者前面已经有一层统一认证(Cloudflare Access、
oauth2-proxy、VPN)挡住了所有请求。宿主自己的 `/api` 鉴权中间件**罩不住**这里 ——
iframe 和 WebSocket 带不了 `Authorization` 头。

iframe 和浏览器的 WebSocket 带不了 `Authorization` 头,所以常见做法是**票据换 cookie**:
给窗地址附一张短期一次性票,页面请求上核票、种一个 `Path=/tty` 的 HttpOnly cookie,
之后的 `/ws` 凭 cookie 进,**并核对 `?arg=` 就是票上那个会话** —— 一张票只开一扇窗。

几件要知道的事:

- `base_path` 要和 mount 的前缀一致(`"/tty"` 配 `"/tty"`);
- 转发只搬字节,不解析 ttyd 的协议;**ttyd 的生死仍归 `Tmuxd`**(构造时起、`close()` 收),ttyd 没了回 502;
- 宿主保持**单 worker**;前面再有代理时,WebSocket 的空闲超时要放宽;
- 窗和你的页面同源,ttyd 的前端 JS 读得到你页面的 localStorage。要更严就把 `/tty` 放到独立子域。

设计和取舍:[works/08 · 一扇门](docs/v1/works/08-one-door.md)。

## 它和别的东西不一样在哪

**这份设计是一路减出来的。** 减掉的每一样,都比留下的更能说明它是什么。

- **一个会话就是一个终端。** 没有 window,没有 pane —— tmux 的多路复用那一半不用。
  要多个终端?多开几个会话。
- **只写,不读。** 没有 `capture`、`run`、输出流、录制、事件流。读终端内容这件事,
  要么归**人**(打开那个 URL,ttyd 已经做得比任何 API 都好),要么归 **ssh**
  (干净的 stdout、真的退出码、二进制安全)。留下的是那个**它们俩都办不了**的写入动作。
- **门面短命,屋子长命。** ttyd 是你进程的子进程,tmux server 谁的都不是。
  `kill -9` 掉你的程序,会话照跑,连启动目录和命令都还记得。
- **它不碰你自己的 tmux。** 只探测二进制,会话池永远开在专属的 `tmux -L tmuxd` 上,
  你的 `tmux ls` 一个不多一个不少。
- **人和程序敲的是同一个终端。** 这不是我们做的功能,是 tmux 白送的 ——
  也正因为白送,整个设计才围着它转。
- **不分权限档。** 全部可读可写。进得了窗就是拿到这台机器的 shell,
  在这一层加个只读开关只是**假的边界**。要锁,往有身份的上层去锁 ——
  挂进你的 app 时,那个上层就是你的 `gate(scope)`。

## 两条链路

|  | 库(挂进你的 app) | 库(ttyd 自己一个口) | CLI |
| --- | --- | --- | --- |
| 谁持有实例 | 你的进程 | 你的进程 | `tmuxd serve` |
| 要 server 吗 | **不要**(用你的) | **不要** | **要** |
| 装什么 | `pip install "tmuxd[asgi]"` | `pip install tmuxd` | `pip install "tmuxd[server]"` |
| 开几个口 | **零个**(ttyd 在 socket 上) | ttyd | ttyd + 管控口 |
| 窗怎么暴露 | `app.mount("/tty", t.asgi(…))` | ttyd 的端口 | ttyd 的端口 |
| 程序怎么调 | 直接调库;要给别人 HTTP 就挂 `tmuxd.server.router()` | 同左 | 管控口(随机) |

**CLI 的两个口,两拨用户。** 一个是 ttyd,**给人的** —— `s.url` 直接发给同事就行;
另一个是管控口,**给程序的** —— JSON 进 JSON 出,七个端点。
**两个都是启动时随便挑的空闲口** —— 7681 正是 ttyd 自己的默认端口,也就最可能被你
自己那个 ttyd 占着,固定用它等于主动找架吵。端口记在 `~/.tmuxd/daemon.json` 里,
`tmuxd` 的每条命令都从那儿读,你不用记。
要驱动别的机器就 `ssh box tmuxd …`,而不是把口开到网上。

## 依赖

| | | |
| --- | --- | --- |
| **tmux** | ≥ 3.0 | `apt install tmux` · `brew install tmux` · `dnf install tmux` |
| **ttyd** | ≥ 1.6 | **Linux wheel 里自带** · macOS:`brew install ttyd` |
| **Python** | ≥ 3.9 | |
| **系统** | Linux、macOS | |

**Linux 上 `pip install` 就够了。** wheel 里带着对应架构的上游 ttyd
(x86_64 / aarch64 / armv7l,glibc 和 musl 通用 —— 上游是静态链接)。
PATH 上已经有 ttyd 的话仍然优先用系统那份:**它能被 `apt upgrade` 修,自带的只能等我们发版**。

**macOS 要自己装** —— `brew install ttyd`。上游从来没出过 Darwin 产物
(往回查到 1.7.3,每一版都是十个 musl ELF 加一个 win32.exe),而 Homebrew 那份
动态链接着它自己的五个包,再分发一遍等于把 brew 做得好的事做砸。
macOS 装的是 `py3-none-any` 那个 wheel —— 哪都装得上,只是要求 PATH 上有 ttyd。

**不支持 Windows** —— tmux 没有 Windows 版,而 tmuxd 顶层 `import fcntl`。

**可选的 extra:** `tmuxd[asgi]` 加 `websockets`(`t.asgi()` 连 ttyd 那一侧要用);
`tmuxd[server]` 加 `fastapi` + `uvicorn`(CLI 和控制 API 要用)。`import tmuxd` 本身永远零依赖。

**环境不齐的话** —— 冷门架构、没装 tmux、或者想要比自带更新的 ttyd —— 有一条可选的
[`tmuxd install`](docs/v1/cli/install.md):从上游下一份**验过校验和**的 ttyd
(网络不通就退回包里自带的),tmux 则告诉你这台机器上确切该敲哪条命令;
装完把两条路径记进 `~/.tmuxd.json`,下次库和 CLI 都自动读到。
**环境齐的机器一次都不用敲它**,`Tmuxd()` 也不会检查你跑没跑过。

tmuxd 永远不会接管你自己在用的那个 tmux:它在专属 socket 上开自己的池,
所以 `tmux ls` 显示的还是原来那些。

## 开发

```bash
pip install -e ".[dev]"
pytest                              # 约 234 个用例,约 70 秒
pytest tests/exact_targeting -v     # 单个场景
```

测试跑的是**真的 tmux、真的 ttyd、真的 uvicorn** —— 这个项目的全部价值就在它和这几个
程序的交界处,把它们换成假的等于什么都没测。每个用例拿到独立的 tmux socket,
所以跑测试不会打扰你开着的 tmux。用例是[按场景组织](tests/README.md)的,不按代码模块。

## 许可

Apache-2.0,见 [LICENSE](LICENSE)。

tmuxd 把 [ttyd](https://github.com/tsl0922/ttyd)(MIT)和
[tmux](https://github.com/tmux/tmux)(ISC)当外部程序驱动,**既不打包也不改动**它们。
