# TETHYS v2 内部结构（改动前必读）

本文件回答「项目内部是怎么长的」，`README.md` 回答「怎么用」。改代码前看这里。

## 数据流

```
~/.dsh/sessions/**/*.jsonl.zstd       原始会话（多帧 zstd，追加写）
        │ collect.mjs（按 zstd magic 切帧逐帧解压）
        ▼
data/raw/*.jsonl                      完整事件流（74 会话 ≈ 281 MB，可随时重建）
        │ classify.py（事件锚点契约 → 真失败判定 → callId 去重）
        ▼
data/signals/failReasons.json         真失败分布 + 每族样本 + 会话时间线
        │ verify.py（读 caliber.json 口径 + rule-effects.json 映射）
        ▼
data/verify/<date>.md                 规则判决（有效/观察到下降/无显著/变差/样本不足/不可验证/配置工单）
        │
        ▼
dist/AGENTS.evolve.md  ──apply.py──▶  ~/.dsh/AGENTS.md（tethys 标记块，幂等，可回滚）
```

旁路：
- `audit_sessions.py`：`data/raw` → `data/audit/duplicates.json`（重复会话量化）
- `ab.py`：`data/signals` + `data/ab/baseline.json` → `data/ab/result-*.md`（A/B 对照）
- `ready.py`：`~/.dsh/sessions` + `data/raw` + `data/signals` → GO/NO-GO

## 三模块契约

### 1. 判定器 classify.py
- **锚点**：`tool/result` 的 content 首个 text 以 `caliber.json` 的 `anchors.errorPrefix` 开头。`isError` 字段不可靠（R52 实测时有时无），不作主判据。
- **锚点扩展（H4）**：pwsh 失败 = 输出尾部 `[exit code: N]`（N≠0）且无 `Error:` 前缀；`clean_exit`（N∈{1,3} 且无错误信号）标假阳性排除。`exit_1_real` 族由此可见。
- **配对**：`tool/call` ↔ `tool/result` 按 `callId`/`toolCallId`。配不上的结果计数（unrouted）但不计失败。
- **去重**：`callId` 全局去重（`caliber.dedup`）——副本会话/子代理继承的重复调用只计一次。
- **排除类**：`host_fault` / `user_cancelled` / `tool_aborted`（模式在 `caliber.exclusion`，顺序敏感）。
- **输出**：`totalCalls`（含重复）、`uniqueCalls`、`duplicatedCalls`、`real`、`excluded`、`samples`、`sessionTimeline`（每会话 calls/fails/kinds，供 verify 切前后）。

### 2. 验证器 verify.py（v4，统计版）
- **输入**：`data/signals/failReasons.json` + `state/rule-effects.json` + `state/caliber.json`。
- **匹配**：规则 `detects`（精确）+ `detectsPatterns`（**子串**——kind 带 `tool_error::` 前缀，前缀匹配会恒 0，2026-11-20 实测）。
- **推断单位**：会话（簇），不把调用当独立样本——会话内允许重试相关。方差用比率估计线性化 + Welch–Satterthwaite t 近似。
- **主判据**：后/前事件率比 **RR** + 会话聚类 CI + 原始 p；**Bonferroni** 校正（家族 = verify:true 且非 config_ticket 的规则，未估计者也占名额）。
- **敏感性对照**：给定事件总数的条件 Poisson 精确区间（`exact_rr`），不替代主判决、不进第二个检验族。
- **判决**：调整后 p ≤ alpha 且 RR<1 →「有效（观察性下降）」；RR>1 →「变差（观察性上升）」；否则「无显著」（说明点估计方向，不作因果）。
- **退让条件**：每臂至少 10 个有调用会话、3 个命中会话（`caliber.statistics`）；退化数据判「样本不足」，主 CI/p 留空。
- **样本量规划**：`plan_calls` 按 Wald 设计近似给「检测 X% 变化需要每臂多少调用」（`caliber.statistics.planning*`）。
- **方法文档**：根目录 `statistics-design.md`；`verify:false` → 不可验证（`verify_reason`）；`verify_mode=config_ticket` → 转配置工单。
- **强制**：输出必须带「替代解释声明」（配置/网络/会话构成）；判定阈值从 caliber 读，不硬编码。

### 3. 口径器 caliber.json
- **单一来源**：classify/verify 在模块加载时强制读取，缺失即报错。
- **变更纪律**：改口径先改此文件 + 登记 `_变更历史` + 新旧并行回算一个窗口；报告顶部必须带口径版本号。
- **红线**：口径变更与规则采纳分开登记、分开看（防「改名成非失败 → 验证器忠实报告有效」）。

