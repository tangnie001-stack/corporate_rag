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

## 代码目录结构（修改代码前必读）

```
src/
├── api/          # 纯路由层：请求校验→调 service→返回（不写业务逻辑）
├── services/     # 业务编排 app_service → kb / document / chat
├── rag/          # RAG 流水线 chain → retrieval → rerank → prompt → stream
├── chat/         # 对话管理 manager(Redis) + persistence(MySQL)
├── core/         # Loguru 日志
├── config/       # settings / response_codes / prompts / queries
├── parsers/      # pdf / docx / txt 解析
├── middleware/    # auth / trace_id / 统一响应包装
├── infra/        # db / chunking / llm / search / auth / errors / redis
├── cli/          # RAGAS 评估 / 检索调试
├── eval/         # 分块质量评估
├── models.py     # LLM/Embedding/Rerank 工厂
└── main.py       # FastAPI 入口 + 异常处理器

tests/            # 与 src/ 模块一一对应
```

### 层间调用规则
- ❌ `api/` 不得直接调用 `infra/` 或 `config/`（必须通过 `services/`）
- ❌ `api/chat.py` 不包含 SSE 格式化函数（在 `api/sse_utils.py`）
- ✅ `services/` 可调用 `infra/`、`rag/`、`chat/`

### 文件大小红线
- 单文件超过 400 行 → 必须拆分为模块包
- 单函数超过 80 行 → 必须拆分子函数

## 数据流
链路详解参考 docs/agents/data-flow.md（排查问题/理解系统流程时查阅）。

## 依赖图
查询代码关系时参考 docs/agents/codegraph-guide.md（比逐文件 grep 高效）。

## 常用命令
```bash
uvicorn src.main:app --reload          # 启动（热重载）
pytest tests/ -v                       # 测试
ruff format . && ruff check . --fix    # 格式化 + lint 修复
docker compose up -d --build           # 部署
docker compose restart app             # 改 .py 后重启
docker compose up -d --force-recreate app  # 改环境配置后重创
docker compose build --no-cache app    # 改依赖后重建
```

## TraceID
trace_id 的格式 `trace_<uuid>`，生成优先级：请求头 `X-Trace-ID` → 查询参数 `trace_id` → 自动生成。所有响应头均返回 `X-Trace-ID`（含 401/500）。
容器日志内 `/data/logs/`，按天轮转，trace_id 在日志行第三个 `|` 分隔段：

## 验证
改完代码后自检以下清单：
1. `pytest tests/ -v` 全部通过
2. `ruff check .` 无错误
3. 无遗留 `print()`、TODO 或调试代码
4. 改前端时用 playwright-cli 验证交互
5. **代码位置检查**：新增/修改的代码放在正确的目录了吗？
6. **层次检查**：api/ 里是否只做了参数校验和路由转发，没有写业务逻辑？
7. **import 检查**：有没有违反层间调用规则的 import（如 api/ 里 import infra/）？

## 设计流程
改 UI 或新增组件时参考 docs/agents/ui-design-flow.md。

## 规则
- 架构规约（异常处理 / 响应包装 / 日志约定 / 排查规范）详见 docs/agents/rules.md
- API 路由 handler 必须标注请求体和返回类型（请求用 Pydantic BaseModel，返回也用 Pydantic BaseModel 描述 data 结构，SSE 标注 StreamingResponse）
- 改完代码并验证通过后，先 commit，再输出 `git diff HEAD~1` 供我 review
- API Key 和 Token 通过 `.env` 加载，日志中脱敏；连接串不记录到日志
- 测试 mock 外部依赖，不发起真实网络调用
- 需求池文档在docs/requirements_pool.md
- **接口契约**：API 参数、返回值、历史踩坑记录详见 docs/api_contract.md，修改公共方法签名时同步更新
- **代码风格**：不用三元表达式（`a if cond else b`），写完整的 if/else 结构，保持可读性


## 代码注释标准

所有函数必须写 docstring，详细标准见 docs/agents/rules.md 的"代码注释标准"章节。

## 经验总结
分块问题排查和修复记录详见 docs/agents/chunking-issues.md，遇到分块相关问题时优先查阅。

