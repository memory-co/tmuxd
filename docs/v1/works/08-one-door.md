# 08 · 一扇门:ttyd 走 unix socket,窗挂到宿主的路由上

```python
t = Tmuxd(base_path="/tty")                     # ttyd 听在 <state_dir>/ttyd.sock
s = t.session(id="id5", cmd="claude")
s.url                                           # "/tty/?arg=id5" —— 相对地址
app.mount("/tty", t.asgi())                     # pip install "tmuxd[asgi]";默认不鉴权(§3)
```

## 1. 为什么

TCP 模式下 ttyd 自己占一个端口,**窗在宿主的门外**。嵌进 Web 后端(memory.talk 就是)时,这带来三件事:

- **多一个端口要开防火墙。** 真出过事:ttyd 好好的,是云上防火墙只放了宿主那个口;
- **多一套认证,而且跟宿主的登录毫无关系。** ttyd 的 basic auth 是一个明文、全员共用的 token
  —— 拿到它的人绕过宿主的登录直接进 shell,宿主的登出、改密码也管不到它;
- **一串只为"让浏览器找得到窗"而存在的配置**(端口、bind、token、对外主机名)。

宿主本来就有一个端口、一扇门(登录态)。窗应该从同一个端口、同一扇门进去。

## 2. 形状

```
浏览器 ──https──▶ 宿主(uvicorn,一个端口)
                  ├─ /api/...  宿主自己的路由
                  ├─ /tty/...  ──▶ t.asgi(authorize=…) ──unix socket──▶ ttyd -i ttyd.sock -b /tty
                  └─ /         宿主的前端
```

窗仍然是 ttyd 那一页,tmuxd 不碰它的内容。变的只有两处:

| | 核心(`import tmuxd`) | 扩展(`tmuxd[asgi]`) |
| --- | --- | --- |
| 改了什么 | 多两个**部署参数**:`listen="unix"`、`base_path="/tty"` | 多一个 `t.asgi()`:把窗交成一个 ASGI app |
| 依赖 | 无,同以前 | `websockets`(只用来连 ttyd 那一侧) |
| 不装会怎样 | —— | 和以前一样;socket 模式下没人进得去窗,直到宿主挂上一个转发 |

### 2.1 `listen`:ttyd 听在哪

| | `"unix"`(**默认**) | `"tcp"` |
| --- | --- | --- |
| ttyd 参数 | `-i <state_dir>/<socket>/ttyd.sock` | `-p <port> -i <bind>` |
| 门 | socket 文件权限 **0600** | basic auth(`token`) |
| `s.url` | `/tty/?arg=id5`(相对) | `http://127.0.0.1:12345/?arg=id5` |
| 复用记录 | `ttyd-unix.json`(按 socket 路径认领) | `ttyd-<port>.json`(按端口认领) |
| 谁用 | 嵌进一个 Web 后端 | CLI(`tmuxd start`)、单独使用 |

**传了 `port=` 就是要 TCP**,所以 `Tmuxd(port=12345, token=…)` 的意思一点没变。
什么都不传才落到 socket。`listen=("tcp", 12345)` 是同一件事的另一种写法。

socket 模式下 `port` / `bind` 没有意义,**显式给了就报错**,不静默忽略;
「非回环 bind 必须带 token」那条检查也只在 TCP 模式下才有意义。
`token` 不再传给 ttyd —— 门是文件权限,basic auth 叠在上面只会让浏览器透过转发弹个框。

socket 路径有长度上限(`sun_path`,Linux 108 字节、macOS 104)。
`state_dir` 太深时构造直接 `ValueError` 并说明原因,不让 ttyd 静默截断。

**0600 是 tmuxd 自己 chmod 的。** libwebsockets 无视 umask,总是建成 0660 ——
同组的人不是这个进程在服务的那个人。

**被 SIGKILL 的 ttyd 会留下 socket 文件。** 没人应答的 socket 删掉不会断开任何人,
所以认领失败、且没人在听时,tmuxd 先 unlink 再起新的。有人在听但不是自己人:`PortInUse`(带 `path`)。

### 2.2 `base_path`:ttyd 的页面、`/token`、`/ws` 都带这个前缀

直接传给 ttyd 的 `-b`。**挂载前缀要和它一致**:`Tmuxd(base_path="/tty")` 配 `app.mount("/tty", …)`。
ttyd 自己发的跳转(`/tty` → `/tty/`)写的是它认识的前缀,两者不一致时跳转会指错。

`base_path` 也参与复用判据:一个前缀不同的 ttyd **不会被接手** —— 接手了,发出去的 URL 它不认。

## 3. `t.asgi(authorize=None)`

```python
async def gate(scope):          # 或者同步函数
    ...
    return True                 # 放行
    return False                # 拒:HTTP 403;WebSocket 在握手时关,1008
    return [("set-cookie", …)]  # 放行,并把这些头加到 HTTP 响应上
```

**不传 `authorize` 就是全放行,而这通常是错的。** socket 的 0600 只管「本机别的用户能不能直连 ttyd」;
转发器的全部意义就是把这个私有的 socket 重新接到宿主的公共端口上。所以挂上之后,
谁能连到宿主的端口,谁就能打开 `/tty/?arg=<任意 id>` 拿到 shell —— 比 TCP 模式的 basic auth 还松。
默认值仍然是 `None`,因为确实有两种部署不需要它:宿主只听 `127.0.0.1` 给自己用;
前面已有一层统一认证(Cloudflare Access、oauth2-proxy、VPN)挡住了所有请求。
宿主已有的 `/api` Bearer 中间件**罩不住**这条路 —— iframe 和 WebSocket 带不了那个头 ——
这正是要有一个专门钩子的原因。

