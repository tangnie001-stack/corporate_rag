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

### 新增一个分块策略

**场景**：需要为新的文档类型定制分块方式
**步骤**：
1. 在 `src/chunking/` 新增策略实现
2. 用 `validate_chunks` 校验输出合法
3. 用 ChunkQualityScorer 评估质量
**验证**：`pytest tests/chunking/ -v` 通过
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

**验证**：加入规则表并重启后，用该域名来源重新提问，引用抽屉徽标显示预期档位；或 `pytest tests/ -k tier -v` 通过。
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

## 分区命名

按操作主题分区，例如：`## 评估`、`## 分块`、`## 部署`。新主题首次出现时新建分区。

## 登记

新增 cookbook 条目**不需要**登记进 CLAUDE.md 文档组织表（表保鲜规则适用于**归属文档**，cookbook.md 内部条目由本文协议管理）。
