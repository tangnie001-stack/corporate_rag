## 1. 改前基线取证

- [x] 1.1 记录改前基线：`.venv/bin/python -m src.cli.check_docs --verbose` 的全量输出落档（供 §3.1 逐条 diff 对照），并记下 `13 篇文档，N error, M warn` 的计数
- [x] 1.2 记录改前耗时：`time .venv/bin/python -m src.cli.check_docs`（预期数百秒）
- [x] 1.3 记录改前的文件读取次数：以替换 `pathlib.Path.read_text` 的计数脚本统计对 `_SRC_DIR` 的调用次数（预期 ≈62,000，理论最坏 99,876），以及触发检索的符号出现次数
- [x] 1.4 `pytest tests/cli/ -q` 记录改前通过状态

## 2. 实现

- [x] 2.1 在 `src/cli/check_docs.py` 新增模块级快照 getter：遍历 `sorted(_SRC_DIR.rglob("*.py"))`，把每行经 `line.split("#", 1)[0]` 剥注释后的文本用 `\n` 连接成一个字符串；**lazy 初始化，单进程只读盘一次**
  - **必须复刻现状的异常语义**：逐文件 `try: read_text(encoding="utf-8") except OSError: continue`（跳过不可读 / 竞态删除的文件）。该钩子是 `always_run`、每提交必跑，若不复刻，一个不可读文件会让闸门从"跳过"变成**硬失败**
  - `UnicodeDecodeError` 按现状**不捕获**（有意保留，见 `design.md` D6），并在 docstring 写明
  - getter 校验快照来自当前根（`_SRC_DIR` 变化则重建）
- [x] 2.2 新增显式失效函数（清空快照），供测试与将来长驻进程使用
- [x] 2.3 改写 `_symbol_exists_in_code(symbol)` 函数体为「查快照 + 同一个正则」：`re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])")` 对整份快照做一次 `search`。**函数签名 `(str) -> bool` 与判定语义保持不变**
- [x] 2.4 更新该函数与模块 docstring：写明快照的等价性依据（符号不含换行 + `\n` 非 `[A-Za-z0-9]` + 剥注释逻辑逐行相同）与生命周期（进程级、无 TTL、不落盘）
- [x] 2.5 确认**不引入** `lru_cache`；若评审要求引入，须按 `design.md` D4 把 memo 并入同一份快照对象（禁止"函数对象 memo + 模块变量快照"两个失效点）
- [x] 2.6 确认 `_collect_code_routes` / `_collect_known_tool_names` / 锚点判定口径**未被改动**；并确认两处现状行为被**有意保留**：`#` 截断（`line.split("#", 1)[0]`）与 `except OSError` 不捕获 `UnicodeDecodeError`（取舍理由见 `design.md` D6）

## 3. 验证

- [x] 3.1 **等价性（硬门槛）**：改后 `--verbose` 全量输出与 §1.1 基线逐条 diff，`error` 与 `warn` 的**文档 + 行号 + 锚点 + 文案**必须完全一致，差异数为 0
- [x] 3.2 **性能**：`time .venv/bin/python -m src.cli.check_docs` 全量 ≤ 60 秒（规格阈值），并**记录实测值**。该阈值为**人工 / CI 复核判据**，**不新增耗时断言**（避免把机器性能引入测试导致 flaky）
- [x] 3.3 **边界用例（注释内符号）**：以临时 `_SRC_DIR`（tmp 目录）构造"标识符只出现在行内注释中"的场景，断言判为"未检索到"
- [x] 3.4 **边界用例（跨行不成立）**：构造相邻两行（前一行行尾与后一行行首拼起来恰为某符号），断言仍判为"未检索到"
- [x] 3.5 **守卫用例（确定性，非耗时）**：以替换 `pathlib.Path.read_text` 计数，在同一进程内连续多次调用符号检索（次数 ≫ 代码库文件数），断言对 `_SRC_DIR` 下 `.py` 的**读取次数不随调用次数增长**（至多等于文件数）。此用例守卫规格的「读取次数不随符号数增长」scenario
- [x] 3.6 **测试隔离**：在 `tests/cli/` 加 autouse fixture，每个用例后调显式失效函数 —— pytest 会话内快照会跨用例存活，而 §3.3 会替换 `_SRC_DIR`，不隔离会污染其他用例
- [x] 3.7 `pytest tests/cli/ -q` 全绿（含 §3.3–3.6 新增用例）
- [x] 3.8 质量门禁：`ruff check .` 无错误、`ruff format` 已跑、`pyright src/` 不新增 error

## 4. 收尾

- [ ] 4.1 端到端：一次真实 `git commit`，确认 pre-commit 的 `doc anti-rot (all docs)` 钩子耗时降到秒级
- [ ] 4.2 归档：`openspec archive check-docs-symbol-lookup-perf` —— 把 `documentation-anti-rot` 的 delta 合并进主规格
- [ ] 4.3 **登记本次有意保留的两处现状行为**（不修，仅登记；取舍理由见 `design.md` D6）
  - [ ] 4.3.1 `#` 截断（字符串字面量里的 `#` 被当注释起点 → 只产生 `warn` 噪声）→ 登 `docs/agents/requirements_pool.md`
  - [ ] 4.3.2 `UnicodeDecodeError` 不捕获（单个非 UTF-8 的 `.py` 可**挡死全员所有提交**）→ 登 `docs/agents/defensive-patterns.md`（缺陷类别）**并**登 `docs/agents/requirements_pool.md`（待修项，约 1 行 `except`）
- [ ] 4.4 cookbook 的 `.venv` 补充：作为**与本 change 无关的独立修复**，在本分支上单独一笔提交（message 标明独立）——「并行会话（worktree）」步骤补"必须同时 symlink `.venv`"（pre-commit 本地钩子 entry 是 `.venv/bin/python -m src.cli.check_docs`，只带 `.env` 不带 `.venv` 会让该 worktree 的每次提交都被 doc 闸门挡死）
- [ ] 4.5 合并回 `dev-wsl`（**本地，不 push**）
- [ ] 4.6 **合并后**在主工作区跑一次图谱增量更新，并核对 `src/cli/check_docs.py` 节点的**入边完整** —— 来自未变更的 `tests/cli/test_doc_consistency.py` 的 `calls` 边不得被丢（本分支内**有意不跑**图谱增量，故 worktree 的提交不含 `.ua/*`）；判据 `丢失=0 且 新增=0`（见 `docs/agents/knowledge-graph.md` 已知坑 #2）
- [ ] 4.7 清理：`git worktree remove` worktree；删除本地分支 `perf/check-docs-symbol-lookup`
