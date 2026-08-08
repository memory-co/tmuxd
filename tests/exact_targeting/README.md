# exact_targeting — `work` 绝不能打到 `workbench`

## 这个场景在测什么

tmux 的 `-t` **默认前缀匹配**。只存在 `workbench` 时:

```console
$ tmux send-keys -t 'work:' -l -- 'BBB'
$ echo $?
0                       # ← 成功了,字符静默进了 workbench
```

**退出码 0,没有任何警告,按键落在了另一个终端里。** 在浏览器场景下这意味着
你以为在给 A 投喂,实际打进了 B —— 而 B 里可能正跑着别的东西。

这个场景把这条路上的每个入口都钉死:

1. **`=` 前缀关掉前缀匹配** —— 但两类命令写法不同,少一个字符就出事:

   | 写法 | 只有 `workbench` 存在时 |
   |---|---|
   | `send-keys -t 'work:'` | **exit 0,打进了 workbench** |
   | `send-keys -t '=work'` | `can't find pane` —— 解析不了,`=id` 不是合法的 pane 目标 |
   | `send-keys -t '=work:'` | `can't find session: work` ✅ |
   | `has-session -t 'work'` | **exit 0**(前缀匹配) |
   | `has-session -t '=work'` | exit 1 ✅ |

   所以 **session 类命令用 `=id`,pane 类命令必须写 `=id:`**。这条规则只写在
   `tmux.py` 一处(`target_session` / `target_pane`),这里连规则本身一起锁。
2. **库的每个入口都不前缀匹配**:`has` / `get` / `send`。
3. **`attach.sh` 也不能**。它是 ttyd 那条路上的入口,`?arg=` 是调用方可控的字符串,
   前缀匹配意味着一个近似的 id 就能进别人的终端。

## 不在这测什么

- **`?arg=` 传了个根本没建过的 id** —— 那是创建收口,在 [`the_entrance/`](../the_entrance/)。
- 非法 id 的拒绝规则 —— 在 [`session_identity/`](../session_identity/)。

## fixture 来源

- `instance` / `served` / `screen`(`tests/conftest.py`)
- 直接对 `tmuxd.tmux` 模块调命令,用来锁 tmux 自身的行为 —— 这几条一旦
  在新版本 tmux 里变了,应该由这里先炸,而不是由用户在生产上发现