## 注入器 apply.py
- `dist/AGENTS.evolve.md` 是唯一真源；注入块用 `<!-- tethys:begin ... -->` / `<!-- tethys:end -->` 包围。
- 幂等：块存在则替换内容；内容无变化跳过写盘。
- 每次写盘前备份到 `state/backup/AGENTS.md.<ts>.bak`；`--revert` 删块；`--list` 显示条数/一致性。
- 注入后自检：块内容必须与 dist 逐字一致。

## 数据层 audit_sessions.py
- 重复会话识别：**两两比对 callId 序列前缀**（不能按 createdAt 分组——副本的 createdAt 可能不同，2026-11-20 实测 62c405ca 是 11-19 才出现的副本）。
- 判定：前缀重合 ≥90% 且 ≥10 条 callId。
- 输出：组列表（每对：会话 id、事件数、轮数、前缀条数、重合率、createdAt 差）。

## 前瞻 A/B（与历史窗口独立）
- 方案及故障处置见 [ab-design.md](ab-design.md)。`experiment_plan.py` 读冻结任务包估工期；`experiment_switch.py` 管预注册、锁、真实切换读回和回滚；`experiment_meta.py` 提取模型指令暴露佐证。`experiment.py seal` 固定到期后按配对窗口推断，保留一次终末结果。
- 不改 `classify.py` 的 callId 去重、配置态或 v4 的事件定义，不用 `ready.py` 的 GO 触发提前解盲。

## 规则审计（A–E + 深挖）
- `audit_rules.py`：规则集审查（键级冗余/覆盖空白/理论收益上限）。
- `audit_behavior.py`：行为审计（规则要求的行为是否发生；路径规范化+窗口有测量误差，见深挖）。
- `audit_families.py`：失败族聚类（关键词规则，未归类 4.1%）。
- `audit_models.py`：跨模型失败率（`model/selection` + `assistant/message.source` 跟踪，fallback 防 unknown）。
- `audit_rule7_deepdive.py`：规则 7 深挖（测量修正 + `edit_no_match` 失败链）。
- 全部为**描述性审计**，不作因果结论；汇总结论见 [docs/audit-summary.md](docs/audit-summary.md)。

## 插件健康度审查（F）
- `audit_plugins.py`：从 raw 统计每个工具/插件的调用数、失败率、失败归因；输出「活跃/静默/添乱」状态。
- 工具→插件映射：扫描第三方包 `tools.register({ name })` 注册 + 已知映射表（34 个工具）；扫不到的归「内置/未识别」。
- 局限：DSH **没有官方「工具→插件」归属信息**（dsh-plugin-prune 的 README 也承认这点），映射是尽力而为；纯 UI 插件不注册工具，测不到。

## 历史窗口工具 ab.py
- AGENTS 整块注入，单规则摘不干净 → 只做「整块开/关」粗粒度对照。
- `start`：记录基线（会话/调用/真失败分布）；`check`：对比同签名断点；每臂调用数下限 `MIN_CALLS`。
- 口径版本变化时拒绝比较（跨版本不混算）。

## 频率门槛 ready.py
- 门槛：新会话 ≥5 / 估算新调用 ≥2000 / 采集缺口 / 判定缺口 / **14 天兜底**（任一 GO）。
- 未达门槛：只跑离线采集+判定，不跑提案/验证/落地。

## 目录职责

| 路径 | 作用 | 生命周期 |
|---|---|---|
| `dist/AGENTS.evolve.md` | 规则真源 | 唯一真源，改规则只改这里 |
| `state/caliber.json` | 口径单一来源 | 强制引用，变更要登记 |
| `state/rule-effects.json` | 规则 → 失败类型映射 | 规则增删时同步 |
| `data/raw/` | 解压事件流 | 可再生，删了重解压 |
| `data/signals/` | 判定输出 | 每次 classify 覆盖 |
| `data/verify/` | 验证判决历史 | **不可再生，保留** |
| `data/audit/` | 普查结果 | 可重跑 |
| `data/ab/` | A/B 基线与结果 | 判决历史，保留 |
| `Temp/` | 临时脚本/草稿 | 可清理 |

## 关键决策记录（v0.2 评审）

- 判定器锚点契约先于一切（GLM）：没有契约，口径冻结是空转。
- 口径提权为单一来源（qwen）：不许做成并列模块，必须是强制引用。
- 数据可重算的不存（qwen）：这次数据全丢、幸存的恰恰是原始会话和注入块。
- 多键以键和判（qwen）：R52 拆键病灶在效果评估里的复发。
- 配置态剔除（qwen）：环境问题混进真失败会稀释所有效应量。
- 先验证再重建（用户 + 团队共识）：代码没了但规则活着，先给规则判决，再决定重建规模。
