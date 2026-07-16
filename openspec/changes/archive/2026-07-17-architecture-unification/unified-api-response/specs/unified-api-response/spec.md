# Unified API Response

## Requirements

1. 所有 HTTP API 响应使用统一信封格式：
    ```json
    {"code": "SUCCESS", "message": "操作成功", "data": <payload>}
    ```
2. 错误响应：
    ```json
    {"code": "ERROR_CODE", "message": "人类可读描述", "data": null}
    ```
3. 机器可读错误码定义在 `Code` 类中，每次新增错误码时必须同时添加 `_MSG`。

## Endpoints Affected

| 端点 | 改前格式 | 改后格式 |
|---|---|---|
| `GET /api/kbs` | `[{id, name, doc_count}]` | `{code, message, data: [...]}` |
| `POST /api/kbs` | `{id, created}` | 同上 |
| `DELETE /api/kbs/{id}` | `{success, message}` 或 `{detail}` | 同上 |
| `GET /api/kbs/{id}/documents` | `[{...}]` | 同上 |
| `POST /api/kbs/{id}/documents/upload` | `{doc_id, status, filename}` | 同上 |
| `GET /api/kbs/{id}/documents/{id}/status` | `{status, progress, ...}` | 同上 |
| `GET /api/kbs/{id}/documents/{id}/chunks` | `{items, total, page}` | 同上 |
| `GET /api/sessions` | `[{...}]` | 同上 |
| `GET /api/sessions/{id}/messages` | `[{...}]` | 同上 |
| `DELETE /api/sessions/{id}` | `{success}` 或 `{detail}` | 同上 |
| `POST /api/auth/login` | `{token, user_id}` | 同上 |
| `GET /api/auth/verify` | `{valid, user_id}` | 同上 |
| `POST /api/auth/logout` | `{message}` | 同上 |
| `GET /api/health` | `{status: "ok"}` | 不改 |
| `GET /api/chat/stream` | SSE 事件 | 不改 |

## Error Codes

参考 `src/config/response_codes.py`：
- `SUCCESS`、`INTERNAL_ERROR`、`NOT_FOUND`、`VALIDATION_ERROR`、`UNKNOWN_ERROR`
- `AUTH_REQUIRED`、`AUTH_TOKEN_EXPIRED`、`AUTH_WRONG_PASSWORD`、`AUTH_ACCOUNT_EXISTS`
- `KB_NOT_FOUND`、`KB_DELETE_FAILED`
- `FILE_TOO_LARGE`、`FILE_TYPE_UNSUPPORTED`、`FILE_UPLOAD_FAILED`、`FILE_DUPLICATE`
- `DOC_PROCESSING_FAILED`
- `SESSION_NOT_FOUND`

## Frontend

1. `apiRequest()` 按 `body.code` 判断结果：
   - `AUTH_REQUIRED` / `AUTH_TOKEN_EXPIRED` → 跳登录页
   - `SUCCESS` → 返回 `body.data`
   - 其他 → throw Error
2. `checkAuth()` 读取 `d.code === 'SUCCESS' && d.data?.valid`
3. 直接 `fetch()` 的调用（分块预览、状态轮询）手动解 `body.data`
4. 页面级 auth 处理：
   - ��识库页 `loadKBs()` catch `AUTH_REQUIRED` → ���登录
   - 聊天页 `loadKbSelector()` catch `AUTH_REQUIRED` → 显示"加载失败"

## doc_count

SQL query 从 `SELECT id, user_id, name` 改为：
```sql
SELECT k.id, k.user_id, k.name, COUNT(d.id) AS doc_count
FROM knowledge_base k LEFT JOIN document d ON d.kb_id = k.id
WHERE k.user_id = %s GROUP BY k.id ORDER BY k.created_at DESC
```
