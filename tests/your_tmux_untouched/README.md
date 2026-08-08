# your_tmux_untouched — 装一个服务不该动到你手里正在跑的东西

## 这个场景在测什么

tmuxd 对 tmux 的全部依赖是"二进制在哪",然后**一律 `tmux -L` 开一套专属 server**。
你的 `tmux ls` 和它的 `t.sessions()` 两份清单永不相交。

这条不变量有两个方向,后一个更要紧:

- 对内:对账、无中生有、GC 都建立在"这个 socket 里的东西都是我开的"之上;
- **对外:一个库在你自己的 tmux 里改配置、往里敲字、GC 时盯着你那个跑了三天的会话
  —— 光是"它有能力这么做"就已经不可接受了。** 专属 socket 让这件事在物理上不可能。

具体锁这些:

1. **socket 名带前缀**,而且 `socket="default"` **直接报错** —— 那会把会话开进
   你自己在用的那个 server,不给这个机会。
2. **构造完不启动任何 tmux 进程**,`sessions()` 返回 `[]` 而不是抛。
   tmux 在 server 不存在时以 **exit 1** 报 `error connecting to ...`,
   把它当失败的话,一个刚构造的实例调 `sessions()` 就会炸 —— 这是实现上最容易写错的一处。
3. **`external`:有人绕过库直接开的会话**。列出来、标出来,**既不杀也不收编** ——
   不给它补一份状态文件假装是自己开的,那会把一个看得见的异常变成一个看不见的谎。
4. **GC 只删 JSON,永远不 kill。** 一个后台机制自作主张杀掉别人跑了三天的会话,
   是这类工具最不可原谅的行为。所以有一条用例专门把 `gc_ttl` 调成 -1
   (等于"全都过期了")然后断言活会话一个不少。
5. **`kill_tmux_server()` 只杀自己那个池。**

## 不在这测什么

- **`tmuxd stop` 不杀会话** —— 那是门面/屋子的分离,在
  [`survives_the_process/`](../survives_the_process/)。
- ttyd 端口归属 —— 在 [`port_reuse/`](../port_reuse/)。

## fixture 来源

- `instance`(`tests/conftest.py`)
- `external` 那条用例直接 `subprocess` 调 `tmux -L <池> new-session`,
  **故意绕过库** —— 要测的就是绕过之后会怎样
