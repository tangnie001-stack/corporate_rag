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

## 分区命名

按操作主题分区，例如：`## 评估`、`## 分块`、`## 部署`。新主题首次出现时新建分区。

## 登记

新增 cookbook 条目**不需要**登记进 CLAUDE.md 文档组织表（表保鲜规则适用于**归属文档**，cookbook.md 内部条目由本文协议管理）。
