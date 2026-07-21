# 代码规范规则

## 导入规范

- 导入顺序分组：标准库 → 第三方库（`chromadb`、`pytest` 等）→ 本地模块（`src.*`），每组间空一行
- 禁止 `from module import *`
- 类型导入与运行时导入分开：`from typing import ...` 放最上方

## 注释要求
- 每个模块必须有模块级 docstring，说明职责和用途
- 每个类、方法、函数必须有 docstring，说明用途、参数和返回值
- 关键逻辑步骤必须有行内注释，说明"为什么这么做"

## 格式化
- 所有代码修改后必须运行 `ruff format` 格式化
- 行宽限制 100 字符
- 符合 ruff 所有规范

## 类型注解
- 所有函数/方法必须有完整的类型注解（参数 + 返回值）
- 使用 `typing` 模块的泛型类型（`list[str]`、`Optional[str]` 等）
- 类属性必须有类型注解

## 日志与异常
- 日志统一使用 loguru，禁止 print
- 禁止裸 `except: pass`，所有异常必须用 loguru 记录错误

## 架构约束
- 绝对不修改 `old/` 目录下的任何文件
- 所有模块必须用类实例化，禁止模块级全局初始化

## 测试规范
- 测试文件使用 `tests/` 目录，命名 `test_<module>.py`
- 测试函数命名：`test_<行为>_<场景>`（蛇形命名）
- fixture 在文件上方集中定义，指定 `scope`（默认 `function`）
- mock 外部依赖，不发起真实网络/数据库/Docker 调用
- 每个 `assert` 只验证一个逻辑断言

## 资源管理
- 打开文件、连接、游标等资源必须用 `with` 语句或 `try/finally` 确保释放
- 禁止在 `__init__` 中打开不会关闭的资源（连接池除外）
- 网络调用（LLM、Reranker、API）必须设超时参数

## Retry 模式
- 外部 API 调用失败时统一使用 `src.config` 中定义的 `RETRY_MAX_ATTEMPTS` / `RETRY_INITIAL_INTERVAL` / `RETRY_BACKOFF_FACTOR`
- 所有重试均失败后降级（返回兜底值或错误提示），不崩溃
