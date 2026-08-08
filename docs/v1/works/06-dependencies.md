# 06 · 两个依赖,两种态度

tmuxd 依赖两个外部程序,**处理方式故意不对称**:

| | tmux | ttyd |
| --- | --- | --- |
| 怎么找 | **只探测 PATH** | PATH → **包里自带的** → 报错 |
| 打包进 pip 包 | **永不** | **是**,静态二进制,按架构挑 |
| 没有会怎样 | 构造时报错,给出安装命令 | 先退到自带的;自带的也用不了才报错 |
| 依据 | §1 | §2 |

看起来像是"一个宽松一个严格",其实是同一条判据的两面。

## 1. 判据:它在不在契约里

> **ttyd 是实现细节,tmux 是契约的一部分。**

**没有人会去和 tmuxd 的那个 ttyd 直接打交道。** 它只负责把连接 exec 到 `attach.sh`,
你连它监听在哪个端口都不需要知道([01 §2](01-library.md))。它是可替换的零件,
**vendoring 它在概念上不欠任何人东西**。

tmux 不是。这个项目开篇那句承诺就是"**tmuxd 的 session 就是 tmux 的 session,不是黑盒**",
`tmux -L tmuxd ls` 是写进文档的逃生舱([01 §4](01-library.md)),
"你自己的 `tmux ls` 一个不多一个不少"是卖点。这些全都建立在
**"tmuxd 的 tmux 和你的 tmux 是同一个东西"**之上。

一旦自带一份 tmux,它仍然是 tmux,但**不再是你的 tmux** —— 契约窄了一圈,
而窄掉的正是开篇那句。

所以不是"哪个更重要"(真论重要性,tmux 更重要 —— 没有它什么都不成立,
而没有 ttyd 你还有 `port=None`),而是**哪个在契约里**。

### 1.1 事实基础

这条判据不是纯推演,是量出来的(2026-08-08,Debian bookworm):

```console
$ apt-cache policy tmux ttyd
tmux:  Candidate: 3.3a-3          ← 一条 apt install 就有
ttyd:  Candidate: (none)          ← 仓库里没有,得自己去 GitHub 抓
```

**难装的恰恰是那个不在契约里的。** 这个巧合让整件事变得干净:
该 vendoring 的那个正好是需要 vendoring 的那个。

## 2. tmux:只探测,没有就报错

构造时做两件事,**都不启动任何 tmux 进程**:

```
which tmux    →  tmux_bin= / TMUXD_TMUX_BIN 可覆盖
tmux -V       →  低于 3.0 报 TmuxMissing(要用 window-size,2.9 才有)
```

找不到就**在构造时报错**,不拖到有人打开浏览器才炸。报错要给出**这台机器上的那条命令**:

```
TmuxMissing: tmux not found in PATH

  Debian/Ubuntu   apt install tmux
  RHEL/Fedora     dnf install tmux
  macOS           brew install tmux
  Alpine          apk add tmux

装好了但不在 PATH 里,用 Tmuxd(tmux_bin="/path/to/tmux") 或 TMUXD_TMUX_BIN 指过去。
```

### 2.1 为什么不打包 tmux 的二进制

除了 §1 那条判据,还有三个具体代价:

**① 逃生舱降级成"通常能用"。** tmux 的 client 和 server 必须协议版本兼容
(两个二进制里都有 `protocol version mismatch (client %d, server %u)` 这句)。
实测 3.3a ↔ 3.5a 是互通的,所以**大多数时候没事** —— 但机制在那儿,
哪天上游升了协议版本而你自带的和用户系统里的分处两边,
`tmux -L tmuxd attach` 就直接拒绝。**一个平时好用、偏偏在你最需要排障时失效的逃生舱,
比没有更糟。**

**② tmuxd 升级可能变成杀会话事件。** 这打在项目命门上:现在的承诺是
"门面挂了屋子还在,升级也不影响会话"。自带 tmux 之后,`pip install -U tmuxd`
换掉的是客户端二进制,而跑着的 server 还是旧的 —— 同样只在协议跨版本时出事,
但出事就是**所有会话一次性失联**。要防住得写"发现有 server 在跑就继续用旧二进制"
的逻辑,那是一套真正的版本管理,不是打包一个文件。

