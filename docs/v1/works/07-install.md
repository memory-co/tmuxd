# 07 · `tmuxd install`

## 1. 它补的是哪三处缺口

[06](06-dependencies.md) 定下的是**运行时怎么找二进制**。找不到的时候怎么办,那份文档给的
答案是"报错,告诉你怎么装"。这条命令把最后这一步也接上,而它真正解决的是三个具体缺口:

| 缺口 | 现在会怎样 | `install` 之后 |
| --- | --- | --- |
| **冷门架构**(s390x / i686 / mips…) | 没有对应的 wheel,自带的也没有 → 报错让你自己去 GitHub 抓 | 按清单从上游下载,校验和验过 |
| **tmux 根本不自带**([06 §2](06-dependencies.md)) | 报错,给一串按发行版分的命令 | 检测 + 代劳(有权限时),否则给准确的那一条 |
| **自带的会陈旧**([06 §4](06-dependencies.md)) | 只能等 tmuxd 发新版才更新 | **装的时候拿当前版本** —— 这条是"网络优先"的真正理由(§3) |

**不是给已经装好的人用的。** Linux 上装了平台 wheel、机器上有 tmux 的人,
`pip install` 之后直接就能跑,这条命令一次都不用敲。

## 2. 两个二进制,两种现实

`tmuxd install` 一条命令处理两个依赖,但它们**能被处理的程度完全不同**,
文档必须先把这一点说白,否则用户会以为它俩一样。

| | tmux | ttyd |
| --- | --- | --- |
| 上游有预编译产物吗 | **没有**,只有源码 tarball(实测 3.7b:资产只有 `tmux-3.7b.tar.gz`) | **有**,静态 musl,按架构 |
| 装它要 root 吗 | **要** —— 只能走系统包管理器 | **不要** —— 一个文件放进 `~/.tmuxd/bin/` |
| `install` 能做到 | 检测版本;**有权限时**代跑包管理器,否则给出那一条命令 | 全自动:下载、校验、落盘、记下来 |
| 为什么不下一个静态 tmux | 协议版本、逃生舱、升级即杀会话 —— 三条代价见 [06 §2.1](06-dependencies.md) | — |

> **不代跑 `sudo`。** 一个 `pip install` 来的库自作主张提权,是不能接受的。
> 判据很简单:**已经是 root 就直接装**(容器里是常态,而容器正是这东西的主场),
> 不是 root 就**把那条命令打出来给你自己跑**。
>
> ```console
> $ tmuxd install
> tmux   ✗ 没找到
>        这台机器上是: sudo apt install tmux
>        (装好后再跑一次 tmuxd install)
> ttyd   ✓ 1.7.7  →  ~/.tmuxd/bin/ttyd(下载,sha256 已校验)
> ```

## 3. 优先级:网络 > 本地自带

**这条乍看反直觉** —— 包里已经有一份校验过的二进制了,为什么还要去网上下?

因为它们不是同一个东西:

> **自带的那份是"发 wheel 那天的 ttyd",网络上的是"现在的 ttyd"。**

[06 §4](06-dependencies.md) 写过自带二进制的代价:Mbed TLS 焊死在里面,
`apt upgrade` 永远修不到,**只能等我们发版**。`install` 走网络,正好绕开这条 ——
它让用户不必等 tmuxd 发版就能拿到上游的修复。

所以顺序是:

```
① 网络:按清单从上游 releases 下载(校验和验过)
②   ↓ 网络不通 / 被墙 / 离线机器
③ 本地:包里自带的那份(如果这个架构有)
④   ↓ 都没有
⑤ 报错,给出手装的准确步骤
```

**降级要说出来**,不能静悄悄:

```console
$ tmuxd install
ttyd   ⚠ 下载失败(连不上 github.com),改用包里自带的 1.7.7
       网络恢复后可以 tmuxd install --refresh 换成最新的
```

### 3.1 这和 §06 的"运行时查找顺序"不冲突

两件事,别混:

| | 问的问题 | 顺序 |
| --- | --- | --- |
| **[06 §3](06-dependencies.md) 运行时查找** | 现在该跑哪个二进制 | 显式 → PATH → 自带 |
| **本文 §3 安装来源** | `install` 该从哪弄一份来 | 网络 → 自带 |

`install` 装完会把路径写进配置(§4),而配置属于**显式**那一级 —— 所以装过之后,
运行时就用装好的那份。两条顺序各管各的,合起来见 §6。

## 4. 装完写到哪:`~/.tmuxd.conf`

装完要记下来,否则下次还得重找。写进已有的那个配置文件,沿用 `set -g` 写法:

```conf
# ~/.tmuxd.conf
set -g port          7681
set -g control-port  7682

# --- managed by tmuxd install (2026-08-08T12:00:00Z) ---
set -g tmux-bin      /usr/bin/tmux
set -g ttyd-bin      /home/me/.tmuxd/bin/ttyd
# --- end managed ---
```

