## Why

`src/cli/check_docs.py` 的符号锚点校验对**每一个**反引号标识符都重新遍历并读取 `src/` 下全部 `.py`，且无任何缓存 —— 它是 pre-commit 钩子 `doc anti-rot (all docs)` 的热点，而该钩子**每次提交都全量运行**。实测全量检查约 **497 秒**：3 篇文档样本触发 14,367 次 `read_text`，外推 13 篇 ≈ 62,000 次（理论最坏 99,876 次），174 个文件平均被读 ≈ 358 次（详见 `design.md`）。这把单次提交窗口拖到 4–7 分钟，与并行会话的提交互踩（本仓已实际发生：一次提交被该钩子以 `files were modified by this hook` 中止）。

## What Changes

- **符号检索改为单进程只读一次代码库快照**：把所有 `.py` 剥注释后的行拼成一个字符串（blob），对整体跑同一个正则。实测对拍 297 个符号出现（去重 191）**不一致 0 处**；全量耗时 ≈497s → ≈5.5s（**≈90×**）。
- `_symbol_exists_in_code(symbol)` 的**签名与判定语义不变** —— 仍为 `0 error / 27 warn`，每条 warn 的文档、行号、内容均不变。
- **新增 capability `documentation-anti-rot`**：把"文档防腐闸门**本体**"的契约写成可归档、可回归的要求 —— 路径 / 路由 / 符号三类锚点的单向口径、排除表来源、**单次快照**、全量耗时上限、判定不依赖读取次数。
- **不引入 `lru_cache`**：收益仅约 0.5s，却会引入"函数对象上的 memo"与"模块变量上的快照"两个失效点（理由见 `design.md` D4）。
- 不改 `_collect_code_routes` / `_collect_known_tool_names` —— 它们本来就只扫一遍，不随符号数放大。
- **有意保留两处现状边界行为**并登记为独立要件（本次不修）：(a) `#` 出现在字符串字面量内也被当注释起点（失败方向单一，只产生 `warn` 噪声）；(b) `UnicodeDecodeError` 不被捕获（单个非 UTF-8 的 `.py` 可挡死全员提交）。保留是为维持"改前 / 改后 0 差异"这条等价性证明的纯净；取舍与非对称严重度见 `design.md` D6。

## Capabilities

### New Capabilities
- `documentation-anti-rot`: 文档防腐闸门本体的校验口径与性能契约 —— 路径 / 路由 / 符号三类锚点的单向校验、排除表来源、单次运行只读一次代码库快照、全量检查耗时上限、判定结果不依赖读取次数。

  **归属边界**：`docs/openspec/specs/` 下**没有** capability 覆盖闸门本体；而 **skill 工具名锚点**的规则**已由 `skill-registry` 的「防腐校验」requirement 覆盖**（这是真实覆盖，不是误命中）。故本 capability **不重复**该条规则，只在正文中**引用**它。

### Modified Capabilities
- （无）判定行为不变。`skill-registry` 的「防腐校验」requirement **原样保留、不作修改**（新 capability 仅引用它）。

## Impact

- `src/cli/check_docs.py` —— 新增模块级快照 getter 与显式失效函数；`_symbol_exists_in_code` 函数体改为查快照。
- `tests/cli/test_doc_consistency.py` —— 新增用例：注释内符号、跨行不成立、**读取次数不随调用次数增长**（确定性守卫，非耗时断言）。
- `.pre-commit-config.yaml` 的 `doc anti-rot (all docs)` 钩子 —— 耗时下降，**配置不变**。
- 无新增依赖、无 API / 数据格式变更、无迁移、无破坏性操作。
