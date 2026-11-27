# 失败归因报告（2026-11-27 · 18:35 快照）

> **数据源**：`data\signals\failReasons.json`（`generatedAt: 2026-11-27T18:35:15`，`caliber-2026-11-24-1`）。
> **方法**：沿用 `docs\c-attribution.md` 的归因单位——按「能不能一次性修掉」分组，而非按 kind 排名；所有数字用当前快照重算。
> **计算方式**：本报告所有次数均为 `real` 对象对应键值求和；分组由只读脚本 `_tmp-failure-triage\calc.py`（项目外临时目录）的 `classify()` 函数判定，每个 kind 恰好归入一组；百分比 = 组次数 ÷ 419。未运行 collect / classify / verify，未改 `scripts\` 任何文件。
> **交叉验证**：`sessionTimeline` 78 个会话的 `realFails` 合计 = 419，逐 kind 与 `real` 差异数 = 0；`calls` 合计 = 12,624 = `uniqueCalls`。数据自洽。

---

## 一、概览

| 指标 | 数值 | 怎么算的 |
|---|---:|---|
| 真失败总数 | **419** | `sum(real.values())`，89 个 kind |
| 会话数 | 99（其中 78 个有调用记录） | `sessions` 字段；`sessionTimeline` 长度 78，其余 21 个无调用也无失败样本 |
| 唯一调用数 | 12,624 | `uniqueCalls` 字段（与 `totalCalls` 13,458、`duplicatedCalls` 995 非简单减法关系，见局限 §5.8） |
| 总体失败率 | **3.32%** | 419 ÷ 12,624 |
| 会话失败率 | 均值 3.54% / 中位 2.53% | 对 78 个会话各算 `realFails ÷ calls` 再取均值/中位 |
| 时间跨度 | 2026-10-21 → 2026-11-27（38 天） | `sessionTimeline.createdAt` 与 `samples.time` 的 min/max |
| 已剔除项 | 175 | `excluded`：clean_exit 146 + tool_aborted 15 + host_fault 11 + user_cancelled 3 |
| 本次新增 kind | 7 个，各 1 次，共 7 次 | `newKinds` 与 `real` 取交集 |

**核心结论：C 环境治理路线的实际 ROI 仍然很低。**

| 组 | 含义 | 次数 | 占比 | 能一次性修掉吗 |
|---|---|---:|---:|---|
| A | 环境/工具坑（asar） | **11** | 2.6% | **能**（规则已补，待观察） |
| B | 配置/凭据态 | **39** | 9.3% | 能，但修法在用户/设置侧，且历史发生 ≠ 当前仍缺 |
| C | 行为类 | **179** | 42.7% | **不能**——规则大多已写对，问题是执行率 |
| D | 外部网络/目标站 | **47** | 11.2% | 不能 |
| E | 产品/权限/宿主约束 | **17** | 4.1% | 不能（agent 侧） |
| F | 业务失败 | **126** | 30.1% | 不该修 |
| 合计 | | 419 | 100% | |

与上一轮归因（`c-attribution.md`，基于同日 13:57 快照，真失败 398）相比：总数 +21，主要 kind 变化为 `exit_1_real` 119→124、`edit_no_match` 45→47、`file_read_missing` 27→31、`web_fetch_fail` 35→35。**注意口径**：旧文档另有「剔配置态后 349」一说（398 − 49），本快照 `excluded` 中无配置态分项，419 与 398 同为「真失败」口径，不应与 349 直接比较。旧数字引自文档，非本次重算。

---

## 二、按 kind 的分布

### 2.1 出现 ≥2 次的 kind（34 个，合计 364 次，占 86.9%）

次数直接取 `real` 键值；kind 名去掉 `tool_error::Error: ` 前缀并截短路径，完整键见源文件。

| kind（缩略） | 次数 | 组 |
|---|---:|---|
| `exit_1_real` | 124 | F |
| `edit_no_match` | 47 | C |
| `web_fetch_fail` | 35 | D |
| `file_read_missing` | 31 | C |
| `file_not_read` | 12 | C |
| multiple usable web providers (deepseek-fetch-api, http) | 9 | B |
| grep search failed …`AppData\Local\Programs\Dee`（asar） | 9 | A |
| `file_conflict` | 9 | C |
| OpenAI Codex search is signed out | 7 | B |
| Config.listConfigs rejected: "input" must be an object | 6 | C |
| grep search failed …`.dsh\profiles\desktop\node`（路径不存在） | 6 | C |
| seedream editing failed: ModelNotOpen | 6 | B |
| REMOTE_DISCOVERY_FAILED | 6 | B |
| cross-origin redirect → investor.asml.com | 5 | C |
| `read_out_of_range` | 4 | C |
| `tool_timeout` | 4 | D |
| sandbox: file access denied (workspace-write) | 4 | E |
| multiple usable web providers (deepseek-official, …) | 3 | B |
| cross-origin redirect → cn.bing.com | 3 | C |
| target index URL is required | 3 | C |
| invalid arguments: missing "description" | 3 | C |
| modlens: No vision provider | 3 | B |
| unknown entry id "llm-pi-ai" | 3 | C |
| grep search failed …`dsh-evolve\scri`（路径不存在） | 2 | C |
| read_image returned invalid output (originalDimensions) | 2 | E |
| subagent "qwen" is unavailable | 2 | C |
| subagent "kimi" is unavailable | 2 | C |
| glob search failed …`Temp\WinSAT`（拒绝访问） | 2 | E |
| DeepSeek API error (HTTP 401) | 2 | B |
| invalid arguments: missing "agent" | 2 | C |
| unknown tool "bash" | 2 | C |
| Team member limit 8 reached | 2 | E |
| unsupported content type "unknown" | 2 | D |
| glob search failed …`AppData\Local\Programs\Dee`（asar） | 2 | A |

其余 **55 个 kind 各出现 1 次**（合计 55 次），完整清单见源文件 `real` 对象；分组归属见 §2.2 与附录脚本。

### 2.2 分组汇总（每组次数 = 组内 kind 键值之和）

- **A = 11**：grep asar 9 + glob asar 2
- **B = 39**：web providers 9 + 3、Codex 7、seedream 6 + 1、Remote 6、modlens 3、401 2、Gemini key 1、Image credentials 1
- **C = 179**：文件操作 103 + 参数/标识符 39 + 搜索行为 13 + cross-origin 17 + 队友/子代理 7（细分见 §4.1）
- **D = 47**：web_fetch_fail 35 + tool_timeout 4 + DeepSeek 404 3 + unsupported content type 2 + non-public IP 2 + cdn terminated 1
- **E = 17**：sandbox 4 + read_image schema 2 + WinSAT 2 + Team limit 2 + LLM/route not allowed 3 + console.table 1 + session storage 1 + ReplaceFileW EIO 1 + design-studio not listed 1
- **F = 126**：exit_1_real 124 + exit_code_3 1 + Cannot find module 1

### 2.3 会话集中度

按 `sessionTimeline.realFails` 降序取前 10：**合计 211 次，占 419 的 50.4%**。失败高度集中——一半失败来自 10 个会话（78 个有调用会话中的 12.8%）。

| 会话（缩略） | 日期 | 失败/调用 |
|---|---|---:|
| session-c50c77de… | 11-09 | 29 / 1432 |
| session-be856da4… | 11-24 | 29 / 734 |
| a799961e… | 11-12 | 26 / 278 |
| session-87d2a863… | 11-24 | 26 / 396 |
| session-a749845d… | 10-31 | 20 / 367 |
| session-25a9bf59… | 11-04 | 18 / 704 |
| session-f66385ea… | 11-08 | 17 / 529 |
| 18379f3b… | 11-12 | 16 / 181 |
| session-f1fa7bb0… | 10-28 | 15 / 559 |
| session-87c90760… | 11-19 | 15 / 283 |

56 个会话有失败（占 78 个有调用会话的 71.8%）；22 个会话零失败。

---

## 三、能修的模式

### 3.1 A 组：asar 内 grep/glob（11 次，2.6%）——唯一确定的「一次性可修环境坑」

`app.asar` 是单文件，`read` 有专门支持，但 `grep`/`glob`/`Get-ChildItem` 一律失败。证据（`samples` 原文）：

```text
Error: grep search failed (exit 2): rg: %USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\resources\app.asar\dsh: IO error for operation on …
Error: glob search failed (exit 2): rg: …\app.asar\dsh\node_modules\@deepseek-ai: IO error …
```

> 原文为真实绝对路径，此处用户名打码（公开仓库不带本机路径）。

- 次数构成：grep 9 + glob 2 = 11（`real` 中两个 `…AppData\Local\Programs\Dee` 键）。
- **修法已落地**：当前 AGENTS 规则已含「asar 是单文件，glob/grep/Get-ChildItem 一律失败，只能 read 已知路径」。样本时间最晚 11-24，早于本轮规则修订（11-27），**修复效果需后续快照观察，本报告不能断言已生效**。
- 与旧报告差异：旧文档称 asar 相关 14 次（grep/glob 8 + `exit_1_real` 内 `Get-ChildItem` 6）。本快照 grep/glob asar 增至 11，但 `exit_1_real` 内的 6 次无法从本快照分离验证（见 §5.1）。**可确认的 asar 坑 = 11，不是 14。**

### 3.2 B 组：配置/凭据态（39 次，9.3%）——能修，但修法不在 agent 手里

| 子类 | 次数 | 构成 | 修法 |
|---|---:|---|---|
| 本地配置可修 | 19 | web providers 冲突 12、modlens 无 vision provider 3、401 密钥失效 2、Gemini key 1、Image credentials 1 | 设置里显式指定 provider / 填 key / 装 Antigravity CLI |
| 需登录或开通 | 20 | Codex signed out 7、seedream ModelNotOpen 6 + InvalidEndpoint 1、Remote discovery 6 | `dsh openai-codex login` / 开通模型服务 / Remote 登录与 host 在线 |

**为什么仍归「能修」但要打折**：
1. 这些是**历史累计**失败。快照只记录「发生过」，不记录「现在还缺不缺」。401 的密钥可能早已换好，Codex 可能早已登录——把 39 次全算成「待修收益」会虚高。
2. 修复动作在用户/设置侧，agent 改规则改不掉它们；agent 能做的只是「遇到这类错先报告、不盲重试」（R3 已覆盖）。
3. seedream ModelNotOpen、Remote host 离线属外部服务状态，用户也未必能单方面修。

**口径说明**：任务描述称「配置态已剔除」，但本快照 `real` 仍含上述 39 次配置/凭据类错误，`excluded` 中无配置态分项。本报告不二次剔除，单列 B 组呈现；「配置态」的官方判定边界以 `classify.py` 为准（本次未运行，无法核验）。

### 3.3 行为组里「规则已写对」的部分——理论上可修，实际不算「一次性」

C 组 179 次中，绝大多数对应的规则**已经存在**（R5 编辑前重读、R4 路径先核对、R3 停止无效重试、参数类型核对、动态标识符从清单取）。它们不是「规则空白」，而是「执行率缺口」。补规则措辞的边际收益趋近于零——旧报告对 `edit_no_match` 的判断（「规则 R5 已写对，问题是执行率」）在本快照依然成立，且该 kind 从 45 涨到 47。**这类只能靠行为层审计盯，不属于「一次性修掉」的范畴**，故归入 §4.1。

---

## 四、修不了的

### 4.1 C 组：行为类（179 次，42.7%）——最大的一组，规则治不了

| 子类 | 次数 | 构成 | 为什么修不了 |
|---|---:|---|---|
| 文件操作行为 | 103 | edit_no_match 47、file_read_missing 31、file_not_read 12、file_conflict 9、read_out_of_range 4 | 凭记忆拼路径、默写 old_string、不重读就改——R4/R5 已覆盖，纯执行率问题 |
| 参数/标识符行为 | 39 | invalid arguments 9、unknown entry id 10、cordis rejected input 9、unknown tool 4、no catalogued Service 3、target index URL 3、session target outside 1 | 不查清单就猜名字、嵌套对象传成字符串（qwen 系已知坑）、漏必填字段——规则已写「动态标识符从清单取」「参数类型错改值再发」 |
| 搜索行为 | 13 | grep/glob 路径不存在 11、grep 输出超 20MB 上限 1、regex parse error 1 | 搜不存在的文件（os error 2/3）与 file_read_missing 同根；输出超限/正则错是操作失误 |
| cross-origin redirect | 17 | asml 5、bing 3、其余 9 站各 1 | 错误自带恢复指引（retry against that URL directly），R3 已写「改抓提示地址」；**是否最终重试成功本快照无法验证**（见 §5.5） |
| 队友/子代理不查清单 | 7 | subagent unavailable 5（qwen 2 / kimi 2 / seed 1）、teammate not found 2 | 给不存在/已结束的成员发消息，应先 `list_agents` |

细分加法核验：103 + 39 + 13 + 17 + 7 = 179 ✓。

**结论**：这 179 次没有「改一条规则就消失」的修法。唯一杠杆是行为层审计（如 `audit_behavior.py` 的 R5 执行率指标）持续盯，属于长期治理，不属于 C 路线的一次性收益。

### 4.2 D 组：外部网络/目标站（47 次，11.2%）

web_fetch_fail 35（fetch failed / Connect Timeout）、tool_timeout 4、DeepSeek API 404 3、unsupported content type 2、non-public IP 2（127.0.0.1 与 fandom 各 1）、cdn.dsh.so terminated 1。

目标站不可达、DNS/TLS、内容类型不支持——不是配置问题，重试无意义（R3 已覆盖），换源属任务策略。**把它算进环境治理收益会把 ROI 吹大**（旧报告这一判断在本快照依然成立）。

### 4.3 E 组：产品/权限/宿主约束（17 次，4.1%）

| kind | 次数 | 性质 |
|---|---:|---|
| sandbox denied (workspace-write) | 4 | 权限策略，需审批或换模式；本会话审批已禁用 |
| read_image invalid output (originalDimensions) | 2 | **DSH 产品缺陷**（输出 schema 与声明不符），需上游修 |
| glob WinSAT 拒绝访问 (os error 5) | 2 | 系统保护目录，路径正确也读不了 |
| Team member limit 8 | 2 | 产品硬上限 |
| LLM provider / child route not allowed | 3 | 会话模型选择策略限制 |
| console.table is not a function | 1 | run_code 沙箱能力缺失 |
| session history storage unavailable | 1 | 宿主存储故障 |
| ReplaceFileW EIO (Win32 1175) | 1 | 文件系统/外部软件干扰，偶发 |
| dsh-design-studio not listed in market | 1 | 市场目录未收录 |

agent 侧无一次性修法；其中 read_image schema 与 console.table 属产品缺陷，应反馈上游而非改规则。

### 4.4 F 组：业务失败（126 次，30.1%）——不该修

exit_1_real 124 + exit_code_3 1 + Cannot find module 1。

业务脚本报错、构建失败、git 操作失败是干活时正常会遇到的，**修不了也不该修**。但必须诚实指出：`exit_1_real` 是大杂烩，本快照**无法拆分**其中有多少是环境坑（旧报告曾拆出 `-c` 多行 8 次、asar 列目录 6 次）——见 §5.1。126 是「业务失败」的上界估计，真实业务失败可能略少（若其中仍藏有可归因环境坑）。

---

## 五、这次归因的局限

1. **`exit_1_real`（124 次，占 29.6%）无法拆分子模式——最大盲区。** 本快照该 kind 仅 3 条样本，`first_line` 分别为 `### tool_aborted samples: 5`、`[stderr]`、`[stderr]`，零归因信息。旧报告的「`-c` 多行 8 次、asar `Get-ChildItem` 6 次」拆分**无法用当前数据复现**。因此：A 组 11 次是「可确认」的环境坑下界；`-c` 多行这一可修模式本次**完全看不到**，不能断言它已消失。要补这个盲区，需带完整 stderr 的采集（本次禁止跑 collect，未做）。
2. **samples 每 kind 仅 1–3 条**（93 个 kind：55 个 1 条、11 个 2 条、27 个 3 条），不是全量证据。所有「样本显示……」的归因都是抽样推断，非逐条核验。
3. **kind 键被截断**：41 个键长度恰为 92 字符（如 `…non-public IP addr`、`…AppData\Local\Programs\Dee`）。同键可能混入不同错误：asar grep 9 次中仅 3 条样本可确认指向 `app.asar`，其余 6 次按同键前缀推断；`grep …\.dsh\profiles\desktop\node` 6 次的样本均指向已不存在的 `dsh-voynisa-voice`，其余 3 次同理推断。
4. **历史累计 ≠ 当前状态**。B 组 39 次配置/凭据失败是 38 天内的累计，无法从快照判断哪些已修复。样本时间最晚到 11-27（当天），说明配置类失败仍在发生，但具体哪几项当前仍缺，需实时查设置（本次未查）。
5. **无法验证恢复**。cross-origin 17 次、edit_no_match 47 次等「可恢复」失败，快照只记失败本身，不记后续是否重试成功。因此 C 组的「执行率缺口」是失败次数口径，不是「最终未解决」口径——实际影响可能小于 179。
6. **21 个会话无调用记录**（99 − 78），既不在 timeline 也不在 samples，无法判断是纯聊天还是采集遗漏；`badLines` 33、`orphanCalls` 2 提示采集存在少量噪声。
7. **分组是本报告的判断，非官方口径**。`classify()` 分组规则见附录脚本，与 `classify.py` 的官方 kind 判定是两层：kind 判定沿用快照（caliber-2026-11-24-1），「能不能一次性修掉」的分组是本次归因新增。cross-origin 归 C（有恢复指引而未用）而非 D（网络）是主观判断，若归 D 则 C=162、D=64，结论方向不变。
8. **与旧报告的口径差**：旧文档「398 / 剔配置态后 349」两个数并存，本快照 419 对应 398 口径；`totalCalls` 13,458 与 `uniqueCalls` 12,624 之差 834 ≠ `duplicatedCalls` 995，说明去重口径含跨会话合并，失败率分母用 uniqueCalls 更稳。