**③ 静态 tmux 比静态 ttyd 难一个量级。** ttyd 上游直接发布静态 musl 二进制
(1.2MB,`not a dynamic executable`);tmux 要 `libevent` + `libtinfo`,
而 `libtinfo` 意味着**要读 terminfo 数据库** —— 静态链接 ncurses 不等于把
`/usr/share/terminfo` 带上,`tmux-256color` 在不少系统上根本不存在。
而且 **tmux 上游不发布官方静态二进制**,得自己为每个平台构建和维护。

### 2.2 为什么也不在安装时编译源码

"tmux 是 C 写的,把源码放进来 pip install 时编译"——比打包二进制更差:

**实测:一台装了 `cc` / `gcc` / `make` / `pkg-config` / `libevent` 头文件的开发机,
依然编不动** —— 缺 `ncurses.h`,还缺 `bison`。而 `python:3.11-slim` 里连 `gcc` 都没有。
**连最友好的情况都不成立。**

代价清单:

- **拿一条命令换四条命令加一次构建**,而省掉的还是那个 `apt install` 就有的依赖;
- **失败信息变成一堵编译器输出墙**,而且发生在**安装期** —— CI 和 Dockerfile 里
  这会变成构建失败,比运行时一句"没装 tmux"难查得多;
- **契约问题一个没解决**(编出来的还是私有 tmux),还**新增一个**:
  同一个 tmuxd 版本在两台机器上会编出行为不同的 tmux,取决于安装那一刻是哪个
  libevent、哪个 ncurses。三个选项里只有它连"机器间可复现"都丢了;
- **源码内联不替代平台矩阵**,它只是矩阵下面的长尾兜底(pyzmq 那种成熟形态是
  "wheel 为主、源码兜底")。只做源码 = 让 100% 的人走本该只有 1% 的人走的那条路。

正当的源码内联理由有两条 —— **你需要打补丁的版本**,或**上游没有二进制分发** ——
tmuxd 一条都不占:它用的全是 stock tmux 的公开命令行,而 tmux 是被打包得最好的软件之一。

想让用户少装东西,对口的答案是**把 tmuxd 送进发行版和 Homebrew**,
让包管理器去解依赖 —— 而不是在 pip 包里重造一个包管理器。

## 3. ttyd:三级查找

```
① Tmuxd(ttyd_bin=…) / TMUXD_TTYD_BIN     显式指定,不合格直接报错,不偷偷换
②   PATH 里的 ttyd                        合格就用它
③   包里自带的 ttyd.<arch>                 前两级没有或不合格时兜底
     ↓ 都不行
   TtydMissing,说清检测到什么架构、包里带了什么、怎么自己装
```

### 3.1 为什么 PATH 优先,自带兜底

反过来(自带优先)能拿到版本一致性,但代价太大:

- **系统装的那个是能被 `apt upgrade` 修的**,自带的不能(§4)。
  用户的发行版打了 ttyd 的安全补丁,tmuxd 却绕过它去跑一个自己带的旧版 —— 不可接受;
- 用户显式装了 ttyd,通常是**有原因的**(要 SSL、要某个新特性、要自己编的版本);
- 自带的定位是**兜底**,不是**接管**。

### 3.2 但 PATH 优先要求"合格性检查"

这是 PATH 优先能成立的前提,也是当前实现的一个真实缺口 ——
**tmux 那边低于 3.0 会拒绝启动,ttyd 这边连版本都没看过**,只把 `--version` 读出来显示。

tmuxd 用到 ttyd 的 `-p` / `-i` / `-a` / `-W` / `-c` 和 `?arg=` 语义。一个太老的 ttyd
会在**有人打开浏览器时**以很难懂的方式失败,而不是在构造时。所以:

```
ttyd --version   →  低于 1.6 视为不合格
```

**不合格不等于报错** —— 它触发降级:PATH 里那个不合格,就退到自带的,
并记一条 warning 说明为什么。只有当自带的也用不上时才报错。
这条让"系统上有个老 ttyd"从一种故障变成一次无感的降级。

