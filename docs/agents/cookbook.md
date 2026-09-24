# Cookbook 操作记录协议

> 本文定义"操作记录"（cookbook entry）的规范。LLM 在开发中遇到可复用的操作流程时，按此协议自动追加记录到本文。本文是**记录协议**，不是具体操作清单；条目由遇到场景时补充。

## 什么时候记录

满足以下全部条件时，新建一条操作记录：

1. **可复用流程**：该操作以后还会再做（不是一次性动作）
2. **多步骤**：需要 2 步以上才能完成
3. **有踩坑细节**：存在参数、顺序、环境依赖等容易出错的地方

典型例子：跑 RAGAS 评估、新增分块策略、加 LLM provider、迁移文档、修复环境依赖。

## 不记录什么

- 一次性操作（如"删除临时文件"）
- 已在 CLAUDE.md / rules.md 中的规则（那是规则的"家"）
- 已在 chunking-issues.md / defensive-patterns.md 中的坑（那是踩坑的"家"）
- 不确定是否可复用的操作：先不记，出现第二次再补

## 记录格式

每条记录用固定模板，放在对应分区（新增分区按主题命名）：

```markdown
### <操作名>

**场景**：什么情况下需要做这个操作
**步骤**：
1. <第一步>
2. <第二步>
**验证**：怎么确认操作成功（命令/输出/现象）
**注意事项**：易踩的坑、环境依赖、参数含义
```

## 示例

### 跑一次 RAGAS 质量评估

**场景**：需要量化某个知识库的检索+生成质量
**步骤**：
1. 生成测试集：`python -m src.cli.eval_ragas --kb-id <kb_id> --generate --size 20`
2. 执行评估：`python -m src.cli.eval_ragas --kb-id <kb_id>`
3. 需要质量门禁时加 `--gate`
**验证**：检查输出 CSV/Markdown，确认 `eval_report` 表有记录
**注意事项**：需要 `DASHSCOPE_API_KEY`；测试集生成前会脱敏

### 采一次 prompt 度量（RAGAS + 三症状指标 + 段体积）

**场景**：改动 system prompt（段正文 / 模板）后记录当时的**绝对水平**，作为阶段性留档。
**步骤**：
1. RAGAS：`POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas --kb-id <kb_id> --testset-version <N> --output data/ragas/reports/<name>`
2. 三症状指标：`python -m src.cli.symptom_metrics --log-dir logs --out docs/tmp/<name>-symptoms.txt`
3. 段体积：从同批请求日志取 `section_chars` —— `grep -o 'section_chars={[^}]*}' logs/app_*.log | tail -20`
**验证**：留档文件里四类数据（RAGAS / 三症状 / 段体积 / 环境）都有值；`symptom_metrics` 的
`traces_total ≠ 0`（为 0 说明日志目录读错了）
**注意事项**：
- ⚠ **跑完先看 `contexts=` 是不是 0** —— 截至 2026-09-22，`eval_ragas` 不设置 `RequestContext`，
  `agent_finalize` 取不到检索上下文，导致 `context_precision` / `context_recall` 恒 0、
  `faithfulness`=nan（详见 `requirements_pool.md` 的 F-32）。**这几项在修复前不可用作质量结论。**
- 宿主跑必须 `POSTGRES_HOST=localhost`（`.env` 里是 compose 服务名 `postgres`，宿主解析不了）
- 症状指标的采集范围是**本机全量日志**（`logs/app_*.log` 自最初累计），不是仅本轮请求 ——
  留档时必须写明范围，否则数字会被误读成"本次改动的结果"
- `section_chars` **不含**日期行与态 A 第二条未绑定消息，占比估算系统性偏低属预期（见 `logging-rules.md`）
- 指标口径（分组键、分母）见 `logging-rules.md` 的「症状指标口径」一节

### 新增一个分块策略

**场景**：需要为新的文档类型定制分块方式
**步骤**：
1. 在 `src/chunking/` 新增策略实现
2. 用 `validate_chunks` 校验输出合法
3. 用 ChunkQualityScorer 评估质量
**验证**：`POSTGRES_HOST=localhost pytest tests/chunking/ -v` 通过（宿主侧须加前缀）
**注意事项**：分块结果受 embedding 2048 token 限制（见 defensive-patterns.md）

## 部署

