# tmuxd

**tmuxd = tmux 的 server,长出一个 HTTP 口。**

`ttyd tmux new -A -s work` 这条命令大家都写过:tmux 让会话活着,ttyd 让你在浏览器里看见。
拼是拼上了,但拼出来的东西没有把手——会话是谁开的、还活着没有、里面刚输出了什么,一概问不到;
想让程序驱动它,只能 ssh 进去敲 `tmux send-keys`。

tmuxd 把这条命令做成一个服务:**会话由 API 管,由 URI 定位,能被程序驱动。**

- **门面挂了,屋子还在**:tmuxd 重启、升级、被 kill -9,会话统统不受影响
- `send-keys` / `capture-pane` 有了 HTTP 版,还有拿得到退出码的 `run`
- **一个会话就是一个终端** —— 不做 window / pane,要几个终端就开几个会话
- **全部可读可写,这一层不做权限** —— 要区分谁能看谁能写,往有身份的上层去锁
- **会话池是专属的** —— 只探测 tmux 二进制,一律 `-L` 开独立 server,不碰你自己的 tmux

```bash
pip install tmuxd
tmuxd start       # 起服务;你自己的 tmux ls 一个不多一个不少
tmuxd new -s work -c ~/proj
tmuxd attach -t work -p             # → http://localhost:7681/s/work/
```

```python
from tmuxd import Server
t = Server("http://box:7681", token="...").session("work")

t.send("npm test", enter=True)
print(t.capture())                  # 屏幕上是什么
print(t.run("git rev-parse HEAD"))  # 要退出码就用 run
print(t.share())                    # 限这个会话、限一小时的链接,发给同事接手
```

设计文档:[`docs/v1/works`](docs/v1/works/)
