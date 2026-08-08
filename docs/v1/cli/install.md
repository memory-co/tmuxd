# `tmuxd install` —— 把两个二进制凑齐

设计依据:[`../works/07-install.md`](../works/07-install.md)

> **先说清楚:这条命令不是安装步骤。**
> Linux 上 `pip install tmuxd`、机器上有 `tmux`,就已经能用了 —— `Tmuxd(...)` 直接跑,
> **`tmuxd install` 一次都不用敲**,而且 `Tmuxd()` 也不会因为你没跑过它而报错。
> 它是给**环境不齐**的机器准备的:冷门架构、没装 tmux、或者你想要比包里那份更新的 ttyd。

## 它管什么

| | tmux | ttyd |
| --- | --- | --- |
| 上游有预编译产物吗 | 没有,只有源码 | 有,静态 musl |
| `install` 能做到 | **检测版本**;已经是 root 就代跑包管理器,否则把命令打给你 | **全自动**:下载 → 验校验和 → 落到 `~/.tmuxd/bin/ttyd` |

装完把两条路径记进 [`~/.tmuxd.json`](#tmuxdjson),**下次库和 CLI 都自动读到**。

## 用法

```console
$ tmuxd install
tmux   ✓ 3.3a         /usr/bin/tmux
ttyd   · downloading https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64
ttyd   ✓ 1.7.7        /home/me/.tmuxd/bin/ttyd  (download)
写入   /home/me/.tmuxd.json
```

不带参数跑就是**一次体检加一次补齐**。已经齐了就什么都不做:

```console
$ tmuxd install
tmux   ✓ 3.3a         /usr/bin/tmux
ttyd   ✓ 1.7.7        /usr/local/bin/ttyd
无需改动。
```

| 参数 | 干什么 |
| --- | --- |
| `--refresh` | 已经有能用的也重新下一份。**换新版本靠这个** |
| `--ttyd-version X.Y.Z` | 指定上游版本(必须 ≥ 1.7.5,见[下面](#校验和)) |
| `--tmux-bin PATH` | 不去找,就记这一个 |
| `--ttyd-bin PATH` | 不去下,就记这一个(手动下载完用它收尾) |

退出码:两个都齐了 `0`,缺一个 `1`。

## 优先级:网络 > 包里自带

这条乍看反直觉 —— 包里明明带着一份验过的二进制。区别在于:

> **自带的那份是"发 wheel 那天的 ttyd",网上的是"现在的 ttyd"。**

自带的 Mbed TLS 是焊死的,`apt upgrade` 修不到,**只能等 tmuxd 发版**。
所以顺序是**先网络,连不上再退回自带**,而且退回时一定会说出来:

```console
ttyd   ⚠ download failed: … (Connection refused)
ttyd     using the build bundled in the wheel; `tmuxd install --refresh` once the network is back
```

**注意"已经装好了"不包括自带的那份。** PATH 上有、或者 json 里记了,才算装好;
只有自带的,`install` 仍然会去联网 —— 否则它要解决的"陈旧"问题永远解决不了。

## 校验和

- **默认版本**:校验和来自仓库里的 [`tmuxd/data/ttyd/assets.json`](../../../tmuxd/data/ttyd/assets.json);
- **`--ttyd-version` 别的版本**:从那个 release 拉 `SHA256SUMS` 再比;
- **1.7.4 及更早**:上游那时还没发 `SHA256SUMS` —— **直接拒绝**,不提供"下了但不验"。

对不上就**丢弃,不安装**,而且没有 `--force`。

**注意它不会退回包里自带的那份。** 网络不通是天气,给你一个能用的是帮忙;
校验和不符、版本太老不给验,这些是**决定** —— 这时候塞一个别的版本给你,
等于同时谎报了版本、来源和原因。所以拒绝**直接终止命令**(退出码 1):

```console
ttyd   ✗ checksum mismatch, discarded
         expected 8a217c…
         got      3f91ab…
         this could be a hijacked download or a changed upstream asset -- it will not be installed.
```

## tmux:不会替你 `sudo`

tmux 上游只发源码,装它只能走系统包管理器,而那要 root。规则是:

- **已经是 root**(容器里的常态)→ 直接跑 `apt-get install -y tmux` 之类;
- **不是 root** → **只打印那条命令**,你自己跑:

```console
tmux   ✗ not found
tmux     this machine wants: sudo apt install tmux
tmux     tmuxd will not escalate for you; run that, then `tmuxd install` again
```

认得 `apt-get` / `dnf` / `yum` / `zypper` / `pacman` / `apk` / `brew` / `pkg`。

## `~/.tmuxd.json`

`install` 写的就是这个文件,**只有两个键**:

```json
{
  "tmux": "/usr/bin/tmux",
  "ttyd": "/home/me/.tmuxd/bin/ttyd"
}
```

没有 port,没有 token,不会有第三个键。**正因为里面只有"这两个程序在哪",
库才敢默认读它** —— 端口和 token 是行为,必须由调用方决定,不能被 home 目录下的一个文件改掉。

| | `~/.tmuxd.conf` | `~/.tmuxd.json` |
| --- | --- | --- |
| 谁写 | **人**(见 [README 配置文件](README.md#配置文件)) | **`tmuxd install`** |
| 写什么 | 端口、token、默认值 | 两个二进制在哪 |
| 谁读 | 只有 CLI | **CLI 和库都读** |

`install` **从不碰 `.tmuxd.conf`**。想撤销?`rm ~/.tmuxd.json` 就回到没跑过的状态。
`TMUXD_JSON` 可以把它指到别处。

## 查找顺序

```
tmux                                 ttyd
① Tmuxd(tmux_bin=…)                  ① Tmuxd(ttyd_bin=…)
② TMUXD_TMUX_BIN                     ② TMUXD_TTYD_BIN
③ ~/.tmuxd.json 的 tmux              ③ ~/.tmuxd.json 的 ttyd
④ PATH                               ④ PATH
⑤ 没有 → 报错,附上安装命令           ⑤ 包里自带的
                                     ⑥ 没有 → 报错
```

**每次构造 `Tmuxd()` 都会复验 ③**。记的那个二进制被删了、被升级搬走了,
不会报错,而是**退回去继续找**,并 warn 一次:

```
RuntimeWarning: ttyd recorded in /home/me/.tmuxd.json is gone (/home/me/.tmuxd/bin/ttyd);
falling back. `tmuxd install` fixes it.
```

一个过期的缓存文件,不该让本来能跑的机器跑不起来。

## 相关

- [`README.md`](README.md) —— 命令总表、`-s` 参数、退出码、`~/.tmuxd.conf`
- [`server.md`](server.md) —— CLI 为什么必须有 server
- [`../works/07-install.md`](../works/07-install.md) —— 为什么这样设计
- [`../works/06-dependencies.md`](../works/06-dependencies.md) —— 为什么 tmux 只探测、ttyd 自带
