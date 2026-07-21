## Why

API 响应格式不统一。成功时直接返回裸数据，失败时 JSON 结构各异（`{"detail": "..."}`、`{"message": "..."}` 等混用），前端需要解析不同格式来判定成功/失败。缺少机器可读的错误码，前端错误处理脆弱。

## What Changes

- 所有 API 响应统一为 `{"code": "SUCCESS", "message": "操作成功", "data": ...}` 信封格式
- 错误响应统一为 `{"code": "ERROR_CODE", "message": "描述", "data": null}`
- 前端 `apiRequest()` 适配新格式，按 `body.code` 判断业务结果
- 前端各页面的 `checkAuth()` 适配新格式
- 直接 `fetch()` 的调用（分块预览、状态轮询）手动解信封
- 修复 `doc_count` 硬编码 0 的问题
- ChromaDB 持久化目录挂载 volume，防止容器重建丢失数据
- `src/infra/` 重构为子文件夹结构

## Capabilities

### New Capabilities
- `unified-api-response`: 标准化 API 响应格式，中间件自动包裹，前端统一解析

### Modified Capabilities
- `user-auth`: 响应格式改为新的信封格式
- `file-storage`: 修复文件上传后的轮询未解信封的问题

## Impact

- 后端：新增 `ResponseEnvelopeMiddleware` + `ApiError` 异常 + `Code` 常量类
- 前端：`apiRequest()`、`checkAuth()`、`renderChunkPage()`、上传轮询
- 数据库：`doc_count` SQL query 改为 LEFT JOIN 计数
- 部署：`docker-compose.yml` 新增 chroma_persist volume 挂载
