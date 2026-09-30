# ttyd_output — ttyd 的输出不能进一根没人读的管道

## 这个场景在测什么

ttyd 活多久就写多久日志(每个连接至少一行)。以前 `ensure()` 用 `stdout=PIPE` 起它,
只为启动失败时能读到报错;启动之后**没人再读那根管道**。内核管道 ~64 KiB 写满后,
ttyd 下一次写日志就阻塞在唯一的事件循环线程上:所有窗口卡死,新连接排在 listen 队列里没人接。

1. **ttyd 的 stdout / stderr 是状态目录里的日志文件**(`<state record>.log`),不是管道。
2. **日志写过 64 KiB 之后 ttyd 仍然应答** —— 直接把它灌到超过管道容量再发一个请求。
3. 启动失败时的报错仍然能从日志里读到(`TtydFailed` 带最后一行)—— 由 `finding_ttyd` / `installing` 覆盖。

## fixture 来源
`instance`(真 ttyd,TCP 端口);只在有 `/proc` 的系统上跑第 1 条。
