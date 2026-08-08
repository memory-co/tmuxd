# nothing_reads — 守住那条"不读"的线

## 这个场景在测什么

tmuxd **不提供任何读取终端内容的接口**:没有 `capture`、没有 `run`、没有输出流、
没有录制、没有事件流。三层壳都没有。

这个场景**不测功能,测的是没有功能** —— 它是一道守门测试。

理由很实际:这几样东西看起来都"顺手就能加",而且每一样都有人会提。
一旦加进来,它带回来的就是当初砍掉它们的全部理由 ——
抓出来的是屏幕不是日志、受宽度折行、全屏程序只给一帧、拿退出码要往命令后面拼标记、
录制会把密码和 token 落到磁盘上、事件流要一条长连接和一堆漏消息的边界情况。

所以这里把"没有"写成断言:

1. **库**:`Session` 上没有 `capture` / `run` / `stream` / `resize` / `split` /
   `wait_for`;`Tmuxd` 上也没有。
2. **HTTP 壳**:这些路由一律 404,而且**一条 WebSocket 都没有**(`/api/events` 也 404)。
3. **CLI**:子命令表里没有 `capture` / `run` / `wait` / `stream` / `watch`。
4. **`RemoteSession` 的接口和 `Session` 完全一致** —— 远程那头也不会多出读的能力。

哪天真的要加,先改 `docs/v1/works/03-server.md` 的论证,再让这里的用例红掉 ——
**顺序反过来就是在悄悄扩大这一层的职责**。

## 不在这测什么

- **"那我怎么知道命令跑完没有"** —— 答案不在代码里:让人打开那个 URL,或者用 ssh。
  这属于文档([`docs/v1/sdk/session.md`](../../docs/v1/sdk/session.md) 末尾那张表)。
- 测试自己确实在读屏幕(`tests/conftest.py` 的 `screen()`)—— 那恰恰是这条线的注脚:
  **测试是唯一真正需要读屏幕的调用方**,而它下到了 tmux 那一层去读,没走库。

## fixture 来源

- `instance`(`tests/conftest.py`)
- 直接 introspect 类和 argparse 的 parser,不需要跑起来
