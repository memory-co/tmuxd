# 往里敲:send / keys

**tmuxd 只有这两条写入命令,而且它只写不读。**
没有 `capture`、没有 `run`、没有 `wait`、没有 `stream` —— 要看就让人打开那个 URL,
要程序化拿输出和退出码就用 `ssh`([为什么](../works/03-http.md))。

---

## `tmuxd send` —— 打字面量

```
tmuxd send -t ID TEXT [--enter]
```

```console
$ tmuxd send -t work "npm test" --enter
✓ sent
```

**一个字符都不解释。** 底层是 `tmux send-keys -l`,`--enter` 之后再补一个回车键。

---

## `tmuxd keys` —— 按键

```
tmuxd keys -t ID KEY [KEY...]
```

```console
$ tmuxd keys -t work C-c
✓ sent

$ tmuxd keys -t vim Escape : w q Enter
✓ sent
```

收的是 **tmux 的键名**:`Enter` `Escape` `Tab` `Space` `BSpace` `Up` `Down`
`C-c` `C-d` `M-x` `F1`…

---

## 为什么分成两条命令

因为 `tmux send-keys` 不加 `-l` 时会把参数当**键名**解析,于是这句话:

```bash
tmux send-keys -t work "Enter the code"      # tmux 原生
```

里的 `Enter` 变成了一个回车键。**这是 tmux 用户人人踩过一次的坑。**

tmuxd 把它挡在接口形状上,不给你混着写的机会:

```console
$ tmuxd send -t work "Enter the code"
✓ sent                                 # 打进去的就是这七个词
```

**要发文本用 `send`,要发按键用 `keys`。** 记不住也没关系 —— 写错的那条不会有歧义地
干出别的事,它只会把字面量打进去。

---

## `✓ sent` 是什么意思

**仅仅是"字符已经交给 tmux 了"。**

不是"命令跑完了",更不是"跑成功了"。这一层不读终端,所以它没有能力知道后续发生了什么。

想知道结果,两条路:

```bash
tmuxd url -t work -o          # 让人看一眼
ssh box 'cd ~/proj && npm test'   # 或者根本别用 tmuxd,用 ssh
```

`tmuxd send` 存在的理由只有一个:**你要投喂的那个会话,正有人看着**,
而且你希望他随时能接手。不需要这个性质的活,ssh 更直。

---

## 幂等与重试

CLI 这层**不做去重**:`tmuxd send` 跑两次就是敲两次。

要防"网络重试导致同一条命令跑两遍"(尤其当那条命令是 `terraform apply`),
用 HTTP 那层的 `Idempotency-Key`,或者在你自己的脚本里判重。

---

## 这里没有"安全"可言,也不假装有

不做命令白名单。**能打开那个终端的人本来就能敲任何东西**,
在命令行上拦 `rm -rf` 只会给人虚假的安全感,顺便挡住正当用法。

安全边界在 token 和网络那一层,不在这里。
