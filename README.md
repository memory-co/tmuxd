# tmuxd

**tmuxd = tmux 的 server,长出一个 HTTP 口。**

`ttyd tmux new -A -s work` 这条命令大家都写过:tmux 让会话活着,ttyd 让你在浏览器里看见。
拼是拼上了,但拼出来的东西没有把手——会话是谁开的、开在哪个目录、还活着没有,一概问不到;
想从外面往里投喂一条指令,只能 ssh 进去敲 `tmux send-keys`。

tmuxd 把这条命令做成一个服务:**会话由 API 管,能被程序往里敲。**

会话模型小到一句话:**一个 id、一个启动目录、一条启动命令。**

- **门面挂了,屋子还在**:tmuxd 重启、升级、被 kill -9,会话统统不受影响
- **一个会话就是一个终端** —— 不做 window / pane,要几个终端就开几个会话
- **不读,只写** —— 没有 capture / run / 输出流;读归人(attach 进去看)或归 ssh
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

t.send("npm test", enter=True)      # 往里敲,就这一个动作
print(t.share())                    # 限这个会话、限一小时的链接,发给同事接手
```

设计文档:[`docs/v1/works`](docs/v1/works/)
