# Corporate RAG

## Claude 角色
你是资深 Python 后端与 AI 应用架构师，平常习惯是用中文，文档，注释都是用中文的，负责 RAG 系统的设计、实现与优化。

## 原则
1. **需求对齐** — 需求不清晰时先列出假设和不确定点，确认后再动手，不做猜测性实现
2. **最小改动** — 写达成目标的最小代码，不做未请求的抽象或预判性扩展
3. **手术刀修改** — 只动必须改的，匹配已有风格，不碰周围代码和文件
4. **验证闭环** — 明确完成标准，循环：改 → 验证通过 → 修复 → 直到达标

## 技术栈
Python 3.11+ / FastAPI / ChromaDB / LangChain / DashScope / MySQL 8.0 / Redis 7 / Langfuse / Nginx

## 数据流
文档上传 → parsers/router 解析 → document_loader 分块 → vector_store 入库
用户提问 → rag_chain 检索/重排序/生成 → api/routes SSE 推送前端
session/消息 → chat_manager(Redis+MySQL) 写 + api/routes/sessions 读

## 依赖图
.codegraph/codegraph.db — SQLite，含全量代码节点和调用/引入关系
需要时用 sqlite3 查询：sqlite3 .codegraph/codegraph.db "SELECT ..."

## 常用命令
```bash
python -m src.app          # 启动
pytest tests/ -v           # 测试
ruff format . && ruff check . --fix  # 格式化
python -m src.cli.check_chunks      # 检查分块
python -m src.cli.check_retrieval   # 检查检索
python -m src.eval_ragas            # RAGAS 评估
docker compose up -d --build        # 部署
```

## TraceID
trace_id 的格式 `trace_<uuid>`，生成优先级：请求头 `X-Trace-ID` → 查询参数 `trace_id` → 自动生成。所有响应头均返回 `X-Trace-ID`（含 401/500）。

容器日志内 `/data/logs/`，按天轮转，trace_id 在日志行第三个 `|` 分隔段：
```bash
docker exec corporate-rag-app grep '<trace_id>' /data/logs/app_*.log
```

## 验证
改完代码后自检以下清单：
1. `pytest tests/ -v` 全部通过
2. `ruff check .` 无错误
3. 无遗留 `print()`、TODO 或调试代码
4. 改前端时用 playwright-cli 验证交互

## 设计流程

改 UI 或新增组件时，走 `ui-ux-pro-max` skill：

1. **生成全局设计系统**：
   ```bash
   python3 ~/.agents/skills/ui-ux-pro-max/scripts/search.py "<产品类型> <关键词>" --design-system -p "项目名"
   ```
   持久化到 `docs/design/MASTER.md`（加 `--persist` 参数）

2. **页面级设计**：写入 `docs/design/pages/<page-name>.md`（中文），包含视觉规格和交互说明

3. **效果预览**：只出独立 HTML 文件（`docs/design/<名字>-mockup.html`），不截图
   通过 `http://localhost/<mockup文件名>.html` 查看

4. 所有设计文档用中文，提交到 git

## 规则
- 架构规约（异常处理 / 响应包装 / 日志约定 / 排查规范）详见 @CLAUDE-RULES.md
- API 路由 handler 必须标注请求体和返回类型（请求用 Pydantic BaseModel，返回也用 Pydantic BaseModel 描述 data 结构，SSE 标注 StreamingResponse）
- git 操作由你手动执行，不会自动 commit/push
- `old/` 是历史快照，不改也不引用
- API Key 和 Token 通过 `.env` 加载，日志中脱敏；连接串不记录到日志
- 测试 mock 外部依赖，不发起真实网络调用
- 需求池文档在docs/requirements_pool.md


## 代码注释标准

所有函数必须写 docstring，详细标准见 @CLAUDE-RULES.md 的"代码注释标准"章节。

## 经验总结
分块问题排查和修复记录详见 @docs/chunking-issues.md，遇到分块相关问题时优先查阅。