### 让 .py 改动在本地容器生效

**场景**：改了 Python 代码，需要本地容器运行新逻辑
**步骤**：
1. 确认 `docker-compose.override.yml` 存在——docker compose 默认自动加载它，无需 `-f` 指定
2. 改完代码后执行 `docker compose restart app`，让进程重新 import（override 已挂载 `src/`，无需 `--build`）
3. 改了环境配置（端口/环境变量）用 `docker compose up -d --force-recreate app`
4. 改了依赖（requirements/pyproject）用 `docker compose build --no-cache app`
**验证**：`curl http://localhost:8000/api/health` 返回 ok；或 `docker exec corporate-rag-app grep <新符号> /app/src/...` 确认容器文件已同步
**注意事项**：
- override 仅挂载 `src/` 和 `tests/`，其他目录改动不会进容器（如 pip 安装的包需重建镜像）
- app 的 uvicorn 无 `--reload`（见 CLAUDE.md 常用命令），必须 restart 进程才能加载新代码
- 判断"代码改动是否已生效"先看 override：挂了 `src/` 则文件已同步只需 restart；未挂载才需要 `--build`
- **`restart` 只重建进程、不重读 compose 的 env / volumes / command**：改了这些必须 `docker compose up -d --force-recreate app`（`restart` 不生效）；改了依赖（`pyproject.toml` / requirements）必须 `docker compose build --no-cache app`，再 `up -d --force-recreate app`

### E2E / 验收前的进程代码前置条件

**场景**：跑 E2E 或任何验收类操作（上传 → ready → 提问 → 检索）之前，需要确认 app 容器加载的是当前代码
**步骤**：
1. 先 `docker compose restart app`（app 的 uvicorn 无 `--reload`，容器可能已陈旧数小时，早于最近的代码改动）
2. 再执行 E2E / 验收步骤
**验证**：`docker exec corporate-rag-app grep <本次改动的新符号> /app/src/...` 能命中，或比对容器启动时间晚于最后一次代码改动
**注意事项**：
- **不做这一步会看到静默不一致**：陈旧进程可能仍在跑旧存储路径 → API 报告成功（`document.status='ready'` 且 `chunk_count>0`），但**新存储里没有数据**（如 PG `chunks` 是 0 行）
- 定性是**操作疏忽（运维）**，不是代码缺陷；单 worker / 无 reload 是本项目有意的既定形态，不会为验收改成 reload

### ALTER TABLE 操作（PostgreSQL）

**场景**：`conversation_history` 表结构变更（如 session-process-replay 新增 process 列）
**前提**：表结构由 alembic 唯一链管理（根 `alembic/`，见 `docs/agents/code-map.md`「关系型存储（PostgreSQL）」）。常规变更应改 ORM 模型（`src/infra/db/models/`）后生成迁移；下面是一次性手工 DDL 的 PG 等价写法。
**步骤**：
1. 进 PostgreSQL 容器执行表结构变更，本例（session-process-replay）：
   ```sql
   ALTER TABLE conversation_history ADD COLUMN process TEXT NULL;
   COMMENT ON COLUMN conversation_history.process IS '过程事件JSON（历史回放）';
   ```
**验证**：`\d conversation_history`，或
   `SELECT data_type, is_nullable FROM information_schema.columns WHERE table_name='conversation_history' AND column_name='process';`
**注意事项**：PG 的 `TEXT` 无长度上限；可空列无需回填，代码对 NULL 容忍

## 调试

### SSE 帧级核对（trace_id 回放事件流）

**场景**：核对某次问答前端实际收到的 SSE 帧序（status/token/reasoning 逐帧），验证过程渲染或排查流式问题。**唯一输入是 trace_id**
**步骤**：
1. 凭据自取：测试账号在 `.env` 的 `TEST_ACCOUNT` / `TEST_PASSWORD`（勿写进任何会提交的文档）；token 过期或首次使用，自己登录换新：
   ```bash
   TOKEN=$(curl -s -X POST http://localhost/api/auth/login \
     -H "Content-Type: application/json" \
     -d "{\"account\":\"$TEST_ACCOUNT\",\"password\":\"$TEST_PASSWORD\"}" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")
   ```
