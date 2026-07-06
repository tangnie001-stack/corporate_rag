# File Storage — 响应格式变更

**Delta from** `openspec/changes/phase4-architecture-overhaul/specs/file-storage/spec.md`

## Changes

1. `POST /api/kbs/{kb_id}/documents/upload` 响应改为信封格式 `{code, message, data: {doc_id, status, filename}}`
2. `GET /api/kbs/{kb_id}/documents/{doc_id}/status` 响应改为信封格式 `{code, message, data: {status, progress, ...}}`
3. `GET /api/kbs/{kb_id}/documents/{doc_id}/chunks` 响应改为信封格式 `{code, message, data: {items, total, page}}`
4. 上传轮询代码 `index.html:541` 手动解 `body.data`
5. 分块预览代码 `index.html:630` 手动解 `body.data`
