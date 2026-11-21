# TETHYS · 潮汐计划（v2 重建版）

> 从自己的轨迹里学，**但先验证再重建**。

2026-11-20 重建：旧项目代码/状态丢失，幸存的是 `~/.dsh/AGENTS.md` 的规则注入块、`~/.dsh/sessions` 的会话轨迹、`memory/PROJECTS.md` 的状态记录。本版按 v0.2 规格重写（四模型评审 + qwen 拍板）。

## 定位

| 层 | 做什么 | 谁做 |
|---|---|---|
| **判定** | 从会话事件里数「真失败」（剔除假阳性/噪音/用户中断/宿主故障） | `classify.py`（唯一空白，必须自造） |
| **验证** | 回答「这条规则到底有没有用」——键和判决、A/B 对照、替代解释声明 | `verify.py` / `ab.py` |
| **口径** | 换尺子登记、单一来源、跨版本不混算 | `state/caliber.json`（classify/verify 强制引用） |
| **观察** | 看板、token 记账、调度、团队面板 | DSH 插件（token-metrics / agent-team-console / session-insights / cron） |
| **行动** | 规则注入 AGENTS | `apply.py`（幂等 + 备份 + 可回滚） |

**核心取舍**：只从轨迹里学，不从想象里学；质量优先于速度；买观察，自己写判定。

## 一轮怎么跑

```powershell
$py = "python"
$node = "node"
Set-Location "path\to\dsh-evolve"

node scripts\collect.mjs          # ① 采集：zstd 多帧 → data/raw/*.jsonl
& $py -X utf8 scripts\classify.py # ② 判定：事件锚点契约 + callId 去重 → data/signals/failReasons.json
& $py -X utf8 scripts\verify.py   # ③ 验证：规则效果判决 → data/verify/<date>.md
& $py -X utf8 scripts\ready.py    # ④ 门槛：数据够不够开轮次（新会话≥5 / 新调用≥2000 / 14 天兜底）
& $py -X utf8 scripts\apply.py    # ⑤ 注入：dist/AGENTS.evolve.md → ~/.dsh/AGENTS.md（幂等）
```

辅助：
- [前瞻 A/B 设计与运行手册](ab-design.md)：`python scripts/experiment_plan.py` 只复算可行性；`experiment_switch.py` 负责经授权后执行的整块开关。实验期间不要并行运行 `apply.py`、修改规则或用旧日报作中期判决。
- [规则审计总览](docs/audit-summary.md)：`audit_rules.py`（规则集审查：冗余/覆盖空白/理论收益）、`audit_behavior.py`（行为审计：规则行为是否发生）、`audit_families.py`（失败族聚类）、`audit_models.py`（跨模型失败率）、`audit_rule7_deepdive.py`（规则 7 深挖）、`audit_plugins.py`（插件健康度：使用率/失败归因/该卸）。
- `python scripts/ab.py start|check` —— 旧窗口的历史描述性对比，不记录实际开关，不作前瞻 A/B 结论。
- `python scripts/audit_sessions.py` —— 数据层普查（重复会话量化）
- `python scripts/apply.py --revert|--list` —— 回滚 / 查看注入状态
- `python -m unittest discover -s scripts/tests` —— 回归测试

## 首次验证结论（2026-11-20 快照：74 会话 / 10,323 调用（唯一 9,487）/ 真失败 290）

> 验证方法：**会话聚类事件率比较**（RR + 聚类 CI + Bonferroni 校正），见 [statistics-design.md](statistics-design.md)。旧阈值版报告留档 `data/verify/2026-11-20.md`。

| 规则 | 判决 | 证据 |
|---|---|---|
| 6 动手前确认存在 | **无显著** | RR 0.456，95% CI [0.158, 1.318]，调整后 p=0.43 |
| 7 读→改闭环 | **无显著** | RR 0.861，CI [0.436, 1.699]，p_adj=1（键和 30→30：`edit_no_match` 10→30 接盘） |
| 9 标识符查准 | **无显著** | RR 1.215，CI [0.332, 4.442]，p_adj=1（次数降但暴露同步降） |
| 5 工具异常分诊 | 转配置工单 | 33 次配置态失败剔除，不计规则效应 |
| 1/2/4 | 不可验证（无精确映射/口径类） | `exit_1_real` 76 次已可见（H4） |
| 3/8/10 | 不可验证 | 口径修正类 / 无单一 failKind |

