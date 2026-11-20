## 环境事实（TETHYS 自动习得示例）

> 这是示例模板：用 `apply.py` 前把它复制为 `dist/AGENTS.evolve.md`，按你的环境改写。

- **DSH 的 `pwsh` 工具在 Windows 上跑的是 Windows PowerShell 5.1**：用 `;` 分隔语句，不要用 `&&`；中文路径一律加引号。
- **`python` / `node` 可能不在 PATH**：先 `load_workspace_dependencies` 拿绝对路径；多行、含中文/正则/引号嵌套的脚本一律先落临时 `.py` 文件再跑。
- **工具输出约 50KB 封顶**：超长会被截断，先 `Select-Object -First 20`、`Get-Content -TotalCount 50`，确实要全量就先落盘再分段读。
- **`exit code 1` 常常是假阳性**：命令结尾若停在返回 False 的探测上，整条命令退出码就变 1；收尾补 `; exit 0`。
- **工具异常先分诊再动手**：宿主抽风（PTY 启动失败、自动审查拦截）与命令无关，原样重试 1 次；provider 配置错误（multiple usable web providers / 401 / 404 api key）重试无用，直接转告用户；`cross-origin redirect to <X>` 按报错给的 X 直接重抓；`rejected input ... must be an object` 参数格式错重试无用，按报错改传参。
- **动手之前先确认目标存在**：读文件前先 `glob` 定位；命令里引用路径先 `Test-Path`；调 CLI 前先 `Get-Command` 确认它在 PATH 里。
- **改文件走「读 → 改」闭环，`edit` 失败先分诊**：`file has not been read` 先 read；`file changed since it was read` 重读再改；`old_string was not found` 从最近一次 read 输出原样复制、禁止凭记忆重写；连续 edit/write 之后每次改完都要重新拿最新内容。
- **`app.asar` 里的路径搜不到**：`grep`/`glob` 底层是 ripgrep，进不了 `resources\app.asar\dsh\`；要读安装目录里的东西，直接用 `read` 工具。
- **调工具前先把标识符查准，别猜**：`cordis_inspect_query` 的 entry id、`send_message` 的队友名、Service 名先查再调；带必填参数先扫 schema；**`cordis_inspect_query` 的 `input` 必须是对象（空 `{}` 也行）**，传字符串会被拒。
- **长任务把约束写进文件**：上下文会被动剪除，路径、口径、验收标准落到 todo 或 scratch 文件，需要时重读。
