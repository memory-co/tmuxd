# tmuxd

**tmuxd = 一个 Python 库,把 tmux 和 ttyd 拼成"活得比连接久、还能被程序往里敲的终端"。**

`ttyd tmux new -A -s work` 这条命令大家都写过:tmux 让会话活着,ttyd 让你在浏览器里看见。
拼是拼上了,但拼出来的东西没有把手——会话是谁开的、开在哪个目录、还活着没有,一概问不到;
想从外面往里投喂一条指令,只能 ssh 进去敲 `tmux send-keys`。

```python
from tmuxd import Tmuxd

t = Tmuxd(port=12345, token="changeme")   # 这行之后:ttyd 起来了,tmux 还没有
s = t.session(id="id5", cwd="~/proj", cmd="claude")
s.send("把测试跑一遍", enter=True)
print(s.url)                              # http://localhost:12345/?arg=id5
```

四行。那个 URL 发给谁,谁在浏览器里就进了这个终端 —— 看得见,也能直接接手敲。

- **核心是库,不是服务** —— CLI 和 HTTP 都只是套在外面的壳,**HTTP 默认不开**
- **会话模型小到一句话** —— 一个 id、一个启动目录、一条启动命令,没有第四个字段
- **门面短命,屋子长命** —— ttyd 是你进程的子进程,tmux 会话谁的都不是,你退了它还在
- **不读,只写** —— 没有 capture / run / 输出流;读归人(打开 URL)或归 ssh
- **全部可读可写** —— 这一层不做权限,要区分谁能看谁能写,往有身份的上层去锁
- **不碰你自己的 tmux** —— 只探测二进制,一律 `-L` 开专属池;你的 `tmux ls` 一个不多一个不少
- **没有自己发明的路径** —— 进终端的地址就是 ttyd 原生的 `?arg=<id>`

```bash
pip install tmuxd
tmuxd start                 # 想让网页入口活得比脚本久,就用 CLI 起一个常驻的
tmuxd new -s work -c ~/proj
tmuxd url -t work
```

## 文档

| | |
| --- | --- |
| [`docs/v1/sdk`](docs/v1/sdk/) | **Python SDK** —— `Tmuxd`、`Session`、异常、`RemoteTmuxd` |
| [`docs/v1/cli`](docs/v1/cli/) | **命令行** —— 十三条命令、退出码、配置 |
| [`docs/v1/works`](docs/v1/works/) | **设计稿** —— 为什么减成这样 |
| [`CHANGELOG.md`](CHANGELOG.md) | **2.0 是破坏性的** —— 改了什么、怎么迁 |