**tmuxd 不知道谁是谁,鉴权全在钩子里。** 每个请求都问一次 —— 页面、`/token`、`/ws`、静态资源,
每一条都是一个进门的路,漏问一条就是一扇后门(测试里断言的就是这个)。

钩子拿到的是原始 ASGI `scope`:path、query_string(`arg` 就在这)、headers(cookie 就在这)全在里面。
返回头列表是为「核票种 cookie」准备的:iframe 和浏览器的 WebSocket 都带不了 `Authorization` 头,
所以宿主要在页面请求上核一张短期票、种一个 cookie,之后的 `/ws` 凭 cookie 进门,
并且核对 `arg` 就是票上那个会话 —— **一张票只开一扇窗**。这全是宿主的逻辑,tmuxd 只提供那个挂钩的位置。

转发本身只搬字节:

- **HTTP**:请求原样发到 socket(去掉逐跳头,上游一侧 `Connection: close`),响应原样回;
  `Content-Length` / chunked / 读到 EOF 三种 body 都认。ttyd 没在听:**502,不挂住**;
- **WebSocket**:先连上游、拿到它选的子协议(`tty`),再 accept 浏览器那一侧;
  然后两个 task 各拷一个方向,帧不解析。一侧断了另一侧跟着关;
- 挂成子 app 收不到 lifespan(Starlette 的老坑)—— 所以它**不管 ttyd 的死活**。
  ttyd 仍然在 `Tmuxd.__init__` 里起、`close()` 里收(§4)。单独当 app 跑时它会应答 lifespan。

路径的算法:挂载点之后剩下的部分,前面接上 `base_path`。Starlette 保留完整 `path`、
把前缀加进 `root_path`;老一些的路由器直接截掉 `path` —— 两种都认。

转发器对 ttyd 听在哪无所谓:TCP 模式下它连 `127.0.0.1:<port>`。
这是个过渡形态(ttyd 只绑回环,前面挂同一个 app),端口冲突和按端口记录的麻烦都还在。

## 4. 生命周期仍归 `Tmuxd`

```
Tmuxd.__init__   起 ttyd(或接手)
t.asgi()         造一个转发 app,读的是 t 此刻的 ttyd 地址
Tmuxd.close()    收 ttyd(只收自己起的)
```

ASGI app 活得比 ttyd 久没关系:ttyd 没了它回 502。

**宿主要保持单 worker。** 多个 worker 各自 `Tmuxd(...)` 会接手同一个 socket 上的 ttyd
(§3.1 的复用规则同样适用),这本身是安全的;但谁起的 ttyd 谁收,
起它的那个 worker 一重启,别的 worker 手里的窗就断了,直到有人重新构造。

## 5. 和"不做反向代理"那条的关系

[01 §2](01-library.md)、[02 §3](02-session.md)、[README ⑤](README.md) 都说过:
**没有反向代理、没有自己发明的路径、没有 302。** 这条没破:

- **核心仍然只报 URL。** `listen` / `base_path` 是 ttyd 自己的参数(`-i` / `-b`),
  `?arg=` 仍然是 ttyd 原生的;没有 `/s/<id>/`,没有跳转,没有解析 ttyd 的协议。
  这也是 `*muxd` 规范 M11(「组件只报 URL,要不要套一层网关是上层的事」)的原话;
- **转发器是替上层备好的网关零件,放在 extra 里。** 它不自己决定任何事 ——
  挂不挂、挂在哪、谁能过,都是宿主说了算。不装 extra、不 mount,tmuxd 和以前一样;
- 那几条"不做"反对的是**库自己在人和 ttyd 之间插一跳**。这里插那一跳的是宿主,
  用的是宿主的端口和宿主的门;tmuxd 只是把零件递过去,省得每个宿主各写一遍。

## 6. 为什么是 ASGI,不是 FastAPI router

ASGI 是调用约定(`async def app(scope, receive, send)`),不是框架:
FastAPI、Starlette、Litestar、Quart 都能 mount。`tmuxd.server.router` 是**控制 API**
(JSON 进 JSON 出,给 CLI 用),和这里的**窗流量**是两件事,不合并。

挂不上的只有 WSGI(Flask、同步 Django)—— WSGI 没有 WebSocket,谁实现都一样。

## 7. 考虑过、没选的

- **前面放 Caddy / nginx 反代 socket,`forward_auth` 回宿主鉴权。** Python 代码最少,
  但部署从一个进程变两个。`listen="unix"` + `base_path` 这一半对它同样适用,所以它仍是生产部署的可选形态;
- **不要 ttyd,在 ASGI 里自己开 pty 跑 `tmux attach`、自己讲 ttyd 的协议(或前端直接上 xterm.js)。**
  少一个子进程、少一跳;代价是终端协议、resize、流控都得自己维护。
  **接口先定成 `t.asgi()`,这条留作第二阶段** —— 以后换掉 `asgi()` 的内部,调用方不改;
- **ttyd 仍走 TCP、只绑 127.0.0.1,转发器连本地端口。** 改动最小,端口的麻烦都还在。
  转发器支持它,但只当过渡。

## 8. 要留神的

- **同源 iframe。** ttyd 的页面和宿主同源之后,它的 JS 读得到宿主页面的 localStorage(比如 Bearer token)。
  ttyd 前端是可信代码、xterm 不执行终端输出,风险低;要更紧就把 `/tty` 放到独立子域。
  `sandbox` 帮不上:ttyd 页面要 `allow-scripts` + `allow-same-origin` 才连得上 ws,两个同开等于没沙箱;
- **长连接。** 宿主的 ASGI server 要带 WebSocket 支持(uvicorn 是 `uvicorn[standard]`);
  前面再有代理时,空闲超时要放宽。
