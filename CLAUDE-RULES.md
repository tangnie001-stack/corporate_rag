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

- **降级型 / 拦截型** → `logger.warning`（不带 traceback，可控的预期行为）
- **透传型 / 兜底** → `logger.exception`（带完整 traceback，异常堆栈必须落盘）

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
  - 注释不描述代码语法（假设读者懂 Python），而是说明意图和背景
  - 注释一律用**中文**
