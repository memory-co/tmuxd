# 06 · 两个依赖,两种态度

tmuxd 依赖两个外部程序,**处理方式故意不对称**:

| | tmux | ttyd | fastapi + uvicorn |
| --- | --- | --- | --- |
| 是什么 | 外部程序 | 外部程序 | Python 包 |
| 怎么找 | **只探测 PATH** | PATH → **包里自带的** → 报错 | `pip install "tmuxd[server]"` |
| 打包进 pip 包 | **永不** | **是**,静态二进制,按架构挑 | 可选 extra,**默认不装** |
| 没有会怎样 | 构造时报错,给出安装命令 | 先退到自带的;自带的也用不了才报错 | 只影响 `tmuxd serve` / CLI |
| 谁需要 | **所有人** | **所有人** | 只有走 CLI 那条链路的人 |
| 依据 | §1 | §2 | [03 §6](03-server.md) |

前两个是 tmuxd 本身的组成(`tmuxd = tmux + ttyd`,缺一不成立);
第三个只服务于 **CLI 那条链路** —— 嵌库的人一行都用不上,所以不该替他们装
([03 §1](03-server.md))。

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

**这条判据和“谁更重要”无关。** 两个都是必需的 —— `tmuxd = tmux + ttyd`,
少一个这东西就不成立([01 §2](01-library.md)):没有 ttyd 不会降级成
“只管会话的库”,而是**根本起不来**。判据问的是另一件事:**用户会不会直接碰到它。**

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

