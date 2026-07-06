# 页面集成测试规范

> 针对 Corporate RAG MVP（Gradio 5.x UI）的页面集成测试方案。
> 使用 `gradio_client` 模拟用户操作，验证 UI ↔ 后端交互正确性。

## 1. 概述

### 测试目的

验证 Gradio 前端组件与后端业务逻辑之间的衔接正确性，确保每个用户操作（创建/选择/删除知识库、上传文档、对话问答）在前端得到正确的组件状态反馈，同时后端数据（MySQL、ChromaDB）一致变更。

### 测试范围

三大模块：

| 模块 | 覆盖场景数 | 核心验证点 |
|------|-----------|-----------|
| 知识库管理 | 8 个 | Dropdown 状态、状态消息、MySQL/Chroma 数据 |
| 文档上传 | 8 个 | 处理状态、chunk_count、文件列表表格 |
| 对话问答 | 3 个 | 流式输出、引用显示、异常提示 |

### 测试策略

- **组件 + 数据库双重验证**：每次操作后既断言 Gradio 组件返回值，也查 MySQL/ChromaDB 确认数据一致性
- **隔离的数据**：每个测试用例使用带 `__test__` 前缀的知识库，teardown 时清理，不污染用户数据
- **支持热更新**：本地开发时 Gradio 以 `--watch-dirs src` 启动，修改代码后自动重载，测试秒级 rerun

### 优先级

知识库管理 → 文档上传 → 对话问答（模块间有依赖关系）

## 2. 测试环境配置

### 依赖服务

| 服务 | 用途 | 默认连接 |
|------|------|---------|
| MySQL 8.0 | 知识库/文档元数据 | `127.0.0.1:3306` |
| Redis 7 | 对话缓存 | `127.0.0.1:6379` |
| ChromaDB | 向量数据 | 本地持久化文件 |
| Gradio App | 被测 UI | `http://127.0.0.1:7861` |

### 启动方式

**方式一：本地手动启动（推荐用于开发调试）**

```bash
# 终端 1：启动 Gradio 应用（开启 watch 实现热更新）
PYTHONPATH=. gradio src/app.py --watch-dirs src

# 终端 2：运行测试
pytest tests/test_kb_page.py tests/test_upload_page.py tests/test_chat_page.py -v
```

**方式二：自动启动（用于 CI / 一键执行）**

```bash
pytest tests/test_kb_page.py tests/test_upload_page.py tests/test_chat_page.py -v --start-app
```

通过 `conftest.py` 中的 fixture 自动 subprocess 启动应用，测试完成后自动关闭。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GRADIO_URL` | `http://127.0.0.1:7861` | Gradio 应用地址 |
| `TEST_KB_PREFIX` | `__test__` | 测试知识库名称前缀 |

## 3. 测试数据说明

测试文档位于 `test_docs/` 目录，清单如下：

| 文件 | 格式 | 大小 | 说明 |
|------|------|------|------|
| `sample.pdf` | PDF | ~1 MB | 年报 PDF（symlink） |
| `sample.txt` | TXT | 1.2 KB | 普通 UTF-8 文本 |
| `sample_gbk.txt` | TXT | 125 B | GBK 编码文本 |
| `sample.docx` | DOCX | 37 KB | Word 文档 |

## 4. 知识库管理 — 测试用例

### 4.1 创建知识库

| # | 场景 | 操作 | 预期组件状态 | 预期数据库状态 |
|---|------|------|-------------|---------------|
| TC01 | 正常创建新 KB | `handle_create_kb("__test__创建测试")` | status: `创建成功`；Dropdown 含新选项 | MySQL `knowledge_base` 表新增记录 |
| TC02 | 创建同名 KB | 再次调用 TC01 | status: `已存在`；Dropdown 不变 | MySQL 不新增记录 |
| TC03 | 空名称 | `handle_create_kb("")` | status: `请输入知识库名称` | 不写入数据库 |
| TC04 | 名称只含空格 | `handle_create_kb("   ")` | status: `请输入知识库名称` | 不写入数据库 |

### 4.2 选择知识库