**关键结论**：三条可验证规则全部**统计不显著**——不是规则没用，是**样本量不够**。样本量规划表显示：检测 50% 降幅需每臂 **~13,075 调用**（基线 5/千次），当前每臂只有 4.4K / 5.1K。要真正验证规则，需要攒更多数据或做前瞻 A/B。

## 规则审计（A–E + 深挖，详见 [docs/audit-summary.md](docs/audit-summary.md)）

- **规则 7 的「没执行」一半是测量误差**：窗口放大后真实「edit 前读同文件」率 **60.8%**（不是 33%）；`edit_no_match` 失败前 37.5% 读过同文件（= 文件变了，不是没读），失败后 62.5% 会重读——**失败分诊行为已在执行**。
- **失败构成**：环境/工具类约占 67%（exit_code 26.3% + read 15.7% + web 9.6% + 网络 5.5% + 配置/子代理 9.9%），规则能管的约 18%——规则策略有天然天花板。
- **规则 6 的行为确实被遵循**：read 前 glob 前缀 2.6%→8.6%（3.3 倍），但失败率未显著下降（行为改变未转化为效果）。

## 目录

```
dsh-evolve/
├── scripts/
│   ├── collect.mjs        采集（zstd 多帧解压 → raw jsonl）
│   ├── classify.py        判定器（事件锚点契约 + 去重 + 排除类）
│   ├── verify.py          验证器（键和判决 + 配置态剔除 + 替代解释声明）
│   ├── apply.py           注入器（幂等/备份/--revert/--list）
│   ├── ab.py              A/B 对照（整块开关/调用数下限/同签名断点）
│   ├── ready.py           频率门槛（含 14 天兜底）
│   ├── audit_sessions.py  数据层普查（重复会话量化）
│   ├── audit_rules.py     规则集审查（冗余/覆盖空白/理论收益）
│   ├── audit_behavior.py  行为审计（规则行为是否发生）
│   ├── audit_families.py  失败族聚类（长尾归族）
│   ├── audit_models.py    跨模型失败率对比
│   ├── audit_rule7_deepdive.py  规则 7 深挖（测量修正+失败链）
│   ├── audit_plugins.py   插件健康度审查（使用率/失败归因/该卸）
│   ├── restore_dist.py    从 AGENTS 注入块恢复规则真源
│   └── tests/             回归测试（58 项）
├── state/
│   ├── caliber.json       口径单一来源（classify/verify 强制引用）
│   ├── rule-effects.json  规则 → 失败类型映射
│   └── backup/            apply 前的 AGENTS.md 备份
├── dist/AGENTS.evolve.md  规则真源（10 条）
├── data/
│   ├── raw/               解压后的会话事件（74 会话，可重建）
│   ├── signals/           判定输出（failReasons.json）
│   ├── verify/            验证报告
│   ├── audit/             普查结果（重复会话）
│   └── ab/                A/B 基线
└── docs/                  设计文档
```

## 设计原则（v0.2 四模型评审结论）

1. **事件锚点契约**：一条 tool error = `tool/result` 首文本以 `Error:`/`错误：` 开头；`isError` 字段不可靠，不作主判据。判定必须事件结构解析，禁止行级正则（R52 实测行级匹配虚高 7 倍）。
2. **口径提权**：口径不是并列的第三个模块，是 classify/verify 强制 import 的单一来源（旧病灶：同一失败两套视图各读各的）。
3. **数据可重算的不存**：只存不可再生的（判决历史、口径版本）；raw 可随时重解压。
4. **多键规则以键和判**：失败在键间搬家不算有效（`file_not_read`→`edit_no_match` 是同一动作的出口迁移）。
5. **反配置态污染**：provider 歧义/跨域重定向/api key 等环境问题从真失败剔除，否则稀释所有效应量。
6. **频率跟数据走**：新会话 ≥5 / 新调用 ≥2000 / 新失败族 / 14 天兜底，任一满足才开轮次。

## 与旧版（3,500 行）的差异

- 砍掉：dashboard/ledger/propose 系统/trend/status/draft/gc/plugin 面板（观察层交给插件）
- 重写/恢复：collect（重写）、classify（新，去重）、verify（统计版）、apply（恢复）、rule-effects（重建）
- 新增：audit_sessions（数据层）、audit_rules/behavior/families/models/rule7（规则审计）、ab（历史窗口对比）、experiment_*（前瞻 A/B）、ready（门槛）、ready_check（每日检查）、backup（备份）、caliber.json（口径单一来源）、tests
- 代码量：约 1,500 行（含审计与实验脚本，不含测试）
