# daemon:让门面活得比脚本久

库形态下 ttyd 是**你进程的子进程**,脚本退出它就没了。想让那个网页入口一直在,
就得有个进程守着 —— 这就是 `serve` / `start`。

**门面和屋子是分开的**:这里停掉的都只是 ttyd,tmux 会话一个不动。

---

## `tmuxd serve`

前台阻塞,日志走 stdout。给 systemd 的 `ExecStart`、容器 ENTRYPOINT 用。

```
tmuxd [-L NAME] [--port N] [--bind ADDR] [--token T] serve [--http-port N]
```

| 选项 | 说明 |
| --- | --- |
| `--http-port N` | 顺便把 HTTP 壳也起在这个端口上。**默认不起** |

```console
$ tmuxd serve --port 12345 --token changeme
tmuxd 1.0.0
ttyd:  http://127.0.0.1:12345   token=changeme…
tmux:  /usr/bin/tmux 3.3a   socket=tmuxd (dedicated)   server not started yet
```

最后那句 `server not started yet` 不是异常:**tmux server 是懒起的**,
第一个会话出现时才拉起来。

systemd 单元大致长这样:

```ini
[Service]
ExecStart=/usr/local/bin/tmuxd --port 12345 --token %S/tmuxd-token serve
Restart=on-failure
```

> **重启这个 unit 不会打断任何会话** —— 只是换一个 ttyd。正在看网页的人会断线重连,
> 终端现场原封不动。

---

## `tmuxd start` / `stop` / `status`

给"手边没有进程管理器"的人用。`start` 就是把 `serve` 甩到后台
(`start_new_session`),再轮询等它就绪。

```console
$ tmuxd start --port 12345
tmuxd 1.0.0
ttyd:  http://127.0.0.1:12345   token=8f2c1e9a…(已存 ~/.tmuxd/default/token)
tmux:  /usr/bin/tmux 3.3a   socket=tmuxd (dedicated)   server not started yet
```

起不来时**把日志尾巴摆到眼前**,而不是丢个 pid 让你自己找:

```console
$ tmuxd start
tmuxd did not come up. Tail of ~/.tmuxd/default/daemon.log:
  ...
```

```console
$ tmuxd status
daemon:    running (pid 598198)
ttyd port: listening on 12345
```

`status` **不信文件**:pid 记录可能过期,所以它回头核实进程还在不在、端口应不应答。
`--json` 给脚本用:

```console
$ tmuxd --json status
{"daemon": true, "pid": 598198, "port": 12345, "listening": true}
```

退出码:两样都不在时返回 1。

```console
$ tmuxd stop
ttyd stopped. 1 session(s) still running (tmuxd start brings the door back).
```

**那行提示不是客套话。** 它是这个工具最需要被理解的性质:
`stop` 关的是门,屋里的人还在干活;`tmuxd start` 回来就还能进去。

---

## `tmuxd info`

```console
$ tmuxd info
tmuxd   1.0.0
ttyd    1.7.7-40e79c7  port=12345  owned=False
tmux    3.3a  /usr/bin/tmux  socket=tmuxd-docdemo2  running=True
sessions 2 total  2 alive  0 exited  0 external
```

三行分别回答三个问题:

- **`ttyd`** —— 门开着没有。`owned` 表示这个 ttyd 是不是**当前这条命令**起的;
  从 CLI 看几乎总是 `False`,因为读命令不会顺手起一个 ttyd。
  没人守着时显示 `not running (port 12345 would be the entrance)`;
- **`tmux`** —— 二进制在哪、什么版本、用的哪个**专属 socket**、server 起来没有;
- **`sessions`** —— `external` 那一栏正常应该是 0,不是 0 说明有人绕过 tmuxd
  直接往这个 socket 里 `tmux new-session` 了([sessions.md](sessions.md#external))。

`--json` 输出的就是 `Tmuxd.info()` 的原样序列化,和 HTTP 的 `GET /api/info` 完全一致。

---

## `tmuxd kill-server --tmux`

**销毁这个池里的全部会话。** 需要两道确认才会动手:

```console
$ tmuxd kill-server
✗ pass --tmux to confirm: this destroys every session in this pool

$ tmuxd kill-server --tmux
kill the tmux server for socket tmuxd? [y/N] y
tmux server killed (socket tmuxd). Your own tmux is untouched.
```

- `--tmux` 是**必须显式给**的确认,因为这条命令和 tmux 的 `kill-server` 名字像、
  行为却差得远(那边杀你自己的 server);
- 交互式终端下还会再问一次;脚本里用 `-y` 跳过;
- 杀的是 **tmuxd 专属的那个 tmux server** —— 你自己的 `tmux` 在另一个 socket 上,碰不到。

---

## 谁挂了会怎样

| 挂的是 | 会话 | 恢复 |
| --- | --- | --- |
| **daemon 进程** | **全活着** | `tmuxd start` 回来,`ls` 一探全回来 |
| **ttyd** | 全活着 | 网页入口暂时没了;重新 `start` 会补一个 |
| **tmux server** | 全没 | 无。记录标 `exited`,7 天后清掉 |
| **机器重启** | 全没 | 同上 —— tmux 从不做跨重启持久化,tmuxd 也不假装做 |
