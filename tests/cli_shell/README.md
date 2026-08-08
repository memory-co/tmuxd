# cli_shell — 命令行:每条命令就是一次库调用

## 这个场景在测什么

CLI 是第三层壳,本地跑时**直接 `import tmuxd`**,不走 HTTP。所以这里要锁的不是
"功能对不对"(那在各自的场景里已经锁过了),而是**壳本身**的几件事:

1. **一个 id 只有一个规范名字**:`-t` / `--id` 是规范写法(和库、HTTP、webmuxd 同名),
   `-s` / `--session` / `--target` 是 1.0.0 的拼法,**必须继续能用但不出现在 `--help` 里**
   —— 已经发到 PyPI 的参数名删不得,而规范写法要显眼。
2. **参数真的递下去了**:`-t` / `-c` / `-e` / `--` 后面的命令。
   尤其是 `--` —— `argparse.REMAINDER` 会把分隔符本身一起交回来,
   不剥掉的话 `new -t x -- cat` 实际在跑命令 `-- cat`。**这条曾经真的写错过。**
3. **退出码是接口**,不是装饰:脚本靠它判断,不该去解析输出。
   `has` 用 3 表示"不在"——那是答案不是错误。
4. **错误走 stderr**,stdout 上只有正常输出,这样 `tmuxd url -t x | xargs open` 才安全。
5. **`serve` / `start` / `stop` 是同一份逻辑套不同的壳**,而 `stop` 停的是门面 ——
   会话还在。这条在 [`survives_the_process/`](../survives_the_process/) 从库那边验过,
   这里从命令行这边再验一次,因为**用户读到的是 CLI 打印的那句话**。
6. **配置文件只是构造参数的另一种写法**,不是第二套东西。
7. **`-H` 远端模式能做的事少一档**:`serve` / `start` / `stop` 在那下面直接拒绝,
   因为进程生命周期是对面那台机器的事。

## 不在这测什么

- **哪些子命令不该存在** —— 在 [`nothing_reads/`](../nothing_reads/)。
- 会话语义本身 —— 在 [`session_identity/`](../session_identity/)。
- `send` 的字面量语义 —— 在 [`typing_in/`](../typing_in/);这里只验 CLI 把文本
  原样递下去了(用的还是那句 `Enter the code`)。

## fixture 来源

- `run`(本场景内)—— 直接调 `cli.main(argv)`,不 spawn 子进程:
  壳的逻辑在进程内就能验完,起子进程只会让失败更难读。它给每个用例配好
  独立的 `-L` 实例名和 `--state-dir`,并把 `TMUXD_CONFIG` 指向一个不存在的文件,
  免得跑测试的人自己的 `~/.tmuxd.conf` 掺进来
- `capsys`(pytest 内置)—— 抓输出
- daemon 那两条用例真的 spawn 后台进程,因为**要验的就是它能不能起来又停下**
