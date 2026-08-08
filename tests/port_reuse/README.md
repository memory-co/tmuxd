# port_reuse — 同一个端口上已经有 ttyd 了怎么办

## 这个场景在测什么

ttyd **不持有任何会话状态**(它只 exec `attach.sh`),这让"复用"成了安全的选择。
现实里的触发场景很具体:**一个 Web 后端重启它的 worker**,每个 worker 都
`Tmuxd(port=…)` 一下。如果每次都想独占端口,结果要么是撞端口起不来,
要么是把用户正连着的网页踢掉。

三条分支,三种态度:

1. **端口空着** → 起一个,**归我管**:我 `close()` 它就走。
2. **端口上是 tmuxd 起的 ttyd(同一个池)** → **接手用**,不重起。
   而且**接手方 `close()` 不能带走它** —— 那不是你的孩子,别人还连着。
3. **端口上是别人的东西** → `PortInUse`,**不猜、不抢**。
   猜错的两种后果都不可接受:要么劫持了陌生人的服务,要么把用户送进了错的终端。

外加一条观测:**`info()` 能报出门开着没有,而不必自己去开一个** ——
CLI 的读命令全靠它(否则 `tmuxd ls` 会顺手起一个 ttyd 然后立刻带走)。

## 不在这测什么

- **ttyd 跟着进程走**(`PR_SET_PDEATHSIG`)—— 在
  [`survives_the_process/`](../survives_the_process/),那讲的是生死绑定,不是端口归属。
- ttyd 里面服务的内容 —— 在 [`the_entrance/`](../the_entrance/)。

## fixture 来源

- `free_port()` / `kill_pool()`(`tests/conftest.py`)
- 用例自己构造多个 `Tmuxd`,因为**要验的就是"第二个怎么办"**,共享 fixture 反而挡路
- "陌生人"用一个裸 `socket.listen()` 扮演 —— 它不是 ttyd,也不该被当成 ttyd