2. trace_id → session_id：日志按 trace_id grep，任取一行，第四段 `|` 分隔的 `sess_*` 即 session_id：
   ```bash
   docker exec corporate-rag-app grep "<trace_id>" /data/logs/app_YYYY-MM-DD.log | head -1
   ```
3. 回放帧缓冲：
   ```bash
   curl -s "http://localhost/api/sessions/events?session_id=<SID>&after_seq=0" \
        -H "Cookie: token=$TOKEN" -N
   ```
   返回带 `seq` 的 SSE 帧流（event: status/token/reasoning/…，按到达顺序）
**验证**：正常生成后回放应看到 status/think 交错、末轮 token 连续、citation×N、model_info、done 的完整序列
**注意事项**：
- 帧缓冲是进程内存（`StreamingRunManager`），**终态后 TTL 300s、同会话新提问即清、容器重启全丢**——回放要在生成结束后 5 分钟内做
- 缓冲只保留该会话**最近一次**生成的帧
- 接口有属主校验：session 不属于该账号返回 404
- **密码/token 不写入任何会提交的文档**；`.env` 不在 git 跟踪范围

### 取证时不要截断读取

**场景**：为判断"某符号/路径/提交是否还在"或"最早已于何时引入"而去查证时。
本项目已**三次**因截断读取产出与事实相反或时点错误的结论（记在这里防止第四次）：

1. `sed -n 'X,+16p'` 取表格，表格有 20 行 → 漏掉尾部若干行，据此断言"某表从未登记某行"（实为早已登记）；
2. `grep -rln <symbol> ... | head -20` 只看到前 20 行 → 漏掉第 21 个起的文件，据此断言"其余引用都是无害的名义引用"（实为还有第二处真实依赖）；
3. `git log -- <file>` 直接看最后一屏 → 那是**最新**提交，据此断言"缺陷由 9-12 的某次重构引入"（实为 8-26）。

**规则**：凡结论建立在"全部/没有/最早/最全"这类量词上，先**拿到总数**再下判断：
- 计数用 `grep -c` / `grep -l | wc -l`，**不要**对结论性证据用 `head`；
- 取最早/最晚提交用 `git log --format='%h %ad %s' --date=short <file> | tail -1`（或 `--reverse | head -1`），不要凭默认分页的最后一屏；
- 读取范围不确定时用完整读取或先 `wc -l` 确认行数。

**为什么危险**：截断的输出**看起来是完整的** —— 没有报错、没有提示，只是尾巴被切掉了。
人眼不会察觉，而基于它的判断是"确定语气"的，会写进文档与冻结记录。

**验证**：下结论前复述一次"我这个结论依赖的是全量还是片段"，并给出取总数的命令。

## 来源等级（source-tier-labeling）

### 候选规则审核

**场景**：种子域名清单（`src/config/const.py SOURCE_TIER_RULES`）成长——定期从历史引用中筛出高频未命中域名，人工审核后决定加入规则表或记入拒绝清单。本 change 无 DDL，不建任何表。
**步骤**：
1. 跑聚合 SQL（PostgreSQL；`sources` 为 TEXT，存的是 JSON 序列化后可能再转义一层的 JSON 字符串 —— 用 `#>> '{}'` 剥掉外层字符串再 `::jsonb`，直接对列做 `jsonb_array_elements` 会因它是标量字符串而得 0 行）：
   ```sql
   WITH parsed AS (
     SELECT m.id,
            jsonb_array_elements((m.sources::jsonb #>> '{}')::jsonb) AS elem
     FROM conversation_history m
     WHERE m.sources IS NOT NULL AND m.sources <> ''
   )
   SELECT
     CASE
       WHEN elem->>'source' LIKE 'http%://%'
         THEN split_part(regexp_replace(elem->>'source', '^.*?://', ''), '/', 1)
       ELSE split_part(elem->>'source', '/', 1)
     END AS domain_raw,
     COUNT(*)           AS cite_count,
     COUNT(DISTINCT id) AS msg_count
   FROM parsed
   GROUP BY domain_raw
   HAVING COUNT(*) >= 5
   ORDER BY cite_count DESC;
   ```
   说明：`#>> '{}'` + `::jsonb` 对单层与双层转义都成立；KB 来源（如 `tencent_2024_annual.pdf`）与带 scheme 的 web URL 都能提取；存量字符串数组脏数据解出 NULL，人工审核时排除；未命中过滤不在 SQL 侧做（不复刻 Python 解析逻辑），人工对照规则表排除。
