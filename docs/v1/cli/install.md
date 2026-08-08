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
| `install` 能做到 | **检测版本**;已经是 root 就代跑包管理器,否则把命令打给你 | **全自动**:下 latest → 验校验和 → 落到 `~/.tmuxd/bin/ttyd` |

装完把两条路径记进 [`~/.tmuxd.json`](#tmuxdjson),**下次库和 CLI 都自动读到**。

## 用法

```console
$ tmuxd install
tmux   ✓ 3.3a         /usr/bin/tmux
ttyd   · downloading https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64
ttyd   ✓ 1.7.7        /home/me/.tmuxd/bin/ttyd  (download)
写入   /home/me/.tmuxd.json
```

**这条命令没有任何参数。** 指定用哪个二进制只有一个地方 —— 就是 `~/.tmuxd.json`
(见[下面](#tmuxdjson))。规则一句话:

> **json 里有,就只检查,不安装;json 里没有,才去装,装完写进去。**

| 你想 | 怎么做 |
| --- | --- |
| 指定某个二进制 | 编辑 `~/.tmuxd.json` |
| 换一个新的 ttyd | `rm ~/.tmuxd.json`,再跑一次 |
| 撤销全部 | `rm ~/.tmuxd.json` |

已经配好之后再跑,就只是一次体检:

```console
$ tmuxd install
tmux   ✓ 3.3a         /usr/bin/tmux             (~/.tmuxd.json)
ttyd   ✓ 1.7.7        /home/me/.tmuxd/bin/ttyd  (~/.tmuxd.json)
无需改动。
```

json 里那条用不了的话,**它会告诉你,然后停下** —— 不替你改,也不悄悄换一个:

```console
$ tmuxd install
tmux   ✓ 3.3a         /usr/bin/tmux             (~/.tmuxd.json)
ttyd   ✗ /opt/nope in /home/me/.tmuxd.json cannot run, or is older than 1.6
ttyd     fix that line, or delete it and run `tmuxd install` again
```

退出码:两个都齐了 `0`,缺一个 `1`。

## 优先级:网络 > 包里自带

这条乍看反直觉 —— 包里明明带着一份验过的二进制。区别在于:

> **自带的那份是"发 wheel 那天的 ttyd",网上的是"现在的 ttyd"。**

自带的 Mbed TLS 是焊死的,`apt upgrade` 修不到,**只能等 tmuxd 发版**。
所以就三步,没有分支:**latest → 自带 → 报错**。退到自带时一定会说出来:

```console
ttyd   ⚠ download failed: … (Connection refused)
ttyd     using the build bundled in the wheel
```

**注意"已经装好了"不包括自带的那份。** json 里记了、或者 PATH 上有,才算装好;
只有自带的,`install` 仍然会去联网 —— 否则它要解决的"陈旧"问题永远解决不了。

## 校验和

先问上游 latest 是哪一版(读 `/releases/latest` 重定向到哪儿),
再拉**那一版自己的 `SHA256SUMS`** 比对。对不上就**丢弃**,而且没有 `--force` ——
能被绕过的校验等于没有校验。

丢弃之后仍然**退回包里自带的那份**:自带的是仓库里验过的,和网上这次出了什么事无关。

```console
ttyd   ⚠ download failed: checksum mismatch, discarded
         expected 8a217c…
         got      3f91ab…
         this could be a hijacked download or a changed upstream asset -- it will not be installed.
ttyd     using the build bundled in the wheel; `rm ~/.tmuxd.json` and run it again to retry upstream
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

**每次构造 `Tmuxd()` 都会复验 ③,不合格就报错** —— 不会悄悄改用 PATH 上那个:

```
TtydMissing: /opt/my-ttyd (named by /home/me/.tmuxd.json) cannot run, or is
older than 1.6.
  fix that line, delete it, or run `tmuxd install` again.
  it is not silently replaced -- you would be running something other than
  what the file says.
```

这个文件是你写的,**换一个去跑等于跑的不是你写的那个,而文件还在那儿声称是它。**

## 相关

- [`README.md`](README.md) —— 命令总表、`-s` 参数、退出码、`~/.tmuxd.conf`
- [`server.md`](server.md) —— CLI 为什么必须有 server
- [`../works/07-install.md`](../works/07-install.md) —— 为什么这样设计
- [`../works/06-dependencies.md`](../works/06-dependencies.md) —— 为什么 tmux 只探测、ttyd 自带