---

## 附录：计算可复现

- 脚本：`data\audit\triage-calc.py`（只读，仅打开 `failReasons.json`，不写任何数据文件）。
- 运行：`& python -X utf8 data\audit\triage-calc.py`（python 用 `load_workspace_dependencies` 返回的绝对路径）
- 分组规则（`classify()`，按匹配优先级）：
  - A：`search failed` 且键含 `AppData\Local\Programs\Dee`
  - B：multiple web providers / Codex signed out / HTTP 401 / modlens No vision provider / seedream / Gemini key / Image credentials / REMOTE_DISCOVERY_FAILED
  - D：web_fetch_fail / DeepSeek HTTP 404 / unsupported content type / non-public IP addr / cdn.dsh.so fetch / tool_timeout
  - E：Team limit / not allowed for this Session / child LLM route / sandbox denied / read_image invalid output / console.table / session storage / ReplaceFileW / design-studio not listed / WinSAT
  - F：exit_1_real / exit_code_3 / Cannot find module
  - C：其余（文件操作五 kind、invalid arguments、unknown tool/entry、no catalogued Service、rejected input、target index URL、subagent unavailable、teammate not found、session target outside、grep 输出超限/regex 错、cross-origin、其余 search failed）
- 核验输出：分组合计 419 = sum(real)；UNASSIGNED = 0；timeline 逐 kind 差异 = 0。

> 收束：账算完了——419 次真失败里，能一劳永逸修掉的环境坑只有 11 次（2.6%）；最大的一块 179 次（42.7%）是行为债，规则早写对了，欠的是执行。C 路线的收益别吹大，行为层的盯防才是主战场。