2. 人工对照 `SOURCE_TIER_RULES` 与下方拒绝清单，排除已命中/已拒绝域名，筛出候选（阈值建议：≥5 次且跨 ≥3 会话，即 `cite_count >= 5 AND msg_count >= 3`）。
3. 打开样本消息核对引用上下文，确认该域名内容性质（官方/媒体/UGC）。
4. 批准 → `SOURCE_TIER_RULES` 加行 → `docker compose restart app` 生效 → 涉及标签文案时同步 api_contract.md（动线：const.py → api_contract.md → chat.html）。
5. 拒绝 → 在下方拒绝清单追加一行 `域名 | 拒绝日期 | 理由`（negative cache，不建数据库表）。

拒绝清单（negative cache）：

| 域名 | 拒绝日期 | 理由 |
|------|---------|------|
| （暂无） | | |

**验证**：加入规则表并重启后，用该域名来源重新提问，引用抽屉徽标显示预期档位；或 `POSTGRES_HOST=localhost pytest tests/ -k tier -v` 通过。
**注意事项**：SHALL NOT 使用 LLM 判定 tier 或自动升级档位（spec 硬约束）；SQL 阈值只是初筛，定档唯一权威是 `SOURCE_TIER_RULES`。

### RAGAS 检索质量评估基线分界（2026-09-10）

**场景**：source-tier-labeling 上线当天起，to_prompt_text 会在喂给模型的 context 块文本中以括注追加档位标签（KB `(第1页, 内部文档)`、web 如 `(权威媒体)`），NLI 评估读到的上下文因此与历史不同。
**步骤**：
1. 以本条目登记日期为分界：**此后**的 faithfulness 等依赖上下文的评估结果与**此前**的历史分数不可直接对比。
2. 需要跨基线对比时，重新生成测试集或用同一批问题在两侧各跑一次评估。
**验证**：—（记录性条目）
**注意事项**：对应 design 文档 Risks 项；label 只增上下文长度不改变事实内容，预期影响幅度小但不可忽略。

## 检索（retrieval）

### 词法检索的分词器变更（jieba 升级 / 词典调整）

**何时用**：升级 `jieba` 版本、调整 `tokenizer.py` 的分词配置或过滤规则之后。

**步骤**：
1. 改 `pyproject.toml`（pin 到新版本）并重建镜像：`docker compose build --no-cache app`；
2. 宿主侧跑检查：`POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check`
   —— 退出码 1 且打印 `stale=N` 表示存量已过期；
3. 重写：`POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply`；
4. 复验：`--check` 退出码 0；再跑一次 `--apply` 应为 `rewritten=0`（幂等）；
5. 重启应用：`docker compose restart app`。

**为什么不能省**：`content_seg` 是 `tsv` 生成列的输入，落库即固化；不重写会静默降召回，
没有任何日志或异常提示。

## 数据（data）

### 退役 Chroma 之后如何重建 dev 语料

**场景**：dev 的 PostgreSQL 语料被清空或损坏（复位、误删），需要重建。
**步骤**：
1. 先读计数基线（`knowledge_base | document | chunks | users`，应为 `5|0|176|1`）：
   `docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(*) FROM users);"`
2. 重建来源**只有一个** —— MinIO 里的原始上传文件：
   - 若 `document` 记录还在（只清了 chunks）：`docker compose exec app python -m scripts.rebuild_kb_data.py --all`，按记录的 `file_path` 从 MinIO 重跑入库；
   - 若 `document` / KB 记录也丢了：从 MinIO 取原始文件，经 `POST /api/kbs/documents/upload` 重新上传。
**验证**：`chunks` 计数恢复；`POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check` 退出码 0（`stale=0`）。
**注意事项**：
- **搬迁脚本已退役**：`scripts/migrate_chroma_to_pg.py` / `scripts/dense_equivalence_check.py` 已随 P4 删除，**不能再从 Chroma 语料重建**，别指望跑旧脚本。
- **原始文件是唯一不可再生的源头**：MinIO 中的原始上传文件若丢失，语料不可恢复。
- `data/chroma_persist` / `data/chroma` / `data/bm25_index` 已随 P4 Task 11 从磁盘删除；P4 更早已（Task 6）删除其读取路径，**回滚到 Chroma 不再可能、也不是可用的重建路径** —— 语料重建只能从 MinIO 的原始文件重新上传。