### 3.3 自带哪些

文件名**沿用上游 release 的资产名**,`platform.machine()` 直接当键查:

```
tmuxd/data/ttyd/
├── ttyd.x86_64      ← 绝大多数服务器和桌面
├── ttyd.arm         ← 32 位 ARM
└── LICENSE          ← ttyd 是 MIT,必须随包带上署名(§4.2)
```

| `platform.machine()` | 用哪个 |
| --- | --- |
| `x86_64` / `amd64` | `ttyd.x86_64` |
| `armv7l` / `armv6l` / `arm` | `ttyd.arm` |
| 其余(含 `aarch64` / `arm64`) | 没有自带的可用 → 报错,给上游 releases 链接 |

> **`aarch64` 不在自带清单里,这是一处需要知情的取舍。**
> 64 位 ARM(Graviton、树莓派 64 位系统、ARM 云主机)**跑不了 `ttyd.arm`** ——
> 那是 32 位 ELF,只有在开了 `CONFIG_COMPAT` 的内核上才可能执行,
> 而不少发行版的 aarch64 版本已经关掉了 32 位支持。所以这些机器会落到"报错 + 自己装"。
> 要覆盖它们,把上游的 `ttyd.aarch64` 放进这个目录就行 —— **查找机制一行都不用改。**

**只在 Linux 上生效。** 上游的静态二进制是 musl 构建的 ELF,macOS / BSD 跑不了 ——
那些平台上第三级直接跳过,报错里给 `brew install ttyd`。这一点必须写明白,
不能让 macOS 用户以为"包里带了所以应该能用"。

> 要不要补 `ttyd.aarch64` / `ttyd.i686` / `ttyd.mips`,取决于面向什么机群。
> 每多一个约 +1.2MB,而查找机制不用改一行 —— **加架构就是往目录里多放一个文件。**

### 3.4 可执行位:wheel 里保不住

package data 经过 wheel 打包解压后**可执行位通常会丢**。所以自带的二进制不能就地执行,
要走和 `attach.sh` 同一套路子([01 §9](01-library.md)):
**复制到状态目录 `~/.tmuxd/<实例>/bin/ttyd`,`chmod 0700`,执行那一份。**

顺带解决另外两件事:site-packages 可能是只读的(容器、系统 Python),
以及多个实例共用同一份源文件时不必互相干扰。

复制只在**目标不存在或源文件更新**时发生,和 `attach.sh` 的判据一致。

## 4. 自带的代价,以及怎么还

**必须写在明处:自带一个静态二进制,意味着把它的安全生命周期揽了过来。**

```
$ strings ttyd | grep -i 'mbed tls'
Mbed TLS 2.28.5
```

TLS 栈焊死在里面,`apt upgrade` 永远修不到它。发行版打包体系存在的理由就是解决这个,
而 vendoring 是主动退出这套体系。

### 4.1 三条还债的方式

1. **PATH 优先**(§3.1)—— 用户的发行版装了 ttyd,就用发行版那个。
   **自带的只服务于"系统里根本没有"的情况**,这已经把暴露面砍掉了一大半;
2. **`ttyd_bin=` 永远是逃生口** —— 用户想钉死某个版本,一行就够,不用等我们发版;
3. **跟随上游发版**:ttyd 出新版本(尤其是安全相关的)就刷新自带的二进制并发一个 patch 版本。
   **这是一条运维承诺,写进文档就得做到** —— 做不到的话,诚实的做法是把自带这条路砍掉。

### 4.2 许可与署名

ttyd 是 MIT,再分发合法,但**必须随包带上它的 LICENSE 和版权声明**。
放在 `tmuxd/data/ttyd/LICENSE`,并在项目 README 与 PyPI 页面注明
"本包含有 ttyd 的预编译二进制(MIT),来自 <上游 release 链接>"。
自带的版本号也应该出现在 `info()["ttyd"]` 里,好让人知道自己在跑什么。

## 5. 一个意外的好处:wheel 仍然是 `py3-none-any`

