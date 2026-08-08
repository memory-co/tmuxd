# control_api — CLI 打的那个口

## 这个场景在测什么

**CLI 必须有一个 server 在跑**,这个场景测的就是那个 server 的口
([works/03-server.md](../../docs/v1/works/03-server.md))。
它的每条断言都在说同一件事:**这层不发明东西**。

1. **七个端点,每个对应库上一个方法。** 建 / 列 / 取 / 删 / 敲 / info / health。
2. **`info` 得把两个端口都报出来** —— ttyd 那个给人,管控口给程序,而且它们不同。
3. **`POST /api/sessions` 也是“有则接上”** —— 和 `t.session()` 同一个语义,
   不因为换了层壳就变成“每次新建”。
4. **错误体是库异常的投影**,不是另立一套:`errors.py` 里的 `code` 原样出现在响应里,
   CLI 再按 `code` 映射成退出码 —— 一套词汇走到底。
5. **`Idempotency-Key` 真的防重放。** 网络重试敲两遍是真实事故,尤其当那条命令是
   `terraform apply`。所以断言的是**屏幕上只出现一次**,而不只是“响应一样”。
6. **返回的 `url` 指向 ttyd 的端口,不是管控口。** 两个口两拨用户:API 答程序,
   URL 给人。这个最容易在实现里搞混,所以显式锁。
7. **路由不能贪。** `POST /api/sessions/a/rename` 必须是 404 —— 用 `{sid:path}` 的话
   它会被当成 id 为 `a/rename` 的会话,于是返回 405,看着像“方法不对”其实是路由吃错了。
8. **`router` 能挂进别人的 app**(链路 ①):嵌库的人不起 server,
   他们把 router 挂进自己已经在跑的那个。

## 不在这测什么

- **哪些路由不存在**(capture / run / stream / events / rename)—— 在
  [`nothing_reads/`](../nothing_reads/),那是守门测试,和“现有路由怎么工作”分开。
- **CLI 怎么用这个口** —— 在 [`cli_shell/`](../cli_shell/)。
- 会话语义本身(id 说了算、命令不存在会 exited)—— 在
  [`session_identity/`](../session_identity/);这里只验壳把参数原样递下去了。

## fixture 来源

- `api`(本场景内)—— 把 `router` 挂进一个**真的 uvicorn**,这正是 `tmuxd serve` 干的事;
  用例结束用 `should_exit` 停掉。不用 `TestClient` 是因为要连真的端口,
  才验得到“两个口不同”这类事
- `call()`(本场景内)—— 裸 `urllib`:**验一层壳的时候不该拿另一层壳当放大镜**
- 最后一条用例走 `TestClient`,验的是链路 ①(挂进别人的 app),不需要端口
- 整个场景在没装 `tmuxd[server]` 时 `importorskip` 跳过
