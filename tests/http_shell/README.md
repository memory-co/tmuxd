# http_shell — 可选的那层壳,以及它和库的关系

## 这个场景在测什么

HTTP 是**套在库外面的壳**,给同一个进程里够不着的调用方用。它的每条断言都在说
同一件事:**这层不发明东西**。

1. **默认不开。** 不调 `serve_http()` 就没有这个口 —— 这不是保守设置,
   是"大多数调用方本来就在同一个进程里"的直接后果。
2. **八个端点,每个对应库上一个方法。** 建/列/取/删/改名/敲/info/health。
3. **`POST /api/sessions` 也是"有则接上"** —— 和 `t.session()` 同一个语义,
   不因为换了层壳就变成"每次新建"。
4. **错误体是库异常的投影**,不是另立一套:`errors.py` 里的 `code` 原样出现在
   响应里,`RemoteTmuxd` 再按 `code` 还原成**同一个异常类**。所以本地和远程
   可以用同一个 `except NoSuchSession` 接住。这条是这层壳存在感最强的设计,
   用一条用例专门锁。
5. **`Idempotency-Key` 真的防重放。** 网络重试导致同一条命令敲两遍是真实事故 ——
   尤其当那条命令是 `terraform apply`。所以用例断言的是**屏幕上只出现一次**,
   而不只是"响应一样"。
6. **返回的 `url` 指向 ttyd 的端口,不是 API 的端口。** 两个口两拨用户:
   API 答程序,URL 给人。这个容易在实现里搞混,所以显式锁。
7. **鉴权**:`/api/health` 免票,其余一律要 Bearer。

## 不在这测什么

- **哪些路由不存在**(capture / run / stream / events)—— 在
  [`nothing_reads/`](../nothing_reads/),那是守门测试,和"现有路由怎么работа"分开。
- CLI 的 `-H` 远端模式 —— 在 [`cli_shell/`](../cli_shell/)。
- 会话语义本身(id 说了算、命令不存在会 exited)—— 在
  [`session_identity/`](../session_identity/);这里只验壳把参数原样递下去了。

## fixture 来源

- `api`(本场景内)—— 在 `instance` 上 `serve_http(free_port())`,用例结束停掉
- `call()`(本场景内)—— 裸 `urllib`,故意不用 `RemoteTmuxd`:
  验壳的时候不该用另一层壳当放大镜。`RemoteTmuxd` 自己的用例在文件末尾单独一组
