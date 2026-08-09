# tmuxd

[![PyPI](https://img.shields.io/pypi/v/tmuxd)](https://pypi.org/project/tmuxd/)
[![Python](https://img.shields.io/pypi/pyversions/tmuxd)](https://pypi.org/project/tmuxd/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**tmux + ttyd 做成一个 Python 库:活得比连接久、程序能往里敲、人能用浏览器打开的终端。**

**简体中文** · [English](README.en.md) · [更新日志](CHANGELOG.md)

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
- **不分权限档。** 全部可读可写。拿到 token 就是拿到这台机器的 shell,
  在这一层加个只读开关只是**假的边界**。要锁,往有身份的上层去锁。

## 两条链路

|  | 库 | CLI |
| --- | --- | --- |
| 谁持有实例 | 你的进程 | `tmuxd serve` |
| 要 server 吗 | **不要** | **要** |
| 装什么 | `pip install tmuxd` | `pip install "tmuxd[server]"` |
| 开几个口 | 只有 ttyd | ttyd + 管控口 |
| 怎么暴露 | 把 `tmuxd.server.router()` 挂进你已经在跑的 app | 管控口(随机) |

**两个口,两拨用户。** 一个是 ttyd,**给人的** —— `s.url` 直接发给同事就行;
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
pytest                              # 约 198 个用例,约 50 秒
pytest tests/exact_targeting -v     # 单个场景
```

测试跑的是**真的 tmux、真的 ttyd、真的 uvicorn** —— 这个项目的全部价值就在它和这几个
程序的交界处,把它们换成假的等于什么都没测。每个用例拿到独立的 tmux socket,
所以跑测试不会打扰你开着的 tmux。用例是[按场景组织](tests/README.md)的,不按代码模块。

## 许可

Apache-2.0,见 [LICENSE](LICENSE)。

tmuxd 把 [ttyd](https://github.com/tsl0922/ttyd)(MIT)和
[tmux](https://github.com/tmux/tmux)(ISC)当外部程序驱动,**既不打包也不改动**它们。
