# TETHYS 进化日志

> 只记「真的改变了什么」。v2 重建后（2026-11-20）重新开始。

- **2026-11-20 R53（v2 首轮）**：重建后第一轮离线——collect 74 会话（新解压 2）/ classify 真失败 290（callId 去重 934、配置态 33）/ verify 口径 v3 判决 / ready 判定 **NO-GO**（新增会话 0 < 5，数据不够开提案，只做离线）。**据「砚舟」会话（`f05559a0`，与 `28177500` 双目录副本）的实测失败补两条规则**：①规则 5 加「`rejected input ... must be an object` 参数格式错重试无用，按报错改传参」（砚舟会话 11-19 23:53~23:57 连重试 3 次全撞墙）；②规则 9 加「`cordis_inspect_query` 的 `input` 必须是对象（空 `{}` 也行），传字符串会被拒」，并扩 detectsPatterns 加 `rejected input` / `must be an object`。apply 注入 3974→4152 字符，rulesRevision `rules-25d988006d`→`rules-44f7341436`。**砚舟会话另一半失败是 web_search HTTP 401（key `****f87c` 失效）——用户侧换 key，不是 agent 行为问题。**

- **2026-11-20 R54（v2 第二轮，统计版首跑）**：数据增量——collect 74 会话（新解压 2）/ classify 调用 10,491（唯一 9,655，重复 941）/ 真失败 293（+3）。统计验证（会话聚类 RR + Bonferroni）：规则 6 RR=0.441（p_adj=0.38）、规则 7 RR=0.833（p_adj=1）、规则 9 RR=1.243（p_adj=1）——**全部无显著**（每臂约 4.4K/5.3K 调用，距离检测 50% 降幅所需的 ~6.9K 仍差一截）。**新失败族 1 个**：`session target is outside the caller workspace`（session_trace 查跨工作区会话被拒）——样本仅 1 次，按门槛**只观察不落地**；若再现 ≥2 次，考虑加环境事实「session_trace/session_event_search 只能查当前工作区」。

- **2026-11-20（第三环节）**：A/B 前瞻实验**决定挂起**——用户使用模式为「有灵感才用」，无法保证 84 天稳定强度（每臂需 6,945 调用），硬启动大概率以「信息不足」收场；`ab-design.md` 与 `experiment_*.py` 全部留档，等连续高强度使用期再启动（`experiment_switch.py init` 一条命令）。观察性验证保留（零成本，主要价值是防错+监控变差/新族，不是证明有效）。**新增每日自动检查**：Windows 任务 `TethysReadyCheck` 每天 10:00 跑 `ready_check.py`（错过开机补跑），结果写 `data/status.md`，GO 时桌面留标记（区分「数据量够」与「有新失败族」两种）。
