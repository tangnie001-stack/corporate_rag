# 统一 API 响应格式重构

## Why

当前 `response_processor_middleware` 在中间件层读取响应体（`body_iterator` → `json.loads`），
再重新包装为 `{"code": "SUCCESS", "data": ...}` 格式。存在以下问题：

1. **性能浪费**：每个请求多走一次 json.loads + json.dumps 周转
2. **异常路径 traceback 被截断**：BaseHTTPMiddleware 的 task_group 机制导致业务异常堆栈丢失
3. **中间件职责过重**：既做格式包装、又做日志、又兜底异常

## What Changes

参考 `fastapi-best-architecture` 的做法，用 Pydantic `ResponseModel(BaseModel)` 替代中间件包装：

- **新增** `src/api/schema.py`：定义 `ResponseModel(BaseModel)` 统一模型
- **简化** `response_processor_middleware`：去掉 body 读取 + JSONResponse 包装，直接透传
- **改造 handler**：每个 API handler 改为 `return ResponseModel(data=...)`
- **保留** `@app.exception_handler(Exception)` 返回统一格式

## 数据流

```
改前：
  Handler → 返回 dict/list/Pydantic
    → FastAPI 序列化 JSON
      → 中间件读 body → json.loads → 包装 → JSONResponse

改后：
  Handler → return ResponseModel(data=...)
    → FastAPI 看到 response_model=ResponseModel
      → Pydantic 自动递归序列化（含嵌套 BaseModel）
        → 中间件直接透传，不读 body
```

## 改动清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/api/schema.py` | 新增 | `ResponseModel(BaseModel)` 统一模型 |
| `src/middleware/response_processor.py` | 修改 | 去掉 body 读取和 JSONResponse 包装 |
| `src/api/knowledge_base.py` | 修改 | 3 处 handler 包 ResponseModel |
| `src/api/documents.py` | 修改 | 5 处 handler |
| `src/api/sessions.py` | 修改 | 3 处 handler |
| `src/api/auth.py` | 修改 | 4 处 handler |
| `src/api/health.py` | 修改 | 2 处 handler |
| `src/api/kb_eval.py` | 修改 | 改用 ResponseModel |
| `src/api/ragas_generate.py` | 修改 | 改用 ResponseModel |
| `src/api/llm_test.py` | 修改 | 改用 ResponseModel |
| `src/main.py` | 可选 | `@app.exception_handler` 统一格式 |

约 20 处 handler 逐个修改。
