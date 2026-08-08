# server:CLI 的前提

**CLI 不能单独工作 —— 它需要一个 server 在跑。**

`tmuxd ls` 是一个活几十毫秒就退出的进程:它持不住 ttyd(ttyd 会跟着它死),
也持不住会话状态。所以它只能**去问一个持得住的东西**。那就是 `tmuxd serve`
([设计理由](../works/03-server.md))。

```bash
pip install "tmuxd[server]"     # CLI 和 server 是一体的,装就一起装
tmuxd start                     # 起来
tmuxd new -s work -c ~/proj     # 现在 CLI 才有东西可打
```

> **只用 Python SDK 的话不需要这些。** 你的进程本身就持有实例,
> `pip install tmuxd` 零依赖就够 —— 见 [SDK 文档](../sdk/)。

---

## 两个端口

```
:7681   ttyd      ← 人。浏览器打开 ?arg=<id> 进终端
:7682   管控口     ← 程序。CLI 打它
```

**两拨用户,所以不合并。** ttyd 那个地址是**唯一对人暴露的**,可以直接贴给同事;
管控口是 JSON 进 JSON 出,给 CLI 和别的语言用。

| | ttyd 口 | 管控口 |
| --- | --- | --- |
| 默认 | `7681` | `7682` |
| 给谁 | 人的浏览器 | CLI / 程序 |
| 绑哪 | `127.0.0.1`(要对外得配 token) | `127.0.0.1`,**本来就该是本机的** |
| 鉴权 | ttyd 的 basic auth | `Authorization: Bearer` |
| 参数 | `--port` | `--control-port` |

**管控口不打算给远端用。** CLI 要驱动别的机器,答案是 `ssh box tmuxd …`
—— 不用多开端口、不用管第二份 token、复用 ssh 的鉴权和审计。

---

## `tmuxd serve`

前台阻塞,日志走 stdout。给 systemd 的 `ExecStart`、容器 ENTRYPOINT 用。

```
tmuxd [-L NAME] serve [--port N] [--control-port N] [--bind ADDR] [--token T]
```

```console
$ tmuxd serve --port 7681 --control-port 7682 --token changeme
tmuxd 1.0.0
ttyd:     http://127.0.0.1:7681        token=changeme…
control:  http://127.0.0.1:7682/api    (CLI 打这个)
tmux:     /usr/bin/tmux 3.3a   socket=tmuxd (dedicated)   server not started yet
```

最后那句 `server not started yet` 不是异常:**tmux server 是懒起的**,
第一个会话出现时才拉起来。

systemd 单元:

```ini
[Service]
ExecStart=/usr/local/bin/tmuxd serve --token-file /etc/tmuxd/token
Restart=on-failure
```

> **重启这个 unit 不会打断任何会话** —— 只是换一个 ttyd 和一个管控口。
> 正在看网页的人会断线重连,终端现场原封不动([daemon.md](daemon.md))。

---

## `tmuxd start` / `stop` / `status`

给"手边没有进程管理器"的人用。`start` 就是把 `serve` 甩到后台再轮询等它就绪。

```console
$ tmuxd start
tmuxd 1.0.0
ttyd:     http://127.0.0.1:7681        token=8f2c1e9a…(已存 ~/.tmuxd/default/token)
control:  http://127.0.0.1:7682/api
tmux:     /usr/bin/tmux 3.3a   socket=tmuxd (dedicated)   server not started yet

$ tmuxd status
server:   running (pid 598198)
ttyd:     listening on 7681
control:  listening on 7682

$ tmuxd stop
server 已停。3 个会话仍在运行(tmuxd start 回来即可)。
```

**`stop` 停的是门面,不是屋子** —— 会话由 tmux 持有,谁的子进程都不是。

---

## 没起 server 的时候

CLI 会**如实说**,而不是顺手起一个然后立刻带走:

```console
$ tmuxd ls
✗ 没有 server 在跑(127.0.0.1:7682 上没人听)。先 tmuxd start。
```

一条只读命令不该留下副作用。这也是 `tmuxd = tmux + ttyd` 的直接后果:
**没有完整的 tmuxd,就没有 tmuxd 可查。**

没装 server 依赖时,报错说的是另一件事:

```console
$ tmuxd serve
✗ tmuxd serve 需要 server 依赖:pip install "tmuxd[server]"
  (只用 Python SDK 的话不需要它 —— 见 docs/v1/sdk)
```

---

## 装了什么

```bash
pip install tmuxd              # 库,零运行时依赖
pip install "tmuxd[server]"    # + fastapi + uvicorn
```

**为什么是 FastAPI + uvicorn**:既然已经是一个要显式安装的可选件,就该给个像样的 ——
请求校验从类型标注来、自动出 OpenAPI(管控口本来就是给别的语言调的)、
uvicorn 的 keep-alive / 并发 / 优雅关闭。拿标准库手写这些,
正是这个项目一直在躲的"重造已有的东西"([works/03 §4](../works/03-server.md))。

**这两个依赖只落在选了 CLI + server 的人身上。** 只嵌库的人一行 Web 框架都用不上,
不该替别人背包袱。

---

## 多实例

`-L` 换的是 **tmuxd 实例**,两个端口和 tmux socket 一起换:

```bash
tmuxd -L ci serve --port 7691 --control-port 7692
tmuxd -L ci new -t build
```

**实例名同时决定 tmux socket**(`-L ci` → `tmux -L tmuxd-ci`),
所以两套实例的会话池互不可见,也都不碰你自己的 tmux。

端口不会自动错开 —— 同机跑多套时**自己指定**,撞了会 `port_in_use`,不猜不抢。

---

## 别的语言怎么用

管控口就是普通的 JSON HTTP,七个端点,`GET /api/info` 里有 OpenAPI 的位置:

```bash
curl -H "Authorization: Bearer $TOKEN" \
     -d '{"id":"work","cwd":"/srv/app","cmd":"claude"}' \
     -H 'Content-Type: application/json' \
     http://127.0.0.1:7682/api/sessions
```

返回里的 `url` 指向 **ttyd 的端口** —— 那是你要发给人的地址。
端点清单见 [works/03 §9](../works/03-server.md)。
