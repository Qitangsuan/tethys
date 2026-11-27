# C 环境治理 · 失败归因（2026-11-27）

> 目的：把真失败按「能不能一次性修掉」重新分组，而不是按种类排名。
> 口径：`classify.py` 官方判定（`caliber-2026-11-24-1`），配置态 49 次已剔除。
> 快照：97 会话 / 13,064 调用（唯一 12,230）/ 真失败 398 / 剔配置态后 349。

## 先说结论

**C 路线的实际 ROI 比预期低。** Top 三类失败里，只有约 6% 是「一次性可修的环境坑」：

| 失败族 | 次数 | 可修吗 | 说明 |
|---|---:|---|---|
| `exit_1_real` | 119 | **基本不能** | 是大杂烩：业务脚本报错、构建失败、git 操作失败、网络。只有 8 次 `-c` 多行、6 次 asar 列目录是可归因的环境坑 |
| `edit_no_match` | 45 | **不能** | 42 次 `old_string was not found`（文件变了 / 默写错），3 次歧义匹配。这是行为问题，规则 R5 已覆盖，执行率 64.2% |
| `web_fetch_fail` | 35 | **基本不能** | 39 处 `fetch failed` / `Connect Timeout`，是网络层（目标站不可达、DNS、TLS），不是配置问题 |
| `file_read_missing` | 27 | 不能 | 全是凭记忆拼路径 |
| `grep/glob search failed` | 14 | **能修一半** | rg 无法读 `app.asar` 内路径，8 次是 asar |

「环境/工具类占真失败 67%」这个旧结论要拆开看：**67% 里大头是网络波动和任务本身的失败，不是可以一劳永逸配好的东西。**

## 真正能修的：asar 相关（14 次）

`app.asar` 是 **单个文件**，不是目录。但工具层看起来像目录：

```powershell
Get-ChildItem : 找不到路径"…\resources\app.asar\dsh"，因为该路径不存在。
```

```text
grep search failed (exit 2): rg: …\app.asar\dsh\node_modules\…
```

`read` 工具能读 asar 内文件（有专门支持），但 `glob` / `grep` / `Get-ChildItem` 都会失败。

**已有规则只说了「直接 read」，没说「不能列目录、不能靠 glob 发现」。** 这是规则的空白。

## 第二个能修的：`python -c` 塞多行（8 次）

```text
File "<string>", line 20
  KWS = ['maxMembers', 'Team member limit', 'MEMBER_LIMIT', 'memberLimit', 'roster')
                                                      ^
SyntaxError: closing parenthesis ')' does not match opening parenthesis '['
```

现规则写的是「**简单单行可用 `-c`**」——「简单」这个词太软，实际执行时多行/嵌套引号也往里塞。
要么改成硬边界（只允许真正一行、且无引号嵌套），要么干脆禁掉 `-c`。

## 不建议修的

- **`web_fetch_fail` 35 次**：重试没有意义（R3 已覆盖「环境故障停止无效重试」），换源才是解法，但那属于任务策略。
- **`edit_no_match` 45 次**：规则 R5 已经写对了，问题是执行率。这类只能靠行为层盯着（`audit_behavior.py` 的 R5 指标），不是「治理」能解决的。
- **`exit_1_real` 里的业务失败**：gradle 构建失败、python 脚本跑挂——那是干活时正常会遇到的，修不了也不该修。

## 修法（本轮落地）

1. **R4 补一句**：asar 内不能列目录、不能靠 `glob`/`grep` 发现，只能 `read` 已知路径。
2. **R1 收紧**：`-c` 只允许真正单行且无引号嵌套；其余一律写 `.py` 文件再执行。

两处都是措辞微调，不新增规则条数（仍 7 条），`rulesRevision` 需要重算。

## 复盘：这次归因教了什么

- **按失败种类排名会骗人**。`exit_1_real` 排第一（119 次），但它是十几个不同原因混在一起；
  真正可修的只有 14 次，藏在第 8 名的 `grep search failed` 里。
- **「网络失败」不该进「环境治理」**。它看起来像环境，实际不可控；把它算进收益会把 C 的 ROI 吹大。
- **归因单位应该是「模式」不是「kind」**。kind 是判定口径，模式才是修法单位。