| # | 场景 | 操作 | 预期组件状态 | 预期数据库状态 |
|---|------|------|-------------|---------------|
| TC05 | 选择存在的 KB | 设 Dropdown 值为已有 KB ID | doc_table 显示文档列表；status: `已选择知识库: xxx` | 无变更 |
| TC06 | 空选择 | 设 Dropdown 值为空 | doc_table 清空；status: `欢迎使用` | 无变更 |

### 4.3 删除知识库

| # | 场景 | 操作 | 预期组件状态 | 预期数据库状态 |
|---|------|------|-------------|---------------|
| TC07 | 删除存在 KB | `handle_delete_kb(kb_id)` | status 含 `✅`；Dropdown 清空；doc_table 清空 | MySQL 记录删除；ChromaDB collection 删除 |
| TC08 | 空选删除 | `handle_delete_kb("")` | status: `请先选择一个知识库` | 无变更 |
| TC09 | 重复删除 | 先成功删除，再用同一 ID 删 | status 含 `⚠️` 不存在提示 | 无变更 |

## 5. 文档上传 — 测试用例

### 5.1 正常上传

| # | 场景 | 操作 | 预期组件状态 | 预期数据库状态 |
|---|------|------|-------------|---------------|
| TC10 | 上传 PDF | 上传 `sample.pdf` | status: `✅ ...处理完成`；doc_table 显示 status `✅ ready`，chunk_count > 0 | MySQL `document` status=ready, chunk_count>0 |
| TC11 | 上传 DOCX | 上传 `sample.docx` | 同上 | 同上 |
| TC12 | 上传 TXT | 上传 `sample.txt` | 同上 | 同上 |
| TC13 | 上传 GBK TXT | 上传 `sample_gbk.txt` | 同上（验证 GBK 编码兼容） | 同上 |
| TC14 | 多文件混合上传 | 同时上传 PDF + DOCX + TXT | status 每行显示处理结果；表格 3 行记录 | 3 条 document 记录均为 ready |

### 5.2 异常场景

| # | 场景 | 操作 | 预期组件状态 | 预期数据库状态 |
|---|------|------|-------------|---------------|
| TC15 | 未选 KB 上传 | Dropdown 为空时上传 | status: `请先选择知识库` | 无变更 |
| TC16 | 上传损坏文件 | 上传一个损坏的测试文件 | status 含 `❌`；doc status=failed | MySQL `document` status=failed |

## 6. 对话问答 — 测试用例

### 6.1 正常问答

| # | 场景 | 操作 | 预期组件状态 |
|---|------|------|-------------|
| TC17 | 正常提问（已选 KB + 有文档） | `handle_chat("xxxx年报中的主要财务数据有哪些？")` | chatbot 有回答，引用显示非空 |

### 6.2 异常场景

| # | 场景 | 操作 | 预期组件状态 |
|---|------|------|-------------|
| TC18 | 空问题 | `handle_chat("", ...)` | 只返回原 history，不调用 LLM |
| TC19 | 未选 KB 发送消息 | `kb_name=""` 时调用 | chatbot 提示 `请先选择一个知识库` |

## 7. 运行方式

### 本地开发（热更新模式）

```bash
# 终端 1：启动应用（带文件监控）
PYTHONPATH=. gradio src/app.py --watch-dirs src

# 终端 2：运行测试
pytest tests/test_kb_page.py tests/test_upload_page.py tests/test_chat_page.py -v

# 单模块运行
pytest tests/test_kb_page.py -v
pytest tests/test_upload_page.py -v
pytest tests/test_chat_page.py -v

# 带覆盖率
pytest tests/test_kb_page.py tests/test_upload_page.py tests/test_chat_page.py --cov=src -v
```

### CI 模式（自动启动应用）

```bash
pytest tests/test_kb_page.py tests/test_upload_page.py tests/test_chat_page.py -v --start-app
```

## 8. 附录：测试代码文件结构

```
tests/
├── conftest.py              # 共享 fixture：
│                            #   - gradio_client 实例
│                            #   - 测试 KB 创建/销毁
│                            #   - pytest --start-app 支持
├── test_kb_page.py          # 知识库管理集成测试（8 个用例）
├── test_upload_page.py      # 文档上传集成测试（7 个用例）
└── test_chat_page.py        # 对话问答集成测试（3 个用例）
docs/
└── page_integration_test_spec.md   # 本规范文档
```