一般"打包二进制"的第一反应是:包从此platform-specific,每次发版变成构建矩阵。
**这个方案不是。** 因为几个架构的二进制**同时**放在包里、**运行时**才挑,
所以仍然是一个 `py3-none-any` wheel:

| | 多平台 wheel(常规做法) | 全塞进一个 wheel(本方案) |
| --- | --- | --- |
| 发版 | 每个平台一个 wheel,要 CI 矩阵 | **一个 wheel,一次上传** |
| 冷门平台 | 落到 sdist,行为不同 | 行为一致(都是"没自带的就报错") |
| 体积 | 每个 wheel 小 | **+2.4MB(两个);每多一个架构约 +1.2MB** |
| 用不到的人 | 不下载 | 也下载了(`port=None` 的用户白背) |

**用体积换掉整个构建矩阵。** 对一个 40KB 的纯 Python 包来说 2.4MB 不算小,
但比起"维护 4 条 CI 流水线 + 两种安装路径的行为差异",这笔交易划算 ——
尤其因为**没有矩阵就没有"某个平台忘了发"这种事故**。

## 6. 三种失败,三段文案

报错的价值全在**下一步该干什么**说清楚了没有:

```
① 没有 tmux
   TmuxMissing: tmux not found in PATH
     apt install tmux  /  brew install tmux  /  dnf install tmux
     已装但不在 PATH:Tmuxd(tmux_bin=…) 或 TMUXD_TMUX_BIN

② 没有 ttyd,而且这个架构没有自带的
   TtydMissing: ttyd not found, and no bundled build for linux/aarch64
     包里自带:x86_64, arm
     自己装:https://github.com/tsl0922/ttyd/releases
     或者不要网页入口:Tmuxd(port=None) 照样能管会话、往里敲

③ 显式指定的那个用不了
   TtydMissing: ttyd_bin=/opt/ttyd 无法执行(或版本过低:1.4.x < 1.6)
     显式指定的不会被自动替换 —— 要走自带的就别传 ttyd_bin
```

第 ② 条末尾那句是**必须有**的:`port=None` 是合法形态([01 §2](01-library.md)),
一个只想用库管会话、投喂指令的人,不该因为没有 ttyd 就被挡在门外。

## 7. 不做什么

- ❌ **打包 tmux 的二进制**(§2.1)
- ❌ **内联 tmux 源码在安装时编译**(§2.2)
- ❌ **运行时从网络下载 ttyd** —— "从互联网下载并执行一个二进制"的安全故事
  比自带更差,还引入网络、校验和管理和一类新的安装期失败
- ❌ **自带优先于 PATH**(§3.1)
- ❌ **自带 macOS 的 ttyd** —— 上游没有静态包,自己编就等于接手一条构建流水线;
  macOS 上 `brew install ttyd` 一条命令就有
- ❌ **为 ttyd 做版本钉死** —— 只设下限,不设上限。上游改坏了兼容性应该由
  合格性检查(§3.2)在构造时暴露,不是靠猜一个上界

## 8. 影响清单

| 改哪 | 改什么 |
| --- | --- |
| `tmuxd/ttyd.py` | `find_binary` 变成三级查找;加版本下限与合格性检查;加复制+chmod |
| `tmuxd/tmux.py` | `TmuxMissing` 的文案加上按平台的安装命令 |
| `tmuxd/data/ttyd/` | 新目录:三个二进制 + LICENSE |
| `MANIFEST.in` / `package-data` | 已经是 `data/*`,需要改成递归包含子目录 |
| `tmuxd/core.py` | `info()["ttyd"]` 加 `source` 字段(`path` / `bundled`) |
| `tests/` | 新场景 `finding_the_binaries/`:三级查找各一条、架构不匹配、可执行位、显式指定不被替换 |
| `docs/v1/sdk/tmuxd.md` | 构造参数表里 `ttyd_bin` 的说明 |
| `docs/v1/cli/README.md` | 开头那句"要网页入口还需要 ttyd"要改 |
| 根 `README.md` / PyPI 页面 | 加 ttyd 的署名与许可声明(§4.2) |
