# document-upload-consolidation Specification

## Purpose
TBD - created by archiving change phase-4-service-sinking. Update Purpose after archive.

## Requirements

### Requirement: DocumentService 提供 store_and_process 方法

系统 SHALL 在 `DocumentService` 上提供 `store_and_process(kb_id, file, user_id) -> dict` 方法，封装文件上传后到后台处理的全流程。

#### Scenario: 上传成功
- **WHEN** 调用 `store_and_process()` 传入有效的 kb_id、文件对象和 user_id
- **THEN** 方法依次执行：1) 调用 `FileStore` 上传文件到 MinIO；2) 调用 `db.add_document()` 写入元信息；3) 调用 `asyncio.create_task()` 启动后台 `process_document()` 任务；4) 返回 `{"doc_id": str, "filename": str}`

#### Scenario: 文件类型不支持
- **WHEN** 传入不支持的文件类型
- **THEN** `store_and_process()` 抛出 `ValidationError`，错误码指示文件类型不受支持

#### Scenario: 文件大小超限
- **WHEN** 传入超过 `MAX_FILE_SIZE` 的文件
- **THEN** `store_and_process()` 抛出 `ValidationError`，错误码指示文件过大

### Requirement: API 层只保留路由和校验

`api/documents.py` 的 upload handler SHALL 只做参数校验和路由转发，不包含业务逻辑。

#### Scenario: 上传路由
- **WHEN** POST /api/documents/upload 收到请求
- **THEN** handler 校验请求体后调用 `svc.document.store_and_process()`，返回 `success()` 包装的响应