### 4.1 只碰自己那两行

这是这一节最要紧的约束:**这个文件可能是人手写的**,里面有注释、有排版、有别的设置。
所以 `install`:

- **只写 `tmux-bin` 和 `ttyd-bin` 两个键**,别的一个字不动;
- 用一对 `--- managed by tmuxd install ---` 标记把它们围起来,**重复运行只替换块内**;
- 文件不存在就新建;存在但没有那个块,就**追加**在末尾;
- **永远不重排、不删注释、不"格式化"别人的文件。**

> 更稳的做法本来是写一个单独的机器文件(比如 `~/.tmuxd/toolchain.json`),
> 人写的和机器写的彻底分开。这里选了同一个文件,因为**一处配置比两处好找** ——
> 代价就是上面那几条纪律,它们是硬要求,不是建议。

### 4.2 `--print` 不写盘

有人不想让程序碰他的配置文件(dotfiles 进了 git 的人尤其)。所以:

```console
$ tmuxd install --print
set -g tmux-bin      /usr/bin/tmux
set -g ttyd-bin      /home/me/.tmuxd/bin/ttyd
```

自己贴到哪都行。**一个会改你 home 目录文件的命令,必须给出"只告诉我该写什么"的出口。**

## 5. 谁读这个文件:库只读工具链那两个键

用户要的是"下次不管 lib 还是 server 都自动读到"。但**让一个库去读用户 home 目录下的
配置文件,是会出事的** —— 嵌进别人 Web 应用里的库,行为不该被一个它不知道的文件改变。

所以划一条线:

| 键 | CLI 读吗 | **库读吗** | 为什么 |
| --- | --- | --- | --- |
| `tmux-bin` / `ttyd-bin` | ✅ | **✅** | 这是**机器事实**——那两个程序在这台机器的哪儿。谁来问答案都一样 |
| `port` / `control-port` / `bind` / `token` | ✅ | **❌** | 这是**行为**。嵌进别人应用里的实例,端口和 token 该由那个应用决定,不该被 home 里的文件改掉 |
| `history-limit` / `state-dir` / `open-cmd` | ✅ | ❌ | 同上 |

**机器事实可以从配置来,行为必须从调用方来。** 这条线让"install 之后自动生效"成立,
又不至于让 `Tmuxd(port=8080)` 的行为取决于某个人的 dotfile。

## 6. 合起来的完整顺序

装过之后,两个二进制的查找是这样(从上往下,先命中先用):

```
tmux                                    ttyd
① Tmuxd(tmux_bin=…)                     ① Tmuxd(ttyd_bin=…)
② TMUXD_TMUX_BIN                        ② TMUXD_TTYD_BIN
③ ~/.tmuxd.conf 的 tmux-bin  ← install  ③ ~/.tmuxd.conf 的 ttyd-bin  ← install 写的
④ PATH                                   ④ PATH
⑤ 没有 → TmuxMissing(给安装命令)        ⑤ 包里自带的
                                         ⑥ 没有 → TtydMissing
```

**配置排在 PATH 前面**,因为它是显式的:你跑过 `install`,就是表达了"用这一份"。
不想要就删掉那两行,或者 `tmuxd install --forget`。

每一级都要过**同一套合格性检查**(tmux ≥ 3.0、ttyd ≥ 1.6)。配置里记的路径失效了
(二进制被删、被升级搬走)不该直接炸,而是**当作没配**继续往下找,并提示一次:

```console
$ tmuxd ls
⚠ ~/.tmuxd.conf 里的 ttyd-bin 已经不在了,改用 PATH 上的;tmuxd install 可以修好
```

## 7. 版本检查是这条命令的一半

`install` 不只是"弄一个来",还要**说清现在这台机器是什么状况**。不带参数跑就是一次体检:

```console
$ tmuxd install
tmux   ✓ 3.3a          /usr/bin/tmux              (需要 ≥ 3.0)
ttyd   ✓ 1.7.7         ~/.tmuxd/bin/ttyd          (需要 ≥ 1.6)
写入   ~/.tmuxd.conf   tmux-bin / ttyd-bin

$ tmuxd install        # 已经装好、也已经记下来的时候
tmux   ✓ 3.3a          /usr/bin/tmux
ttyd   ✓ 1.7.7         ~/.tmuxd/bin/ttyd
无需改动。
```

**幂等**:装好了就什么都不做。要强制换一份用 `--refresh`。

版本太旧的处理和 [06 §3.2](06-dependencies.md) 一致:tmux 太旧**报错**(它没得退),
ttyd 太旧**降级**到自带的或重新下载。

## 8. 安全:这改写了 06 §7 的一条

[06 §7](06-dependencies.md) 里有一条"不做":