## 并行会话（worktree）

> **何时该建由流程规定，本文只讲怎么建与有哪些坑** —— 判据（主工作区是否已被占用 / 本 change 是否多次提交 / 是否跨会话）见 `dev-flow.md`「变更开工前置：先问『要不要建 worktree』」。
>
> 顺带一条教训：本文原先只把触发写成下面的**场景**（"多个会话同时改仓库"）—— 那是**状态**，需要人主动判断才能察觉，所以长期没起过提示作用。**提示要挂在"动作"上（如"新开 change"），不能挂在"状态"上。**

### 用 worktree 隔离并行任务

**场景**：同一台机器上多个会话/任务同时改这个仓库。共用一个工作区会互踩 —— 本仓 pre-commit 含 doc 闸门（单次提交实测约 2 分钟），这个长窗口里另一会话若提交，会撞 `fatal: cannot lock ref 'HEAD'`；若改了工作区文件，钩子会报 `files were modified by this hook`（**文档校验本身是过的**，失败只因窗口内有并发写入）。

**步骤**：
1. 建 worktree —— **必须配新分支**（`dev-wsl` 已被主工作区签出，git 拒绝同一分支签出两处）：
   ```bash
   git worktree add -b feat/<change 名> /mnt/d/code/demo/AIAgent/corporate_rag-<名字> dev-wsl
   ```
   目录由 git 创建，**不能预先存在**。分支名用 **`feat/<change 名>`** —— 与历史特性分支 `feat/langfuse-v2`、`feat/intent-routing-upgrade` 一致（`dev-wsl` / `dev-adv-rag` / `dev-wsl-chroma` 是长期环境分支，不属这一类）。
2. **必须**把 gitignore 的运行时文件带过去 —— **两个都要**，缺任一则该 worktree 跑不起来：
   ```bash
   W=/mnt/d/code/demo/AIAgent/corporate_rag-<名字>
   ln -s /mnt/d/code/demo/AIAgent/corporate_rag/.env  "$W/.env"
   ln -s /mnt/d/code/demo/AIAgent/corporate_rag/.venv "$W/.venv"
   ```
   - `.env`：不带则 compose 的 `${POSTGRES_PASSWORD:?}` 直接报错。
   - **`.venv`：不带则该 worktree 里 `git commit` 全部失败** —— pre-commit 的本地钩子 entry 是 `.venv/bin/python -m src.cli.check_docs`（相对路径、按 worktree 解析），没有 `.venv` 就找不到解释器；也跑不了 `pytest` / `ruff` / `pyright`（等于只能在里面空编辑）。**此前本节漏了这一步**（2026-09-23 建隔离 worktree 时踩到，事后补记）。
   - ⚠️ **该 worktree 内禁用 `git add -A` / `git add .`**：`.venv` 是 symlink，而 `.gitignore` 里的 `.venv/`（带尾斜杠）只匹配**目录** ⇒ symlink 会以未跟踪文件 `?? .venv` 冒出来，被一把带进提交。一律用显式路径 `git add <文件...>`。同理见下方「注意事项」的 `/data/` 那条。
3. 在新目录里正常编辑、提交。

**验证**：`git worktree list` 列出两个工作区；新目录内 `git branch --show-current` 是新分支。

**注意事项**：
- **worktree 只隔离 git**：HEAD / index / 工作区文件 / pre-commit 扫描范围各一份。**不隔离**的是 ——
  - **`git stash`**（仓库级唯一一条 ref，两边序号互相挤动）→ **跨工作区禁用 stash**，要暂存就提交到自己的分支
  - 分支/tag 删除、`git push --force`（全仓库范围）
  - **Docker**：工程名、8 个 `container_name`、5 个命名卷、端口全部写死 → 两个工作区**无法各跑一套**容器与数据；从不同工作区 `up -d` 操作的是同一套容器
  - gitignore 的运行时产物：`.env`、`data/`、`logs/`、`.venv`、`.superpowers/` 都不会带过去
  - **未跟踪文件不共享**（只有被跟踪的才跨工作区可见）→ 先提交，再切过去用
