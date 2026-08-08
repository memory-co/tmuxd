# tmuxd 测试 — 按场景组织

每个子目录是**一个场景**,有自己的 `README.md`(在测什么 / **不在这测什么** /
fixture 来源)和 `test.py`。相关的用例合并在一个场景下,跟「按代码模块切文件」解耦 ——
`send()` 的字面量语义和 CLI 的 `send` 命令属于不同场景,而"会话活得比进程久"
这一条会同时用到库、ttyd 和子进程。

跑的是**真的 tmux 和真的 ttyd**,不 mock:这个项目的全部价值就在于它和这两个程序的
交界处,把它们换成假的等于什么都没测。每个用例拿到独立的 tmux socket(名字带用例名
和 pid),所以跑测试的人自己开着 tmux 也不受影响 —— 这本来就是 tmuxd 对用户的承诺,
测试自己先守住。

## 场景一览

| 目录 | 测什么 |
|---|---|
| [`survives_the_process/`](survives_the_process/) | **门面短命,屋子长命**:`kill -9` 掉持有 `Tmuxd` 的进程,会话照跑且 cwd/cmd 还记得;同一次退出里 ttyd 必须跟着走(`PR_SET_PDEATHSIG`,所以**必须用 SIGKILL 验**);`close()` / `with` 收的是门面,只有 `kill()` 销毁会话 |
| [`session_identity/`](session_identity/) | **id 说了算**:`session()` 是有则接上无则创建,对已存在的 id 再给 `cwd`/`cmd` 会被忽略;`get()` 只接不建;非法 id 拒绝而不静默改写;命令不存在是 `exited` 而不是错误码 |
| [`exact_targeting/`](exact_targeting/) | **`work` 绝不能打到 `workbench`**:先证明 tmux 默认真的前缀匹配(exit 0 + 字符进了别人的终端),再证明 `=id` / `=id:` 两种写法各自躲开了它;库的每个入口和 `attach.sh` 都不前缀匹配 |
| [`typing_in/`](typing_in/) | **唯一的写入动作**:`send()` 字面量(那句 `Enter the code` 不能变成回车)、前导横杠不是选项、`send_key()` 真的按下去了(`C-c` 打断 `cat`)、返回只意味着"字符交出去了" |
| [`the_entrance/`](the_entrance/) | **人从哪进来**:URL 就是 ttyd 原生的 `?arg=`,算它不需要活着的 Python 进程;token 由 ttyd 把关;`?arg=` 是调用方可控的,所以 `attach.sh` 在 pty 创建点只 attach、从不创建 |
| [`port_reuse/`](port_reuse/) | **端口上已经有 ttyd 了怎么办**:空着就起一个(归我管)、是自己人就接手(接手方 `close()` 不带走它)、是陌生人就 `PortInUse` 不猜不抢;外加"看得见门开着而不必自己开一个" |
| [`your_tmux_untouched/`](your_tmux_untouched/) | **装一个服务不该动到你手里正在跑的东西**:专属 socket、`socket="default"` 直接报错、server 不存在时 `sessions()` 是 `[]` 而不是抛、`external` 只列不收编、**GC 只删 JSON 永远不 kill** |
| [`nothing_reads/`](nothing_reads/) | **守门测试:断言“没有”**。没有 capture / run / stream / 事件流 / rename / 远程客户端,库也不自己起 HTTP server;**`import tmuxd` 不把 FastAPI 拖进来**(零依赖全靠这条)。要加,先改 `works/03-server.md` 的论证再让这里红掉 |
| [`control_api/`](control_api/) | **CLI 打的那个口不发明东西**:七个端点各对应一个库方法、`POST` 同样是有则接上、错误体是库异常的投影、`Idempotency-Key` 防重放(断言的是**屏幕上只出现一次**)、返回的 `url` 指向 ttyd 而不是管控口;外加“把 router 挂进别人的 app” |
| [`cli_shell/`](cli_shell/) | **CLI 离不开 server**:没起时如实说(而且报的是管控口不是 ttyd 口)、`-s`/`--id` 五种写法同路、`--` 是标点不是命令的第一个词(这条曾经真写错过)、退出码是接口、`stop` 停门面不停屋子 |
| [`installing/`](installing/) | **`tmuxd install` 与 `~/.tmuxd.json`**:**没有这个文件时行为不变**(它是辅助不是步骤)、json 里永远只有两个键(第三个键要报错 —— 库敢默认读它全靠这条)、记的路径失效要**降级并 warn 而不是抛**、校验和不符不留文件也没有 `--force`、1.7.4 及更早直接拒绝、非 root 不提权只打印命令 |

## 共享 fixture / helper(`conftest.py`)

- `instance` —— 一个**完整的** `Tmuxd`:独立 tmux socket、状态目录,以及一个真的 ttyd。
  没有“只起一半”的选项 —— `tmuxd = tmux + ttyd`,缺一个都不成立
- `served` —— `instance` 的别名,留着少改一堆用例
- `screen(t, sid)` —— 读屏幕。**库刻意不提供这个能力**,测试只好自己下到 tmux 那一层;
  这恰恰是那条设计线的注脚:测试是唯一真正需要读屏幕的调用方
- `wait_for(t, sid, needle)` / `wait_until(pred)` —— 终端是异步的,断言前必须等
- `isolated_toolchain_file`(autouse)—— 把 `TMUXD_JSON` 指到不存在的路径:这个文件的作用就是**改变查找结果**,开发机上真实的那份漏进来会让顺序类用例莫名其妙
- `free_port()` / `pool_name(request)` / `kill_pool(name)` —— 挑端口 / 起名 / 收尾
- `needs_tmux` / `needs_ttyd` —— 没装就跳过,不是失败

## 跑

```bash
pip install -e ".[dev]"             # 含 fastapi + uvicorn,控制口和 CLI 要用
pytest                              # 全部,约 45 秒
pytest tests/exact_targeting -v     # 单个场景
pytest -k prefix                    # 按名字挑
```

需要机器上有 `tmux`(≥3.0)和 `ttyd`。缺 ttyd 或缺 `[server]` 依赖时,相关用例自动跳过。

## 加新场景

1. 新目录 `tests/场景名/`,放 `__init__.py`
2. 写 `README.md`:**测什么 / 不测什么 / fixture 来源**。
   "不测什么"那节别省 —— 它是给下一个人看的路标,省掉之后同一条断言会在三个场景里各写一遍
3. 写 `test.py`,开头一行 `"""场景名 — 一句话. See README.md."""`
4. 不需要在任何地方登记 —— pytest 自动收集(`python_files` 已含 `test.py`)

用例名写成句子(`test_the_id_decides_not_the_payload`),失败时那一行本身就是报告。