> ❌ **运行时从网络下载 ttyd** —— "从互联网下载并执行一个二进制"的安全故事比自带更差

**那条要改写,不是推翻。** 写它的时候,对照的是"**把下载当成默认路径**" ——
每次 `pip install` 都从网上拉一个二进制,而那是拿到它的唯一方式。那确实更差。

这条命令是另一种形状,区别是实质性的:

| | 当时拒绝的 | 现在做的 |
| --- | --- | --- |
| 什么时候下载 | 每次安装,自动 | **你自己敲了 `tmuxd install`** |
| 是不是唯一途径 | 是 | 不是 —— 自带的仍在,离线照样能用 |
| 校验 | 要现设计一套 | **上游 releases 的 `SHA256SUMS`** |
| 出了事谁看得见 | 静悄悄 | 命令的输出就是它干了什么 |

所以新的措辞是:**默认不下载;下载只发生在你明确要求的时候,并且必须验校验和。**

### 8.1 校验和从哪来,以及一个真实的限制

上游从 **1.7.5** 起才在 release 里发 `SHA256SUMS`(实测:1.7.5/1.7.6/1.7.7 有,
1.7.4 及更早没有)。所以:

- **装清单里钉的那个版本**(默认):校验和直接来自
  [`scripts/ttyd_assets.json`](../../../scripts/ttyd_assets.json),仓库里就有,最强;
- **`--ttyd-version X` 指定别的版本**:从那个 release 拉 `SHA256SUMS` 再验。
  信任根是 GitHub 的 HTTPS 加 `tsl0922/ttyd` 这个仓库;
- **指定 1.7.4 或更早**:上游没发校验和 —— **拒绝**,并说明原因。
  不给"我帮你下但验不了"这个选项。

## 9. 失败要长什么样

```console
① 网络不通,自带的能用
   ttyd  ⚠ 下载失败(连不上 github.com),改用包里自带的 1.7.7
         网络恢复后:tmuxd install --refresh

② 网络不通,这个架构也没自带的
   ttyd  ✗ 下载失败,而且没有 s390x 的自带版本
         手动:从 https://github.com/tsl0922/ttyd/releases 下 ttyd.s390x
               放到任意位置,然后 tmuxd install --ttyd-bin /path/to/ttyd

③ 校验和对不上
   ttyd  ✗ 校验和不符,已丢弃
         期望 8a217c…  实际 3f91ab…
         这可能是网络劫持,也可能是上游换了产物 —— 不会安装。

④ 没有 tmux,而且没权限装
   tmux  ✗ 没找到
         这台机器上是: sudo apt install tmux
         (tmuxd 不会替你提权;装好后再跑一次 tmuxd install)
```

第 ③ 条的措辞要克制:**校验和不符就是不装**,不给 `--force`。
一个能被绕过的校验等于没有校验。

## 10. 不做什么

- ❌ **代跑 `sudo`** —— 非 root 时只给命令(§2);
- ❌ **下载或编译 tmux** —— 三条代价见 [06 §2](06-dependencies.md),这条命令不改变它;
- ❌ **`--force` 跳过校验和**(§9);
- ❌ **默认自动下载** —— 只在你敲了这条命令时才发生(§8);
- ❌ **装的时候顺便改别的配置** —— 只碰 `tmux-bin` / `ttyd-bin` 两个键(§4.1);
- ❌ **让库读配置里的行为类键** —— 只读工具链那两个(§5);
- ❌ **自动更新** —— 没有后台检查、没有"发现新版本"提示。要换就自己 `--refresh`。

## 11. 影响清单

| 改哪 | 改什么 |
| --- | --- |
| `tmuxd/install.py`(新) | 体检、下载+校验、写配置块;`tmux` 的包管理器探测 |
| `tmuxd/cli.py` | 新增 `install` 子命令(`--refresh` / `--print` / `--forget` / `--ttyd-version` / `--tmux-bin` / `--ttyd-bin`) |
| `tmuxd/config.py`(新) | `set -g` 的读与**保序改写**,CLI 和库共用 |
| `tmuxd/core.py` | 查找顺序插入"配置"一级(§6);路径失效时降级并提示 |
| `tmuxd/tmux.py` | `find_binary` 接受配置来的路径;`TmuxMissing` 带上按发行版的安装命令 |
| `tests/installing/`(新场景) | 网络优先与降级、校验和不符不装、配置块只碰两行、失效路径降级、幂等 |
| `tests/nothing_reads/` | 命令集从 13 条变 14 条 |
| `docs/v1/cli/install.md`(新) | 使用文档 |
| [`06 §7`](06-dependencies.md) | 那条"不做"改写成 §8 的措辞 |
| `README` × 2 | 依赖那节提一句:冷门架构或想要最新版就 `tmuxd install` |