- 换目录起服务会重建 `app` / `nginx`（渲染出的 bind source 不同 → 配置哈希不同），属预期
- 用完 `git worktree remove <路径>`；分支有未合并提交时需 `--force`
- 前提：override 的挂载须是相对路径（本仓 2026-09-21 已改），否则 worktree 里改代码静默失效（见 defensive-patterns.md「部署」）
- **`core.symlinks=false`（本仓 git 配置）下，被跟踪的 symlink 会被写成普通文件**：`openspec` 在 git 里是 symlink（mode `120000`），检出到该环境却成了内容为 `docs/openspec` 的**文本文件** → `openspec` CLI 报 `Unknown item '<name>'`。修法：`rm openspec && ln -s docs/openspec openspec`（git 仍判定未变）。**任何新建的 worktree / clone 都会中这一条**。
- **`.gitignore` 里带尾斜杠的条目忽略不了同名 symlink**：`/data/` 与 `.venv/` 都是这个形态，尾斜杠只匹配**目录**，而 symlink 不是目录 → 整体 symlink 过去会以**未跟踪文件**冒出来（`?? .venv`），有被 `git add .` 带进提交的风险。
  - `data`：改法是建**真目录** `data/`，只在里面 symlink 具体子目录（`data/ragas`）。
  - `.venv`：不能改成真目录（要的就是同一个 venv）。**做法是提交时坚持显式 pathspec、永不 `git add .`**；或把 `.gitignore` 的 `.venv/` 改为不带斜杠的 `.venv`（仓库级改动，需单独决定）；或**只在本机生效**地加进 common dir 的 `info/exclude`：`printf '.venv\n' >> "$(git rev-parse --git-path info/exclude)"`（该文件被所有 worktree 共用、不进任何提交；同时解掉 UA 自动更新因 `?? .venv` 而 `failed: Working tree has relevant uncommitted changes` 的拦截）。
- **venv 里有指向主工作区的 editable 安装**：`.venv` 内存在 `__editable__.corporate_rag-0.1.0.pth` → `<主工作区>/src`。于是「从 worktree 跑测试」是否真用到 worktree 的代码，**取决于 cwd** ——
  - 从 worktree **根目录**跑（`-c` / 脚本会把 cwd 放进 `sys.path[0]`）→ 解析到 **worktree 的 `src`** ✅
  - **cwd 一旦变化 → 静默回落主工作区的 `src`**，测试跑的是别人的代码而你不自知（无报错、结果看似正常）
  - 一行验证：`.venv/bin/python -c "import src; print(src.__file__)"`，应打印 **worktree** 路径
  - 要确定性时显式加 `PYTHONPATH=<worktree 根>`

### worktree 里本地跑服务（后端 + 前端反代）

**场景**：worktree 隔离了 git 但隔离不了 Docker（本仓只有一套容器）。要在 worktree 里看到效果，**不要在 worktree 里起 compose** —— 一律本地跑，按 slot 分配端口，多个 worktree 并存互不冲突。

**步骤**：

```bash
scripts/dev-worktree.sh up            # 默认 slot 1：后端 8001 / 前端 8080
scripts/dev-worktree.sh up --slot 2   # 另一个 worktree 换 slot：8002 / 8081
scripts/dev-worktree.sh status
scripts/dev-worktree.sh down
```

**机制**：

- 后端 = 宿主 `.venv` 起 `uvicorn --reload`（脚本内置 `POSTGRES_HOST=localhost`：宿主解析不了 `.env` 里的服务名 `postgres`）。
- 前端 = 一次性 nginx 容器（默认镜像 `corporate_rag-nginx:latest`，可用 `DEV_NGINX_IMAGE` 覆盖）：挂载静态文件，`/api/` 反代到宿主后端。conf 由 `deploy/nginx/nginx.dev.conf.template` 经镜像 entrypoint 的 envsubst 渲染，**只有一个变量 `BACKEND_PORT`**（容器内固定监听 80，宿主端口由 `-p` 决定，因此模板无需知道前端端口）。entrypoint 只替换「容器 env 里存在的」变量，`$http_upgrade` / `$host` 等 nginx 变量不受影响。
- 端口约定：slot N → 后端 `8000+N`、前端 `8079+N`。容器名 `rag-dev-nginx-<worktree 目录名>`，按 worktree 天然唯一。
- 状态落 `.dev-ports` / `.dev-uvicorn.pid`（已 gitignore），日志 `logs/dev-uvicorn.log`；`--force` 可跳过端口占用检查。

