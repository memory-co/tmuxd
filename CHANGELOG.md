# 更新日志

## 2.0.0

**这一版是有意破坏兼容的。** 1.0.0 发出去之后,设计上有几处被想清楚了,
与其留一堆兼容层,不如趁没人依赖时改干净。

一句话概括这一版做了什么:**把不该在这一层的东西都拿掉了。**

### 破坏性改动

| 没了 | 为什么 | 改用 |
| --- | --- | --- |
| `Tmuxd.serve_http()` | **一个库不该在你的进程里偷偷起第二个 HTTP server。** 嵌进来的人已经有一个 app 在跑了 | `from tmuxd.server import router` 挂进你自己的 app([SDK](docs/v1/sdk/tmuxd.md)) |
| `RemoteTmuxd` / `tmuxd.remote` | 它是个**像 `Tmuxd` 但不是 `Tmuxd`** 的东西 —— 方法名一样,却管不了 ttyd 生死、没有 `close()`。近似的东西比明显不同的东西更容易让人写错 | `ssh box tmuxd …`,或直接 `requests` 打那七个端点 |
| CLI 的 `-H` | 只是套在 `RemoteTmuxd` 外面的壳;`ssh` 覆盖它的全部用途,还白拿 ssh 的鉴权和审计 | `ssh box tmuxd …` |
| `Session.rename()` / `POST …/rename` / `tmuxd rename` | **id 是身份,不是标签。** 改名之后,调用方下次按同样规则算出的还是原来那个 id,却找不到现场 —— 正好破坏 id 存在的唯一理由 | `kill()` 掉再建一个 |
| `Tmuxd(port=None)` / `start_ttyd=False` | **`tmuxd = tmux + ttyd`,缺一个都不成立。** 之前拿 `port=None` 当"没有 ttyd 也能用"的安慰奖,等于把必需依赖说成可选的 | 没有替代 —— 构造就意味着一个完整的 tmuxd 在跑 |
| CLI 的 `-t` / `--session` / `--target` | tmux 里 `-t` 是 **target**,而 target 带着 `session:window.pane` 那套语法,这一层没有。借字母会借来错的预期 | **`-s` / `--id`**,每条要指名会话的命令都用它 |
| id 里的 `/` | ASGI 在路由前就把 `%2F` 解码回 `/`,带斜杠的 id 建得出来却取不回 —— **半能用比直接拒绝更糟** | 换个 id;`.` `:` 本来就不许 |
| 退出码 `5` | 曾是"连不上远端 tmuxd",`-H` 去掉后没有产出者 | **空着不复用** —— 已发出去的退出码不该改含义 |
| 标准库 `http.server` 实现 | server 已经是显式安装的可选件,那"零依赖"的理由就没了 | FastAPI + uvicorn,在 `[server]` extra 里 |

### 新增

- **`tmuxd serve` 与管控口。** CLI 是活几十毫秒的进程,持不住 ttyd 也持不住状态,
  所以它**必须**有个 server 可打。两个端口分工明确:`7681` 是 ttyd(给**人**开浏览器),
  `7682` 是管控口(给**程序**)。详见 [CLI · server](docs/v1/cli/server.md);
- **`tmuxd.server.router()`** —— 挂进你自己的 FastAPI 应用,鉴权/日志/CORS 全走你那套;
- **可选依赖 `tmuxd[server]`** —— 基础安装仍然**零运行时依赖**,
  因为嵌库那条链路一行 Web 框架都用不上;
### 还没做

- **自带 ttyd 二进制** —— 设计已经定了(PATH 优先、包里带 `x86_64` / `arm` 兜底,
  见[依赖设计](docs/v1/works/06-dependencies.md)),**代码还没实现**。
  这一版仍然要求你自己装 ttyd。

### 修的 bug

- **路由吃错了东西**:`{sid:path}` 会吞掉斜杠,`POST /api/sessions/a/rename` 被解析成
  id 为 `a/rename` 的会话、返回 405 —— 看着像"方法不对",其实是路由的问题;
- **`serve` 的启动横幅是块缓冲的**,管道或 systemd 里要等进程退出才看得见;
- **`tmuxd info` 在没有 ttyd 时打印 `ttyd None`**。

### 怎么迁移

```diff
-tmuxd send -t work "npm test" --enter
+tmuxd send -s work "npm test" --enter

-tmuxd new -s work                    # 以前只有 new 用 -s
+tmuxd new -s work                    # 没变 —— 现在所有命令都这样
```

```diff
-t = Tmuxd(port=None)                 # 只管 tmux
+t = Tmuxd(port=12345)                # ttyd 是必需的

-shell = t.serve_http(12346)
+from tmuxd.server import router      # 挂进你自己的 app
+app.include_router(router(t), prefix="/tmuxd")

-from tmuxd import RemoteTmuxd
-t = RemoteTmuxd("http://box:12346")
+# 用 ssh:ssh box tmuxd send -s work "..."
+# 或直接 requests 打 http://box:12346/api/sessions
```

**用 CLI 的话,现在要装 extra 并先起 server:**

```bash
pip install "tmuxd[server]"
tmuxd start
```

## 1.0.0

第一版。库 + CLI + 标准库写的 HTTP 壳。
