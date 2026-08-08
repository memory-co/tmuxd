# tmuxd

**tmuxd = tmux 的 server,长出一个 HTTP 口。**

`ttyd tmux new -A -s work` 这条命令大家都写过:tmux 让会话活着,ttyd 让你在浏览器里看见。
拼是拼上了,但拼出来的东西没有把手——会话是谁开的、还活着没有、里面刚输出了什么,一概问不到;
想让程序驱动它,只能 ssh 进去敲 `tmux send-keys`。

tmuxd 把这条命令做成一个服务:**会话由 API 管,由 URI 定位,能被程序驱动。**

- 底下就是**一个真的 tmux server** —— `tmux -L tmuxd attach -t work` 接的是同一个现场,不是黑盒
- **门面挂了,屋子还在**:tmuxd 重启、升级、被 kill -9,会话统统不受影响
- `send-keys` / `capture-pane` 有了 HTTP 版,还有拿得到退出码的 `run`
- 分享链接默认只读(抄 ttyd),人和程序 attach 的是同一个 pane

```bash
pip install tmuxd
tmuxd start --tmux-socket default   # 接管你现在正在用的那个 tmux
tmuxd ls                            # 手里那些会话,现在都有网页了
```

```python
from tmuxd import Server
t = Server("http://box:7681", token="...").session("work")

t.send("npm test", enter=True)
print(t.capture())                  # 屏幕上是什么
print(t.run("git rev-parse HEAD"))  # 要退出码就用 run
print(t.share(read_only=True))      # 给同事的围观链接
```

设计文档:[`docs/v1/works`](docs/v1/works/)
