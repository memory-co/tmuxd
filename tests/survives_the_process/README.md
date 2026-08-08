# survives_the_process — 门面短命,屋子长命

## 这个场景在测什么

tmuxd 的**全部价值**押在一处不对称上:ttyd 是你进程的子进程,tmux server 谁的都不是。
所以你可以写一个只跑三秒的脚本,派完活就退出,而它派下去的会话继续跑。

这个场景锁的就是这条不对称的两端:

1. **进程没了,会话还在。** `kill -9` 掉持有 `Tmuxd` 的那个 Python 进程,
   会话照跑;新起一个 `Tmuxd` 接上去,不只是"看得见",**cwd 和 cmd 也还记得**
   —— 那部分 tmux 答不上来,是状态文件的活。
2. **进程没了,ttyd 得跟着走。** 绑生死用的是 `PR_SET_PDEATHSIG` 而不是 `finally`,
   因为 SIGKILL 之下没有任何 Python 代码会执行。所以这条**必须用 SIGKILL 验**,
   用 `terminate()` 验等于什么都没验。
3. **`close()` / `with` 收的是门面,不是屋子。** 上下文管理器退出、ttyd 挂掉、
   客户端全断开 —— 会话一律不动。**只有 `kill()` 销毁会话**,这是唯一的一条路。

## 不在这测什么

- **ttyd 被别的实例接手时该不该停** —— 那是端口复用的语义,在 [`port_reuse/`](../port_reuse/)。
- **`tmuxd stop` 之后会话还在** —— 同一条性质的 CLI 表述,在 [`cli_shell/`](../cli_shell/)
  连着退出码一起测。
- 会话内容本身(敲进去的字)—— 在 [`typing_in/`](../typing_in/)。

## fixture 来源

- `instance` / `served`(`tests/conftest.py`)
- 子进程那条用例自己 `subprocess.Popen` 一个真的 Python 进程,因为**要杀的就是它**;
  它把 ttyd 的 pid 打到 stdout 上让父进程盯着