> [07](07-install.md) 在 ① 和 ② 之间插了一级:`~/.tmuxd.json` 里 `tmuxd install`
> 记下的那条路径。它排在 PATH 前面是因为**它也是显式的** —— 你跑过 `install`。
> 但它每次都要复验,失效就**降级**回 ②,不报错(见 [07 §6](07-install.md))。

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
tmuxd/data/ttyd/          ← 平台 wheel 里只有一个二进制;git 里一个都没有(§5.3)
├── ttyd.<arch>           ← 由 CI 按清单放进对应的 wheel
├── LICENSE               ← ttyd 是 MIT,必须随包带上署名(§4.2)
└── SHA256SUMS            ← 上游的那份,裁到本 wheel 带的那一个
```

| `platform.machine()` | 用哪个 |
| --- | --- |
| `x86_64` / `amd64` | `ttyd.x86_64` |
| `armv7l` / `armv6l` / `arm` | `ttyd.arm` |
| `aarch64` / `arm64` | `ttyd.aarch64` |
| 其余 | 没有自带的可用 → 报错,给上游 releases 链接 |

> **`aarch64` 是按平台发 wheel 之后才补上的**(§5):以前全塞一个包时它被体积挤掉了,
> 现在每个 wheel 只带一个二进制,它自然进来了。这很要紧 —— 64 位 ARM
> (Graviton、树莓派 64 位系统、ARM 云主机)**跑不了 `ttyd.arm`**,
> 那是 32 位 ELF,不少 aarch64 发行版已经关掉了 32 位支持。

**只在 Linux 上生效。** 这不是保守,是上游的资产列表决定的 ——
1.7.7 那一版全部产物如下:

```
ttyd.x86_64  ttyd.i686  ttyd.aarch64  ttyd.arm  ttyd.armhf
ttyd.mips  ttyd.mipsel  ttyd.mips64  ttyd.mips64el  ttyd.s390x
ttyd.win32.exe
```

前十个是 **musl 静态 ELF**(Linux),最后一个是 Windows。
**一个 macOS 资产都没有** —— macOS 要的是 Mach-O,上游不出。

所以 macOS / BSD 上第三级**直接跳过**,报错里给 `brew install ttyd`。
这一点必须写明白,不能让 macOS 用户以为"包里带了所以应该能用"。
自己给 macOS 编一份不在考虑范围内(§7):那等于接手一条构建流水线,
而 Homebrew 已经有 ttyd 的 formula,一条命令的事。

> 要不要补 `ttyd.i686` / `ttyd.s390x` / `ttyd.mips`,取决于面向什么机群。
> **加一个架构 = 清单里加一条**([`scripts/ttyd_assets.json`](../../../scripts/ttyd_assets.json)),
> 查找那边加一行映射 —— 而且不影响别的 wheel 的体积(§5)。

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

## 5. 多平台 wheel:每个只带自己那一个

早先这一节写的是"全塞进一个 `py3-none-any` wheel,用体积换掉整个构建矩阵"。
**改成按平台发 wheel 了**,因为那笔交易的前提没成立:

- **体积不是必须付的。** 平台 wheel 里只有本机那一个二进制(~0.8 MB 压缩后),
  而不是所有架构加起来;
- **"矩阵很贵"这条不适用于我们。** 通常的矩阵贵在**要编译** —— 交叉工具链、QEMU、
  cibuildwheel。**这里一行代码都不编译**:平台之间的差别只是一个静态二进制,
  所以整套产物在**一个 runner 上**出:拿普通 wheel、塞进对应的 ttyd、改 tag、重新打包;
- 反过来还便宜了:**能覆盖的架构变多了**,加一个就是清单里加一条。

```
tmuxd-X-py3-none-any.whl                                   不带二进制  ← macOS 等
tmuxd-X-py3-none-manylinux_2_17_x86_64.musllinux_1_2_x86_64.whl    ttyd.x86_64
tmuxd-X-py3-none-manylinux_2_17_aarch64.musllinux_1_2_aarch64.whl  ttyd.aarch64
tmuxd-X-py3-none-manylinux_2_17_armv7l.musllinux_1_2_armv7l.whl    ttyd.arm
tmuxd-X.tar.gz
```

**manylinux 和 musllinux 共用一个文件。** 上游是静态 musl 构建,不依赖任一 libc,
所以同一份产物可以同时声明两个 tag,四个 Linux tag 落成两个 wheel。

### 5.1 那个 `py3-none-any` 不是残留,是兜底

pip 会挑它能用的最具体的 wheel。所以带二进制的平台拿平台 wheel,
**其余的一切 —— macOS、s390x、没想到的某块板子 —— 仍然装得上**,只是要自己装 ttyd。

只发平台 wheel 会把"你得装个 ttyd"变成"**没有给你的 wheel**",那是另一个量级的坏。

### 5.2 macOS 为什么不能从 Homebrew 拿

问过一轮:brew 有 ttyd,能不能抠出来打进 wheel?**不能,而且不该。**

```
$ curl -s https://formulae.brew.sh/api/formula/ttyd.json | …
运行时依赖: json-c, libevent, libuv, libwebsockets, openssl@3
bottle 平台: arm64_sequoia / arm64_sonoma / arm64_tahoe / sonoma / …
```

- brew 的 ttyd 是**动态链接**到那五个 brew 包的 dylib 上的,
  从 bottle 里抠出单个二进制,离开 Homebrew 那棵树就跑不起来;
- bottle 按 **macOS 版本 × 架构**分别构建,还随 brew 重建而更替 —— 那是一个会腐烂的矩阵;
- Apple Silicon 上**未签名的二进制不给执行**,而 brew 是在安装时做临时签名的。

**`brew install ttyd` 本身就是那个渠道。** 它替用户处理了 dylib、重定位和签名,
我们再分发一份等于把 brew 做得好的事做砸 —— 和 §2.2 拒绝内联 tmux 源码是同一条判据。

### 5.3 CI 就该干这个

产物在 GitHub Actions 上出([`.github/workflows/release.yml`](../../../.github/workflows/release.yml)),
打 tag 触发,发布走 **Trusted Publishing**(OIDC),仓库里不放任何 API token。

**二进制不进 git。** 每次上游发版都会换,而 git 会永远留着旧的;
值得版本化的是清单 [`scripts/ttyd_assets.json`](../../../scripts/ttyd_assets.json)
—— 版本号、每个架构的 sha256、以及架构到 wheel tag 的映射。
CI 按它下载并校验,本地想试就跑 `scripts/fetch_ttyd.py`。

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
     ttyd 是必需的 —— 没有它 tmuxd 根本起不来,不是“少个网页入口”而已

③ 显式指定的那个用不了
   TtydMissing: ttyd_bin=/opt/ttyd 无法执行(或版本过低:1.4.x < 1.6)
     显式指定的不会被自动替换 —— 要走自带的就别传 ttyd_bin
```

第 ② 条末尾那句必须写清楚,**而且不能给人留幻想**:没有 ttyd 就是起不来。
早先的稿子在这儿写过“那你还可以 `port=None` 只管会话”—— 那句话把 ttyd 说成了可选的,
和 §1 自相矛盾,**删掉了**。

正因为它是必需的,自带一份兜底才有分量:这不是锦上添花,
而是**让 `pip install tmuxd` 之后那四行真的能跑**的唯一办法。

## 7. 不做什么

- ❌ **打包 tmux 的二进制**(§2.1)
- ❌ **内联 tmux 源码在安装时编译**(§2.2)
- ❌ **默认从网络下载 ttyd** —— 每次 `pip install` 都去网上拉一个二进制、而且那是
  拿到它的唯一途径,这个安全故事比自带更差,还引入网络、校验和管理和一类新的安装期失败。

  > **拒的是"默认路径",不是"下载"这件事。** [07](07-install.md) 加了一条
  > `tmuxd install`:**你自己敲**才发生、**不是唯一途径**(自带的仍在,离线照跑)、
  > 用上游 releases 的 `SHA256SUMS` **验过**、对不上就丢弃且没有 `--force`。
  > 形状不同,所以结论也不同 —— 细节见 [07 §8](07-install.md)。
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
