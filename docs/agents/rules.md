# 架构规约

## 异常处理三模式

区分三种异常处理模式，决定每个 `except` 块应该怎么处理：

| 模式 | 适用场景 | 做法 |
|------|---------|------|
| **降级型** | 非关键路径，有 fallback | 精确捕获异常 + `logger.warning` + 吞掉，继续业务流程 |
| **透传型** | 关键路径失败，业务不可继续 | 精确捕获 + `logger.exception`（带完整 traceback）+ `raise` |
| **拦截型** | 业务规则不满足 | 不 catch，直接 `raise BusinessError/AuthError`，交给全局 dispatch 处理 |

**注**：降级型也需要精确异常类型，避免使用裸 `except Exception`。

## 响应包装边界

业务层（route / service / infra）只 `raise` 异常，**不** `return JSONResponse`。
响应的统一包装（`{"code", "message", "data"}`）仅发生在 `ResponseEnvelopeMiddleware` 这一处。

## 异常层次

按**错误性质**（BusinessError / AuthError / SystemError）分类，不按业务模块细分。
模块归属信息由 `Code` 枚举的前缀（`DOC_*` / `FILE_*` / `KB_*`）承载。

```
AppError (基类)
├── BusinessError  — 业务规则冲突 (400)
├── AuthError      — 认证授权 (401/403)
├── ValidationError — 参数校验 (422)
├── SystemError    — 基础设施故障 (503)
└── AppError       — 未知异常兜底 (500)
```

## 日志约定

**格式准绳**：统一格式（行模板 / 前缀表 / 值引号与 JSON 转义 / 事件命名 / query 完整记录 / helper / 固定事件行）以 `CLAUDE.md`「日志格式（统一）」为准——新增或修改日志先对照该节。本节保留前缀表之外的深度语义。

### 级别语义

- `debug` — 诊断细节，默认关闭（检索中间态、token 流）
- `info` — 正常流程里程碑（请求进出、agent 迭代、检索/联网完成）
- `warning` — 降级/可恢复异常（fallback 生效、重试、超时）；**禁止用于"正常但少见"的执行分支**
- `error` — 单点失败已处理（不阻断）
- `exception` — 透传型异常（带完整 traceback + raise）

与异常三模式联动：**降级型 / 拦截型** → `logger.warning`；**透传型 / 兜底** → `logger.exception`。

### trace_id

trace_id 由 `src/core/logging.py` 的 patcher 自动注入日志行第三段，业务代码不手动写入 message。

### 检索行为信号日志

前缀 `retrieval_signal:`，信号类型：`reretrieve` / `to_web` / `abstain_after_retrieve` / `unsupported` / `cited` / `empty_result`。行格式与字段见 `CLAUDE.md`「日志格式（统一）」固定事件行，经 `src/core/logging.py` 的 `retrieval_signal()` helper 统一输出（query 完整记录 + JSON 转义），不在埋点处手拼。

### 检索重放上下文

每次 `retrieve_kb` 执行经 `log_retrieve_replay()` 落一条 `[retrieval] retrieve replay` 事件行（query 全文 / kb_id / iteration / top_k / dedup_max_per_doc / hybrid / rerank），是该 trace 离线重放（`replay_trace` CLI）的机器输入。

## API 路由类型标注

所有路由 handler 必须标注请求体和返回类型：

- **请求体**：用 Pydantic `BaseModel` 标注（利用 FastAPI 自动校验）
- **返回类型**：用 Pydantic `BaseModel` 标注，描述 `data` 字段的结构
  - 原始返回值直接描述业务数据结构，不包含 `code`/`message` 包装（由 `ResponseEnvelopeMiddleware` 统一包装）
  - SSE 流式接口标注 `StreamingResponse`
  - 文件上传等返回 `JSONResponse` 的标注 `JSONResponse`

```python
class LoginResponse(BaseModel):
    token: str
    user_id: str

@router.post("/auth/login")
async def login(body: LoginRequest) -> LoginResponse:
    ...
    return LoginResponse(token=token, user_id=user_id)
```

## 代码注释标准

### 文档字符串（docstring）

- **模块 docstring**：文件顶部，说明模块用途和核心导出
- **类 docstring**：说明类实例代表什么，`Attributes:` 节列出公开属性
- **函数 docstring**：`Args:` / `Returns:` / `Raises:` 三节
  - Args：每个参数一行，`名称: 描述` 格式
  - Returns：描述返回值的语义
  - Raises：列出接口相关的异常，`异常名: 描述`
  - 生成器函数用 `Yields:` 代替 `Returns:`
- **覆写方法**：若有 `@override` 且行为不变，无需 docstring；否则需要
- **基本原则**：
  - 公共 API / 非平凡函数 / 逻辑不明显的函数 **必须** 有 docstring
  - Pydantic model 的每个字段 **必须** 有行内注释（`#`），说明字段含义和约束
  - 注释不描述代码语法（假设读者懂 Python），而是说明意图和背景
  - 注释一律用**中文**

### 写作原则

- **写当前状态，不写变更历史**：注释和文档描述当前机制，不写"之前是 X，后来改成 Y"、不写 PR/提交位置。变更故事放进 git 提交或变更记录（docs/openspec/changes/）。
- **注释陈述契约，不写推理记录**：保留做什么、行为、失败、时序、归属、异常、后果；删除推理过程、测试讲解、评审分析与代码复述。本地契约可链接其理由。

## 问题排查规则

遇到同一问题修了两次以上仍未解决时，必须先到网上搜索相关资料（官方 Issue、社区讨论、技术博客）确认根因和方案，再用搜索结果佐证，不能靠猜或试。