**验证**：两个 worktree 分别 `--slot 1` / `--slot 2`，`8001/8080` 与 `8002/8081` 同时 `HTTP 200`；各自 `down` 后端口释放、无残留容器与状态文件。

**注意事项**：

- **首次冷启动慢**：`/mnt/d`（9p）上 import 全量依赖约需 1 分钟；脚本等健康检查（上限 180s）才返回，未就绪时反代返回 502（看 `logs/dev-uvicorn.log`）。
- **`down` 要真杀掉后端**：`--reload` 是 supervisor + 子进程，且 `/mnt/d` 上进程可能停在 `D` 状态、SIGTERM 打不进 → 脚本按「谁在监听该端口」兜底结束（`kill_port`）。手工排查同理：**别用 `pkill -f "<模式>"`**，模式串会命中执行它的 shell 自身。
- **前端不要只起静态服务器**（如 `python3 -m http.server`）：前端用相对路径 `fetch('/api/...')`，无反代时 `/api/*` 全部 404。纯样式预览另见 README「纯前端预览」。
- **依赖服务仍是共享的那一套**（postgres / redis / minio / langfuse-web）：数据跨 worktree 共享，别做破坏性操作。
- 该脚本与模板随分支走：**旧分支 / 未合并 `dev-wsl` 的 worktree 里不存在**，需先合并。

## 可观测（Langfuse）

### 本地打开 Langfuse UI（地址 / 账号 / 受限范围）

**场景**：需要看 Langfuse 界面时 —— 确认播种出的 project 与 API Key、验证后端是否正常、将来查 trace。

**步骤**：
1. 确认容器在跑：`docker compose ps langfuse-web`。dev 下它由 `.env` 的 `COMPOSE_PROFILES=langfuse` 默认启用（profile 门保留，置空该变量即关闭）。
2. 浏览器打开 **`http://localhost:3000`**，用 `.env` 的 `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` 登录（本机当前值为 `admin@corprag.local` / `admin123456`）。

**验证**：`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/` 返回 `200`。

**注意事项**：
- **只绑回环**：端口映射是 `127.0.0.1:3000->3000`，所以用非回环地址连不上是**预期**（`curl http://<LAN-IP>:3000/` → `000`）——这是 ADR-0011「暴露面收敛」的决定，不是故障。**不要**为了远程访问去改端口绑定；从 Windows 浏览器访问优先靠 WSL2 的 localhost 转发，不通则用 `ssh -L 3000:127.0.0.1:3000 <host>`。
- 对照：前端 Nginx 绑的是 `0.0.0.0:80`（局域网可达），两者暴露面不同是有意为之。
- **UI 里看不到数据怎么排查**：本机 `.env` 的 `LANGFUSE_ENABLE=false`（`settings.py` 内置默认才是 `true`，被 `.env` 覆盖为关），故**当前看不到数据属预期**。排查顺序：① 把 `.env` 的 `LANGFUSE_ENABLE` 置 `true` 并重创 `app`（环境变量变更须 `docker compose up -d --force-recreate app`，`restart` 不生效）；② 发一轮真实对话；③ 按响应头 `X-Trace-ID` 在 Langfuse 检索该 trace。
- 账号由 `.env` 的 `LANGFUSE_INIT_USER_*` 在**首次启动时播种**，故无需手工注册。**`LANGFUSE_INIT_PROJECT_ID` 是播种开关**：缺它则 project 与 key 都不建，而且**不报错**（静默失效）。库重建后正是靠它 + `_PUBLIC_KEY`/`_SECRET_KEY` 保住原有那对 key，因此这三个键必须与 `.env` 的 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` 是**同一对**。
- 库重建后若 UI 里 project 名称是播种值（`Corporate RAG`）而非你后来改的名字，说明重建生效了，属预期。

## 分区命名

按操作主题分区，例如：`## 评估`、`## 分块`、`## 部署`。新主题首次出现时新建分区。

## 登记

新增 cookbook 条目**不需要**登记进 CLAUDE.md 文档组织表（表保鲜规则适用于**归属文档**，cookbook.md 内部条目由本文协议管理）。
